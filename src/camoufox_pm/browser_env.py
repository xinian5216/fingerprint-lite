"""The pinned Camoufox browser: one verified install path for every source.

The portable edition runs exactly one browser build — the one this product was
validated against (camoufox 0.5.6 with browser ``152.0.4-beta.30``). Not
"whatever is newest", not "whatever is already cached": a browser that appears
later on the network is not automatically trusted, because a fingerprint
manager silently swapping its engine is a quiet way to invalidate every
identity it manages.

Three rules, identical for the online download and the offline ZIP:

* **One pin.** The expected SHA256 is recorded here, from the official release
  asset. Every install verifies against it and refuses anything else — there is
  no "skip verification" door and no automatic upgrade.
* **One atomic install.** Both sources hand their bytes to camoufox's own
  installer, which verifies the digest *before* extracting and installs into a
  fresh versioned folder. A failed download or extraction removes only what it
  just created; an existing browser and the profile data under ``Data/`` are
  never touched.
* **Reuse before anything else.** If the pinned build is already installed and
  complete, nothing is downloaded or reinstalled.

After a failed download the manual entry is offered instead of retrying on its
own: drop the official ZIP into the ``Browser`` folder next to the program and
restart, or run ``camoufox-pm browser install <zip>``.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from camoufox_pm import portable

# The one validated browser build. The digest is the official release asset's
# published SHA256 (camoufox-152.0.4-beta.30-win.x86_64.zip); it covers the
# Windows x64 build this edition targets.
PINNED_REPO = "daijro/camoufox"
PINNED_VERSION = "152.0.4"
PINNED_BUILD = "beta.30"
PINNED_SHA256 = "ea52a02fb1cfb1813ef6a326bea03fb2b650c9774143d953a94a27bfc8f10072"

# Where a user may drop the official ZIP for an offline install.
OFFLINE_DROP_NAME = "Browser"

# Peak room a browser install needs: the 470 MB download beside the extracted
# build, both on the destination disk.
REQUIRED_BROWSER_SPACE = 2 * 1024 * 1024 * 1024

_DOWNLOAD_FAILED = False
_BROWSER_DIR: Path | None = None

# Browser-install progress for the first-start wizard (and any other UI).
#
# The installer itself is camoufox's — digest check, atomic folder, cleanup —
# so this only *observes*: byte counts come from its own progress hook,
# stage names from the wrapper points around it. Guarded by a lock because
# the wizard runs the install on a worker thread while the UI polls.
_PROGRESS_LOCK = threading.Lock()
_PROGRESS: dict[str, Any] = {"stage": "idle", "downloaded": 0, "total": 0, "error": None}
_PROGRESS_LISTENERS: list[Any] = []


def use_browser_root(browser_dir: Path) -> None:
    """Keep the browser install and GeoIP on the chosen disk.

    camoufox hardcodes its managed cache into the user's profile directory on
    C:. Its helpers read these module constants at call time, so pointing them
    at our Browser folder moves the gigabytes — and the GeoIP databases with
    them — onto the chosen disk. camoufox's own source is untouched, and
    nothing outside this process changes.
    """
    global _BROWSER_DIR
    from camoufox import geolocation, multiversion, pkgman

    from camoufox_pm.geoip_compat import install_windows_geoip_reader

    install_windows_geoip_reader()

    _BROWSER_DIR = Path(browser_dir)
    cache = _BROWSER_DIR / "cache"
    pkgman.INSTALL_DIR = cache
    # multiversion imports pkgman.INSTALL_DIR by value at module import time;
    # update its own root as well or active-path resolution can still launch a
    # copy from the user's global cache instead of this selected Browser folder.
    multiversion.INSTALL_DIR = cache
    multiversion.BROWSERS_DIR = cache / "browsers"
    multiversion.CONFIG_FILE = cache / "config.json"
    multiversion.REPO_CACHE_FILE = cache / "repo_cache.json"
    multiversion.COMPAT_FLAG = cache / ".0.5_FLAG"
    geolocation.GEOIP_DIR = cache / "geoip"
    geolocation.MMDB_DIR = cache / "geoip" / "mmdb"
    geolocation.GEOIP_CONFIG = cache / "geoip" / "config.yml"


class BrowserInstallError(RuntimeError):
    """The pinned browser could not be provided, with what to do about it."""


@dataclass(frozen=True)
class BrowserStatus:
    """What the pinned browser looks like right now."""

    installed: bool
    path: Path | None
    sha256: str
    version: str
    cache_dir: Path
    download_failed: bool


def pin_version_string() -> str:
    return f"{PINNED_VERSION}-{PINNED_BUILD}"


def reset_install_state() -> None:
    """Forget the session's download verdict (test hygiene, manual retry)."""
    global _DOWNLOAD_FAILED
    _DOWNLOAD_FAILED = False


def download_failed() -> bool:
    return _DOWNLOAD_FAILED


def reset_browser_progress() -> None:
    """Clear the install progress snapshot (test hygiene, fresh wizard runs)."""
    with _PROGRESS_LOCK:
        _PROGRESS.update({"stage": "idle", "downloaded": 0, "total": 0, "error": None})


def browser_progress() -> dict[str, Any]:
    """A copy of the current install progress for UI polling."""
    with _PROGRESS_LOCK:
        return dict(_PROGRESS)


def set_browser_progress_listener(listener: Any | None) -> None:
    """An optional callback invoked with each progress snapshot (tests, UI push)."""
    with _PROGRESS_LOCK:
        _PROGRESS_LISTENERS.clear()
        if listener is not None:
            _PROGRESS_LISTENERS.append(listener)


def _emit_progress(
    stage: str, downloaded: int = 0, total: int = 0, error: str | None = None
) -> None:
    with _PROGRESS_LOCK:
        _PROGRESS.update({"stage": stage, "downloaded": downloaded, "total": total, "error": error})
        snapshot = dict(_PROGRESS)
        listeners = list(_PROGRESS_LISTENERS)
    for listener in listeners:
        try:
            listener(snapshot)
        except Exception:  # noqa: BLE001 - progress must never break the install
            logger.warning("Browser progress listener failed")


# ---------------------------------------------------------------------------
# Locating the install
# ---------------------------------------------------------------------------


def _repo_name() -> str:
    from camoufox import multiversion

    return multiversion.get_repo_name(PINNED_REPO)


def _expected_folder() -> Path:
    from camoufox import multiversion

    folder = multiversion.version_folder_name(PINNED_VERSION, PINNED_BUILD, PINNED_SHA256[:8])
    return multiversion.BROWSERS_DIR / _repo_name() / folder


def _relative_install() -> str:
    return f"browsers/{_repo_name()}/{_expected_folder().name}"


def installed_path() -> Path | None:
    """The pinned install, if it is present and complete — else ``None``.

    "Complete" means the payload is really there, not just a folder left over
    from an interrupted install: metadata must name the pinned digest and the
    executable must exist.
    """
    folder = _expected_folder()
    metadata = folder / "version.json"
    if not metadata.exists():
        return None
    try:
        record = json.loads(metadata.read_text(encoding="utf-8"))
        from camoufox.pkgman import launch_path

        launch_path(folder)  # raises unless the executable is on disk
    except Exception:  # noqa: BLE001 - any doubt counts as "not installed"
        return None
    if (
        record.get("sha256") != PINNED_SHA256
        or record.get("version") != PINNED_VERSION
        or record.get("build") != PINNED_BUILD
    ):
        return None
    return folder


def _activate(folder: Path) -> None:
    """Make camoufox's own resolution use this install, not a newer download."""
    from camoufox import multiversion

    try:
        if multiversion.get_active_path() != folder:
            multiversion.set_active(_relative_install())
    except Exception as exc:  # noqa: BLE001 - activation is a convenience, not the install
        logger.warning(f"Could not activate the pinned browser explicitly: {exc}")


def browser_status() -> BrowserStatus:
    folder = installed_path()
    from camoufox import multiversion

    return BrowserStatus(
        installed=folder is not None,
        path=folder,
        sha256=PINNED_SHA256,
        version=pin_version_string(),
        cache_dir=multiversion.BROWSERS_DIR,
        download_failed=_DOWNLOAD_FAILED,
    )


# ---------------------------------------------------------------------------
# Installing — the same verified, atomic path for both sources
# ---------------------------------------------------------------------------


def _announce(stage: str, func: Any) -> Any:
    """Wrap an installer step so its start is reported before delegating."""

    def announced(*args: Any, **kwargs: Any) -> Any:
        progress = browser_progress()
        _emit_progress(stage, progress["downloaded"], progress["total"])
        return func(*args, **kwargs)

    return announced


class _PinnedFetcher:
    """Serves the pinned install request from a URL or a local ZIP.

    camoufox's installer does the real work — digest verification before
    extraction, a fresh versioned folder, cleanup on failure. This only decides
    where the bytes come from, so the offline path cannot drift into a weaker
    copy of the online one.
    """

    def __init__(self, local_zip: Path | None = None):
        from camoufox.pkgman import AvailableVersion, CamoufoxFetcher, Version

        self.local_zip = Path(local_zip) if local_zip else None
        pin = AvailableVersion(
            version=Version(build=PINNED_BUILD, version=PINNED_VERSION),
            url=_asset_url(),
            is_prerelease=False,
            sha256=PINNED_SHA256,
        )
        self._fetcher = CamoufoxFetcher(selected_version=pin)
        if self.local_zip is not None:
            self._fetcher._url = str(self.local_zip)  # noqa: SLF001 - display + source only

    def download_file(self, file: Any, url: str) -> Any:
        if self.local_zip is None:
            from camoufox.pkgman import CamoufoxFetcher, webdl

            # Tests replace CamoufoxFetcher.download_file with a stub to
            # exercise the online path without network; a replaced attribute
            # is a plain function where the stock one is a staticmethod, so
            # the stub keeps working and only the real download gets
            # progress reporting.
            if not isinstance(CamoufoxFetcher.__dict__.get("download_file"), staticmethod):
                result = CamoufoxFetcher.download_file(file, url)
                _emit_progress("downloading", 0, 0)
                return result
            # The real downloader, with its own progress hook instead of the
            # console bar: bytes and Content-Length come from the response, so
            # no HTTP logic is reimplemented here.
            _emit_progress("downloading", 0, 0)

            def report(downloaded: int, total: int) -> None:
                _emit_progress("downloading", downloaded, total)

            return webdl(url, buffer=file, bar=False, progress_callback=report)
        total = self.local_zip.stat().st_size
        _emit_progress("downloading", 0, total)
        copied = 0
        with open(self.local_zip, "rb") as source:
            while chunk := source.read(1 << 20):
                file.write(chunk)
                copied += len(chunk)
                _emit_progress("downloading", copied, total)
        return file

    def install(self, replace: bool = False) -> None:
        # Point the installer at our byte source, whatever it is; the inherited
        # install() then runs the full verified flow (digest check, atomic
        # versioned folder, Firefox profile dir) exactly as an online install.
        # Stage labels are observed, not reimplemented: the wrappers below only
        # announce the phase before delegating to camoufox's own functions.
        from camoufox import multiversion

        self._fetcher.download_file = self.download_file  # type: ignore[method-assign]
        real_verify, real_unzip = multiversion.verify_sha256, multiversion.unzip
        try:
            multiversion.verify_sha256 = _announce("verifying", real_verify)  # type: ignore[method-assign]
            multiversion.unzip = _announce("extracting", real_unzip)  # type: ignore[method-assign]
            self._fetcher.install(replace=replace)
        finally:
            multiversion.verify_sha256, multiversion.unzip = real_verify, real_unzip

    @property
    def url(self) -> str:
        return str(self.local_zip) if self.local_zip else _asset_url()


def _asset_url() -> str:
    from camoufox.pkgman import RepoConfig

    config = RepoConfig.get_default()
    name = (
        f"camoufox-{PINNED_VERSION}-{PINNED_BUILD}-{config.get_os_name()}.{config.get_arch()}.zip"
    )
    return f"https://github.com/{PINNED_REPO}/releases/download/v{pin_version_string()}/{name}"


def _install_lock_path(root: Path) -> Path:
    return Path(root) / ".install.lock"


def hold_install_lock(root: Path) -> Any:
    """Serialize browser installs into one root, across processes.

    Two setups racing the same Browser folder (a double-clicked Start, two
    wizard windows, an app restart mid-install) would otherwise extract over
    each other and fail obscurely halfway through verification. The lock is
    an OS file lock held open for the install duration, so a dead process
    can never leave a stale lock behind: ``msvcrt`` on Windows, ``fcntl``
    elsewhere. Raises :class:`BrowserInstallError` when another install
    already holds it.
    """
    import contextlib
    import sys

    Path(root).mkdir(parents=True, exist_ok=True)
    path = _install_lock_path(root)

    @contextlib.contextmanager
    def locked() -> Any:
        with open(path, "a+b") as handle:
            try:
                if sys.platform == "win32":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise BrowserInstallError(
                    "Another browser install is already running for this "
                    "Browser folder. Wait for it to finish instead of starting "
                    "a second one."
                ) from exc
            try:
                yield
            finally:
                try:
                    if sys.platform == "win32":
                        import msvcrt

                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:  # noqa: BLE001 - unlocking at cleanup is best-effort
                    pass

    return locked()


def _install(replace: bool, source: str, local_zip: Path | None = None) -> Path:
    """Run the shared install and confirm the result, or explain the failure."""
    import tempfile

    root = default_offline_dir()
    work = root / ".tmp"
    previous_temp = tempfile.tempdir
    reset_browser_progress()
    try:
        portable.check_free_space(root, REQUIRED_BROWSER_SPACE, "the browser install")
        work.mkdir(parents=True, exist_ok=True)
        # camoufox stages the download and the extraction with the process temp
        # dir; putting that beside the install keeps both on the destination
        # disk.
        tempfile.tempdir = str(work)
        with hold_install_lock(root):
            _PinnedFetcher(local_zip=local_zip).install(replace=replace)
    except Exception as exc:  # noqa: BLE001 - every failure becomes a clear message
        progress = browser_progress()
        _emit_progress("failed", progress["downloaded"], progress["total"], error=str(exc))
        raise BrowserInstallError(
            f"Installing the pinned browser from {source} failed: {exc}\n"
            "Nothing was changed: an existing browser and the profile data "
            "under Data are untouched."
        ) from exc
    finally:
        tempfile.tempdir = previous_temp

    folder = installed_path()
    if folder is None:
        _emit_progress("failed", error="The install did not produce a complete browser.")
        raise BrowserInstallError(
            f"The install from {source} did not produce a complete pinned browser. "
            "Nothing was changed."
        )
    _activate(folder)
    progress = browser_progress()
    _emit_progress("done", progress["downloaded"], progress["total"])
    return folder


def install_from_zip(zip_path: Path, *, replace: bool = False) -> Path:
    """Install the pinned browser from a local official ZIP (SHA256 verified).

    The digest is checked before the installer is even asked: a file the user
    picked by hand deserves a straight answer about whether it is the pinned
    build, whatever else happens to be installed.
    """
    zip_path = Path(zip_path)
    if not zip_path.is_file():
        raise BrowserInstallError(f"No such file: {zip_path}")
    digest = _sha256_file(zip_path)
    if digest != PINNED_SHA256:
        raise BrowserInstallError(
            f"{zip_path.name} is not the pinned browser: its SHA256 {digest[:12]}\u2026 "
            f"is not the pinned {PINNED_SHA256[:12]}\u2026. Nothing was changed."
        )
    return _install(replace, f"zip {zip_path.name}", local_zip=zip_path)


def install_from_download(*, replace: bool = False) -> Path:
    """Download and install the pinned browser (the explicit retry path)."""
    return _install(replace, f"the download ({_asset_url()})")


# ---------------------------------------------------------------------------
# The offline drop-in folder
# ---------------------------------------------------------------------------


def default_offline_dir() -> Path:
    return (_BROWSER_DIR or portable.resolve_program_dir() / OFFLINE_DROP_NAME).resolve()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _scan_offline(offline_dir: Path | None) -> tuple[Path | None, list[str]]:
    """Find a ZIP matching the pin; note the ones that did not."""
    directory = Path(offline_dir) if offline_dir else default_offline_dir()
    if not directory.is_dir():
        return None, []
    notes: list[str] = []
    for zip_path in sorted(directory.glob("*.zip")):
        digest = _sha256_file(zip_path)
        if digest == PINNED_SHA256:
            return zip_path, notes
        notes.append(
            f"Ignored {zip_path.name}: its SHA256 {digest[:12]}… is not the "
            f"pinned {PINNED_SHA256[:12]}…"
        )
    return None, notes


def find_offline_zip(offline_dir: Path | None = None) -> Path | None:
    """The matching ZIP in the drop-in folder, if any (mismatches are logged)."""
    zip_path, notes = _scan_offline(offline_dir)
    for note in notes:
        logger.warning(note)
    return zip_path


def _manual_instructions(reason: str = "") -> str:
    head = f"{reason}\n" if reason else ""
    return (
        f"{head}"
        f"Install the pinned browser manually:\n"
        f"  1. Get camoufox-{pin_version_string()}-win.x86_64.zip from\n"
        f"     https://github.com/{PINNED_REPO}/releases/tag/v{pin_version_string()}\n"
        f"  2. Put the ZIP in the '{OFFLINE_DROP_NAME}' folder next to the program "
        f"and restart, or run\n"
        f"     camoufox-pm browser install <path-to-zip>\n"
        f"Online download can be retried with: camoufox-pm browser install --download"
    )


# ---------------------------------------------------------------------------
# The entry points
# ---------------------------------------------------------------------------


def ensure_browser(
    *, offline_dir: Path | None = None, allow_download: bool = True, replace: bool = False
) -> Path:
    """Make the pinned browser available: reuse, offline ZIP, or one download.

    Order matters. Reuse first (a complete install is never touched), then the
    ZIP the user placed in ``Browser`` (their explicit intent), then a single
    download attempt per session — a failed download is not retried behind the
    user's back; the manual entry is offered instead.
    """
    global _DOWNLOAD_FAILED

    folder = installed_path()
    if folder is not None and not replace:
        _activate(folder)
        return folder

    # A folder that is present but unusable is a broken install, not a browser.
    # Replacing it cannot lose a working one — installed_path() above already
    # said there is none — and reinstalling is the only way back.
    if folder is None and _expected_folder().exists():
        replace = True

    zip_path, notes = _scan_offline(offline_dir)
    if zip_path is not None:
        logger.info(f"Installing the pinned browser from {zip_path.name}")
        return _install(replace, f"zip {zip_path.name}", local_zip=zip_path)

    note_text = "".join(f"{note}\n" for note in notes)
    if allow_download and not _DOWNLOAD_FAILED:
        try:
            return install_from_download(replace=replace)
        except Exception as exc:  # noqa: BLE001 - one attempt, then the manual door
            _DOWNLOAD_FAILED = True
            raise BrowserInstallError(f"{exc}\n{note_text}{_manual_instructions()}") from exc

    reason = "the download failed earlier in this session" if _DOWNLOAD_FAILED else ""
    raise BrowserInstallError(f"{note_text}{_manual_instructions(reason)}".strip())


def ensure_ready() -> Path:
    """Launch guard: reuse the pinned install or refuse. Never downloads.

    This is what stops camoufox's own "download whatever is current" fallback
    from ever firing behind the user's back.
    """
    folder = installed_path()
    if folder is not None:
        _activate(folder)
        return folder
    if _DOWNLOAD_FAILED:
        raise BrowserInstallError(_manual_instructions("The browser download failed earlier."))
    raise BrowserInstallError(
        "The pinned browser is not ready yet — it is still being prepared. "
        "Try again shortly, or install it manually.\n" + _manual_instructions()
    )


def ensure_in_background(offline_dir: Path | None = None) -> threading.Thread | None:
    """Prepare the pinned browser without holding up startup.

    The job always runs — reuse is fast, and the rest of the environment (the
    GeoIP database the proxy checks need) must be prepared even when the
    browser is already in place. Anything missing is fetched on a daemon
    thread, so the app opens at once and a launch says clearly that the
    browser is still being prepared rather than freezing the whole UI.
    """

    def run() -> None:
        try:
            ensure_browser(offline_dir=offline_dir)
            ensure_geoip()
        except Exception as exc:  # noqa: BLE001 - logged, and the launch path repeats it
            logger.error(f"Could not prepare the pinned browser: {exc}")

    folder = installed_path()
    if folder is not None:
        _activate(folder)
    thread = threading.Thread(target=run, daemon=True, name="browser-env")
    thread.start()
    return thread


def ensure_geoip() -> None:
    """Best-effort GeoIP database for proxy location alignment.

    The upstream database publishes no digest of its own (documented in the
    handoff); what is recorded is the digest of what was actually downloaded.
    """
    try:
        from camoufox import geolocation

        from camoufox_pm.geoip_compat import install_windows_geoip_reader

        install_windows_geoip_reader()

        v4 = geolocation.get_mmdb_path("ipv4")
        v6 = geolocation.get_mmdb_path("ipv6")
        if v4.exists() and v6.exists():
            return
        geolocation.download_mmdb()
        for path in (v4, v6):
            if path.exists():
                logger.info(f"GeoIP {path.name} ready (sha256 {_sha256_file(path)[:16]}…)")
    except Exception as exc:  # noqa: BLE001 - proxy checks degrade, launches do not
        logger.warning(
            f"GeoIP database unavailable ({exc}); proxy location checks will be limited."
        )
