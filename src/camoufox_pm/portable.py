"""Portable mode: one explicit ``Data`` folder beside the program.

The portable edition runs from a folder with no Python, no Node and no
installer. Everything it writes — the SQLite database, profile directories,
configuration and logs — lives under a single ``Data`` directory next to the
executable, so the folder can be moved, backed up or replaced as one unit.

Three rules shape this module:

* **Fail loudly, never relocate.** A ``Data`` folder that cannot be created or
  written is an error the user sees, not a reason to fall back to some other
  location. Silent relocation is how data gets lost: the old copy keeps working
  somewhere nobody looks, and the "fresh" app starts empty.
* **Secrets have to survive a restart.** Proxy credentials are encrypted with a
  Fernet key; without a key they are stored in plaintext and unreadable after a
  restart. Portable mode generates the key once and keeps it in
  ``Data/config.env``, which is also the documented place to edit configuration.
* **One instance, redacted logs.** A second double-click must not start a second
  server against the same database, and the logs under ``Data/logs`` must not
  carry passwords, tokens or API keys.

Portable mode is on when the program is frozen (the packaged app), when
``--portable`` is passed, or when ``CPM_DATA_DIR`` names the data folder. Plain
source runs keep their current behaviour.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import MutableMapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

from cryptography.fernet import Fernet
from loguru import logger

CONFIG_NAME = "config.env"
PATHS_NAME = "paths.env"
LOCK_NAME = "instance.lock"
SECRET_KEY_NAME = "CPM_SECRET_KEY"
PATH_KEYS = ("CPM_DATA_DIR", "CPM_BROWSER_DIR", "CPM_TEMP_DIR")

# Space to refuse a start on, before anything is written. A profile manager that
# runs out of room mid-write is how databases get torn; failing early and
# clearly is the cheaper failure.
MIN_DATA_SPACE = 200 * 1024 * 1024
MIN_TEMP_SPACE = 100 * 1024 * 1024


class PortableError(RuntimeError):
    """A portable-mode failure whose message is written for the user."""


class DataDirNotWritable(PortableError):
    """The ``Data`` folder is missing, unusable or read-only.

    Carries the path so the message can name it; nothing is written anywhere
    else when this is raised.
    """

    def __init__(self, path: Path, detail: str, what: str = "data folder"):
        self.path = Path(path)
        super().__init__(
            f"The {what} is not usable: {self.path} ({detail}). "
            "Nothing was moved or deleted. Free up the folder, or point "
            "CPM_DATA_DIR at a writable folder, and start again."
        )


class InstanceAlreadyRunning(PortableError):
    """Another instance of this program is already serving this data folder."""

    def __init__(self, holder: dict[str, Any]):
        self.holder = holder
        super().__init__(
            f"Fingerprint Lite is already running (PID {holder.get('pid')}, "
            f"port {holder.get('port')}). Close that window first, or start "
            "the portable copy from its own folder."
        )


class NotEnoughSpace(PortableError):
    """The chosen disk cannot hold what would be written to it."""


@dataclass(frozen=True)
class PortableContext:
    """Where portable mode put everything."""

    program_dir: Path
    data_dir: Path
    profiles_dir: Path
    logs_dir: Path
    config_path: Path
    browser_dir: Path
    temp_dir: Path


# ---------------------------------------------------------------------------
# Activation and location
# ---------------------------------------------------------------------------


def is_frozen() -> bool:
    """True inside a PyInstaller bundle."""
    return bool(getattr(sys, "frozen", False))


_ACTIVE = False
_windowed = False
_notified = False


def is_active() -> bool:
    """Whether this process runs in portable mode (see :func:`bootstrap`)."""
    return _ACTIVE


def set_windowed(value: bool = True) -> None:
    """Mark this process as the windowless desktop entry.

    With no console, failures cannot be written anywhere the user will see
    them, so :func:`notify_fatal` turns into a native dialog.
    """
    global _windowed
    _windowed = value


def is_windowed() -> bool:
    """Whether this process is the windowless desktop entry (FingerprintLite.exe)."""
    return _windowed


def was_notified() -> bool:
    """Whether the user has already been told about a failure this run."""
    return _notified


def is_portable_request(
    argv: Sequence[str] | None = None,
    environ: MutableMapping[str, str] | None = None,
    frozen: bool | None = None,
) -> bool:
    """Whether this start should use the portable ``Data`` folder."""
    args = list(sys.argv if argv is None else argv)
    env = os.environ if environ is None else environ
    if frozen if frozen is not None else is_frozen():
        return True
    return "--portable" in args or "CPM_DATA_DIR" in env


def resolve_program_exe(program_dir: Path | None = None) -> Path | None:
    """The packaged ``FingerprintLite.exe`` beside ``program_dir``, if present.

    The relaunch after a completed first-start needs a real executable to
    start; a source run or a non-packaged layout has none and returns
    ``None`` so the caller continues in-process instead of spawning a shell.
    """
    root = Path.cwd() if program_dir is None else Path(program_dir)
    candidate = root / "FingerprintLite.exe"
    if candidate.is_file():
        return candidate
    return None


def resolve_program_dir(frozen: bool | None = None, executable: str | None = None) -> Path:
    """The folder the program runs from.

    Frozen: beside the executable — double-clicking must not depend on whatever
    the shortcut says the working directory is. From source: the working
    directory, which is what a developer running ``--portable`` expects.
    """
    exe = Path(sys.executable if executable is None else executable)
    if frozen if frozen is not None else is_frozen():
        return exe.parent
    return Path.cwd()


def resolve_data_dir(program_dir: Path, environ: MutableMapping[str, str] | None = None) -> Path:
    """``<program dir>/Data``, or the explicit ``CPM_DATA_DIR`` override."""
    return resolve_paths(program_dir, environ)[0]


def load_path_overrides(program_dir: Path) -> dict[str, str]:
    """Read ``paths.env`` beside the program — where paths are chosen.

    The file lives outside ``Data`` on purpose: it says where ``Data`` *is*.
    Editing it before the first start is how a portable install lands on
    another disk; no wizard involved.
    """
    return {
        key: value
        for key, value in _read_env_file(Path(program_dir) / PATHS_NAME).items()
        if key in PATH_KEYS
    }


def _rooted(program_dir: Path, value: str) -> Path:
    """A path from config, absolute or read from the program root."""
    path = Path(value).expanduser()
    return path if path.is_absolute() else program_dir / path


def resolve_paths(
    program_dir: Path, environ: MutableMapping[str, str] | None = None
) -> tuple[Path, Path, Path]:
    """Where Data, the browser and temporary files go.

    Precedence: the real environment, then ``paths.env``, then the defaults
    under the program root. Everything is decided here, before the backend
    starts — a path half-applied after startup is a split-brain install.
    """
    env = os.environ if environ is None else environ
    overrides = load_path_overrides(program_dir)

    def pick(key: str, default_name: str) -> Path:
        value = env.get(key) or overrides.get(key)
        return _rooted(program_dir, value) if value else program_dir / default_name

    return (
        pick("CPM_DATA_DIR", "Data"),
        pick("CPM_BROWSER_DIR", "Browser"),
        pick("CPM_TEMP_DIR", "Temp"),
    )


def resolve_browser_dir(program_dir: Path, environ: MutableMapping[str, str] | None = None) -> Path:
    return resolve_paths(program_dir, environ)[1]


def resolve_temp_dir(program_dir: Path, environ: MutableMapping[str, str] | None = None) -> Path:
    return resolve_paths(program_dir, environ)[2]


def _free_bytes(path: Path) -> int:
    probe = Path(path)
    while not probe.exists():
        parent = probe.parent
        if parent == probe:
            break
        probe = parent
    import shutil

    return shutil.disk_usage(probe).free


def check_free_space(path: Path, required: int, what: str) -> None:
    available = _free_bytes(path)
    if available < required:
        raise NotEnoughSpace(
            f"Not enough disk space for {what}: {required / 1_048_576:.0f} MB "
            f"needed, {available / 1_048_576:.0f} MB free at {path}. Nothing "
            "was written. Free up space or choose another path in paths.env."
        )


def probe_writable(path: Path, what: str) -> Path:
    """Create the folder and prove it accepts writes, or name the problem."""
    path = Path(path)
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-probe"
        probe.write_text("probe", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise DataDirNotWritable(path, str(exc), what) from exc
    return path


def prepare_data_dir(data_dir: Path, what: str = "data folder") -> None:
    """Create the layout and prove it is writable, or raise ``DataDirNotWritable``.

    The write probe matters: a folder can exist and still refuse writes (ACLs,
    a full disk, a read-only mount). Probing here turns that into one clear
    error instead of a later traceback from deep inside the database layer.
    """
    data_dir = Path(data_dir)
    check_free_space(data_dir, MIN_DATA_SPACE, what)
    probe_writable(data_dir, what)
    (data_dir / "profiles").mkdir(exist_ok=True)
    (data_dir / "logs").mkdir(exist_ok=True)


def _apply_temp_dir(temp_dir: Path) -> None:
    """Send this process's temporary files (and its children's) to ``Temp``.

    Process-local only: ``tempfile.tempdir`` and this process's TEMP/TMP
    variables decide what our own code and spawned processes use. Nothing
    system-global is touched.
    """
    import tempfile as _tempfile

    temp_dir = Path(temp_dir)
    probe_writable(temp_dir, "temp folder")
    check_free_space(temp_dir, MIN_TEMP_SPACE, "temporary files")
    _tempfile.tempdir = str(temp_dir)
    for key in ("TEMP", "TMP", "TMPDIR"):
        os.environ[key] = str(temp_dir)


# ---------------------------------------------------------------------------
# Persistent configuration
# ---------------------------------------------------------------------------


def load_config(data_dir: Path) -> dict[str, str]:
    """Read ``Data/config.env``; missing or blank values are simply absent."""
    return _read_env_file(Path(data_dir) / CONFIG_NAME)


def _read_env_file(path: Path) -> dict[str, str]:
    """Read a dotenv file, tolerating the BOM Windows editors like to add.

    A byte-order mark turns the first key into ``\\ufeffCPM_...``; silently
    losing that key is how a config file "does nothing".
    """
    from dotenv import dotenv_values

    if not path.exists():
        return {}
    values = dotenv_values(path)
    return {key.lstrip("\ufeff"): value for key, value in values.items() if value is not None}


def save_config(data_dir: Path, values: dict[str, str]) -> None:
    """Write ``Data/config.env`` atomically, so a crash cannot truncate it."""
    path = Path(data_dir) / CONFIG_NAME
    body = "".join(f"{key}={values[key]}\n" for key in sorted(values))
    tmp = path.with_suffix(".env.tmp")
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, path)


def _ensure_secret_key(config: dict[str, str], environ: MutableMapping[str, str]) -> None:
    """Give the install a stable Fernet key, kept in the config file.

    Generated once and reused from then on: the key is what makes stored proxy
    credentials readable again after a restart or a move. A key that came from
    the environment wins and is never written down.
    """
    if environ.get(SECRET_KEY_NAME) or config.get(SECRET_KEY_NAME):
        return
    config[SECRET_KEY_NAME] = Fernet.generate_key().decode()


def bootstrap(
    argv: Sequence[str] | None = None,
    *,
    frozen: bool | None = None,
    environ: MutableMapping[str, str] | None = None,
    program_dir: Path | None = None,
) -> PortableContext | None:
    """Switch this process onto the portable ``Data`` folder, or do nothing.

    Returns ``None`` for plain source runs. When portable mode is on: create
    and probe the layout, load ``Data/config.env``, make sure a secret key
    exists, and point ``CPM_DB_PATH`` inside the data folder — one setting that
    then carries profile directories along with the database.
    """
    args = list(sys.argv if argv is None else argv)
    env = os.environ if environ is None else environ
    if not is_portable_request(args, env, frozen):
        return None

    global _ACTIVE
    _ACTIVE = True
    if program_dir is None:
        program_dir = resolve_program_dir(frozen)
    data_dir, browser_dir, temp_dir = resolve_paths(program_dir, env)
    prepare_data_dir(data_dir)
    _apply_temp_dir(temp_dir)

    config = load_config(data_dir)
    had_key = bool(config.get(SECRET_KEY_NAME)) or bool(env.get(SECRET_KEY_NAME))
    _ensure_secret_key(config, env)
    if not had_key:
        save_config(data_dir, config)
    for key, value in config.items():
        env.setdefault(key, value)

    # Forced, not defaulted: portable data belongs in one folder. CPM_DATA_DIR
    # is the documented way to choose a different one.
    env["CPM_DB_PATH"] = str(data_dir / "profiles.db")

    from camoufox_pm.config import get_settings

    get_settings.cache_clear()
    return PortableContext(
        program_dir=program_dir,
        data_dir=data_dir,
        profiles_dir=data_dir / "profiles",
        logs_dir=data_dir / "logs",
        config_path=data_dir / CONFIG_NAME,
        browser_dir=browser_dir,
        temp_dir=temp_dir,
    )


# ---------------------------------------------------------------------------
# Moving Data to another disk
# ---------------------------------------------------------------------------


def _copy_tree_verified(source: Path, target: Path) -> None:
    """Copy the whole data tree and check the copy before anyone trusts it.

    Verification is part of the copy, not an afterthought: a migration that
    "succeeds" with a torn database is worse than one that fails cleanly.
    """
    import shutil
    import sqlite3

    shutil.copytree(source, target)
    src_files = sorted(p.relative_to(source) for p in source.rglob("*") if p.is_file())
    dst_files = sorted(p.relative_to(target) for p in target.rglob("*") if p.is_file())
    if src_files != dst_files:
        raise ValueError(f"copy is incomplete: {len(src_files)} files in, {len(dst_files)} out")

    database = target / "profiles.db"
    if database.exists():
        conn = sqlite3.connect(database)
        try:
            row = conn.execute("pragma integrity_check").fetchone()
            if not row or row[0] != "ok":
                raise ValueError(f"database integrity check failed: {row}")
        finally:
            conn.close()


def migrate_data(ctx: PortableContext, target: Path) -> Path:
    """Relocate Data to ``target``; return the folder the old data was kept in.

    The source is never deleted: on success it is renamed to ``Data.bak-<stamp>``
    as the rollback, and on any failure it is exactly as it was — the copy is
    built beside the target and only the copy is ever removed. The new location
    is recorded in ``paths.env`` only after the copy verifies.
    """
    import shutil

    target = Path(target)
    if not target.is_absolute():
        raise PortableError(f"The target path must be absolute: {target}")
    source = ctx.data_dir
    if target == source or target in source.parents or source in target.parents:
        raise PortableError(f"The target must not sit inside the current data folder: {target}")
    if target.exists() and any(target.iterdir()):
        raise PortableError(f"The target folder is not empty: {target}")

    check_free_space(
        target.parent,
        sum(p.stat().st_size for p in source.rglob("*") if p.is_file()),
        "the data move",
    )

    staging = target.with_name(target.name + ".migrating")
    if staging.exists():
        shutil.rmtree(staging)
    try:
        _copy_tree_verified(source, staging)
        target.parent.mkdir(parents=True, exist_ok=True)
        staging.rename(target)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise

    # The location is switched only now: if recording it fails, the fresh copy
    # goes away and the source is exactly as it was.
    overrides = load_path_overrides(ctx.program_dir)
    overrides["CPM_DATA_DIR"] = str(target)
    try:
        save_path_overrides(ctx.program_dir, overrides)
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise

    backup = source.with_name(f"{source.name}.bak-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}")
    try:
        source.rename(backup)
    except OSError as exc:  # the new location is live; the old one is just extra
        logger.warning(f"Could not rename the old data folder ({exc}); it stays at {source}")
        return source
    return backup


def save_path_overrides(program_dir: Path, values: dict[str, str]) -> None:
    """Write ``paths.env`` beside the program (atomically)."""
    path = Path(program_dir) / PATHS_NAME
    body = "".join(f"{key}={values[key]}\n" for key in sorted(values))
    tmp = path.with_suffix(".env.tmp")
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Logging: rotation and redaction
# ---------------------------------------------------------------------------

# Key/value shapes that carry secrets. The key is kept so the log stays
# readable; only the value is masked.
_KEY_VALUE = re.compile(
    r"(?i)\b([a-z0-9_\-]*(?:password|passwd|pwd|secret|token|api[_-]?key)[a-z0-9_\-]*)"
    r"(\s*[:=]\s*)([\"']?)([^\s\"',;&]+)\3"
)
_URL_CREDENTIALS = re.compile(r"(://[^/\s:@]+):([^/\s@]+)@")
_ENC_TOKEN = re.compile(r"\benc:[A-Za-z0-9_\-=+/]{8,}")
_BEARER = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-]+")


def redact(text: str) -> str:
    """Mask secret-shaped values while leaving the rest of the line readable."""
    text = _URL_CREDENTIALS.sub(r"\1:***@", text)
    text = _ENC_TOKEN.sub("enc:***", text)
    text = _BEARER.sub(r"\1 ***", text)
    return _KEY_VALUE.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}***{m.group(3)}", text)


def _redact_record(record: Any) -> None:
    record["message"] = redact(record["message"])


def install_file_logging(
    ctx: PortableContext, rotation_bytes: int = 10_000_000, retention: int = 5
) -> int:
    """Send the logs to ``Data/logs/app.log`` with rotation, redacted on the way.

    Redaction is applied at the logger, so every sink — the file and the
    console alike — shows the same masked text.
    """
    # configure() sets the patcher for every sink; logger.patch() would only
    # hand back a separate logger object and leave these sinks unredacted.
    logger.configure(patcher=_redact_record)
    return logger.add(
        ctx.logs_dir / "app.log",
        level="DEBUG",
        encoding="utf-8",
        rotation=rotation_bytes,
        retention=retention,
    )


def uninstall_redaction() -> None:
    """Put the logger back as it was (test hygiene; the app installs once)."""
    logger.configure(patcher=None)


class ConsoleCapture:
    """A rotating, redacting stand-in for ``sys.stdout``/``sys.stderr``.

    The windowed entry has no console to write to, and a windowed process with
    no stdout at all is one that fails silently — which this project does not
    allow. Writes are captured line by line (so a secret cannot slip through in
    a half-written chunk), redacted, and rolled over by size.
    """

    def __init__(
        self,
        path: Path,
        rotation_bytes: int = 10_000_000,
        retention: int = 5,
        encoding: str = "utf-8",
    ):
        self.path = Path(path)
        self.rotation_bytes = rotation_bytes
        self.retention = max(1, retention)
        self.encoding = encoding
        self._buffer = ""
        self._size = self.path.stat().st_size if self.path.exists() else 0
        self._handle: TextIO | None = open(self.path, "a", encoding=encoding)

    # -- file-like surface, enough for print(), logging and uvicorn --
    def write(self, text: str) -> int:
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._write_line(line + "\n")
        return len(text)

    def flush(self) -> None:
        if self._buffer:
            self._write_line(self._buffer)
            self._buffer = ""
        if self._handle is not None:
            self._handle.flush()

    def close(self) -> None:
        self.flush()
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def isatty(self) -> bool:
        return False

    @property
    def name(self) -> str:
        return str(self.path)

    # -- internals --
    def _write_line(self, line: str) -> None:
        data = redact(line)
        if self._handle is None:
            return
        if self._size + len(data.encode(self.encoding)) > self.rotation_bytes:
            self._rotate()
        self._handle.write(data)
        self._size += len(data.encode(self.encoding))

    def _rotate(self) -> None:
        if self._handle is not None:
            self._handle.close()
        for index in range(self.retention, 0, -1):
            source = self._rolled_path(index)
            target = self._rolled_path(index + 1)
            if source.exists():
                if index >= self.retention:
                    source.unlink()
                else:
                    source.replace(target)
        self.path.replace(self._rolled_path(1))
        self._size = 0
        self._handle = open(self.path, "a", encoding=self.encoding)

    def _rolled_path(self, index: int) -> Path:
        return self.path.with_name(f"{self.path.stem}.{index}{self.path.suffix}")


# ---------------------------------------------------------------------------
# One instance
# ---------------------------------------------------------------------------


def _pid_alive(pid: Any) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    import psutil

    return psutil.pid_exists(pid)


def acquire_instance_lock(ctx: PortableContext, port: int) -> InstanceLock:
    """Take the single-instance lock, or raise with who already holds it.

    A lock naming a dead process is stale and gets reclaimed: a crash must not
    lock the user out of their own data folder.
    """
    path = ctx.data_dir / LOCK_NAME
    payload = {
        "pid": os.getpid(),
        "port": port,
        "exe": sys.executable,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
            return InstanceLock(path, payload)
        except FileExistsError:
            holder = _read_lock(path)
            if holder is not None and _pid_alive(holder.get("pid")):
                raise InstanceAlreadyRunning(holder) from None
            try:
                path.unlink()
            except FileNotFoundError:
                pass
    raise InstanceAlreadyRunning(_read_lock(path) or {"pid": "unknown", "port": port})


def _read_lock(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


class InstanceLock:
    """A held single-instance lock; releases itself on the way out."""

    def __init__(self, path: Path, payload: dict[str, Any]):
        self.path = path
        self.payload = payload

    def release(self) -> None:
        holder = _read_lock(self.path)
        if holder is not None and holder.get("pid") != self.payload.get("pid"):
            return  # someone else's lock; not ours to remove
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


# ---------------------------------------------------------------------------
# Reporting failures to the user
# ---------------------------------------------------------------------------


def _show_error_box(title: str, message: str) -> None:
    """A native error dialog. Only used when there is no console to print to."""
    if sys.platform != "win32":  # pragma: no cover - Windows desktop edition
        return
    import ctypes

    ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)  # MB_ICONERROR


def notify_fatal(message: object, *, force_box: bool = False) -> None:
    """Make sure a startup failure reaches the user, windowed or not.

    Silent failure is the one outcome that is never acceptable: the windowed
    entry has no console, so it gets a dialog in addition to the log.
    """
    global _notified
    _notified = True
    text = str(message)
    if sys.stderr is not None:
        print(f"camoufox-pm: {text}", file=sys.stderr)
    if force_box or _windowed or sys.stderr is None:
        _show_error_box("Fingerprint Lite", text)
