"""The first-start wizard: choose where everything lives, once, up front.

The wizard is deliberately thin: it collects three paths and a browser
source, then hands them to the machinery that already exists — ``paths.env``
for the choice, the portable validation for "is this folder usable", and the
data migration for moving an existing install. It never writes the secret
key, so existing data keeps the key it already has.

Two rules the tests pin down:

* **Defaults follow the program's disk.** A portable copy on D: defaults to
  D:; nothing is forced onto C:.
* **No silent overwrite.** Pointing the wizard at a folder that already holds
  data is refused or migrated with the old copy kept — never replaced, and
  never with a regenerated key.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from camoufox_pm import portable

# Serializes Start presses within this process: the check-then-start below
# must be atomic or two rapid clicks launch two installs into one folder.
_START_LOCK = threading.Lock()
ANSWERS_ENV = "CPM_WIZARD_ANSWERS_JSON"
# The env var above is deliberately inert: nothing reads it. Automation passes
# `--wizard-answers <file>` — an explicit, visible choice — so a stray variable
# in some environment can never quietly skip the dialog on a real install.
BROWSER_SOURCES = ("download", "zip", "skip")
DATA_CHOICES = ("migrate", "fresh")


@dataclass(frozen=True)
class WizardAnswers:
    """What the user chose."""

    data_dir: Path
    browser_dir: Path
    temp_dir: Path
    browser_source: str = "download"
    zip_path: Path | None = None
    # What to do about profiles that already live elsewhere: "migrate" moves
    # them over (the old copy is kept), "fresh" deliberately starts clean and
    # leaves the old data exactly where it is. None refuses to guess.
    data_choice: str | None = None


@dataclass(frozen=True)
class WizardResult:
    """What applying the choice actually did."""

    data_dir: Path
    browser_dir: Path
    temp_dir: Path
    migrated_from: Path | None = None
    browser_path: Path | None = None
    # Records whether paths.env was newly created; existing choices and retry
    # completions can also require a fresh GUI process.
    paths_written: bool = False


def default_answers(program_dir: Path) -> WizardAnswers:
    """The program's own disk: ``Data``, ``Browser`` and ``Temp`` beside it."""
    root = Path(program_dir)
    return WizardAnswers(
        data_dir=root / "Data",
        browser_dir=root / "Browser",
        temp_dir=root / "Temp",
    )


def existing_data_dir(
    program_dir: Path, environ: MutableMapping[str, str] | None = None
) -> Path | None:
    """Where profiles live today, if anywhere (resolved the way startup does)."""
    env = os.environ if environ is None else environ
    data_dir = portable.resolve_paths(Path(program_dir), env)[0]
    return data_dir if (data_dir / "profiles.db").exists() else None


def should_show_wizard(
    program_dir: Path,
    argv: list[str] | None = None,
    environ: MutableMapping[str, str] | None = None,
) -> bool:
    """First start in desktop mode — or explicitly asked for."""
    args = list(sys_argv() if argv is None else argv)
    env = os.environ if environ is None else environ
    if "--wizard" in args or "--wizard-answers" in args:
        return True
    if "--desktop" not in args:
        return False
    if (Path(program_dir) / portable.PATHS_NAME).exists():
        return False
    return existing_data_dir(program_dir, env) is None


def answers_from_file(path: Path) -> WizardAnswers:
    """Answers for automation: an explicit ``--wizard-answers`` file."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return WizardAnswers(
        data_dir=Path(data["data_dir"]),
        browser_dir=Path(data["browser_dir"]),
        temp_dir=Path(data["temp_dir"]),
        browser_source=data.get("browser_source", "skip"),
        zip_path=Path(data["zip_path"]) if data.get("zip_path") else None,
        data_choice=data.get("data_choice"),
    )


def use_defaults(
    program_dir: Path,
    *,
    environ: MutableMapping[str, str] | None = None,
    browser_source: str = "skip",
) -> WizardResult:
    """The 'use the default locations' button: decided once, recorded once."""
    defaults = default_answers(Path(program_dir))
    return apply_answers(
        Path(program_dir),
        WizardAnswers(
            data_dir=defaults.data_dir,
            browser_dir=defaults.browser_dir,
            temp_dir=defaults.temp_dir,
            browser_source=browser_source,
        ),
        environ=environ,
    )


def sys_argv() -> list[str]:
    import sys

    return list(sys.argv)


def apply_answers(
    program_dir: Path,
    answers: WizardAnswers,
    *,
    environ: MutableMapping[str, str] | None = None,
) -> WizardResult:
    """Make the choice real, using the existing machinery at every step."""
    env = os.environ if environ is None else environ
    root = Path(program_dir)

    def rooted(value: Path) -> Path:
        return portable._rooted(root, str(value))  # noqa: SLF001 - same rule as startup

    data_dir = rooted(answers.data_dir)
    browser_dir = rooted(answers.browser_dir)
    temp_dir = rooted(answers.temp_dir)
    if answers.browser_source not in BROWSER_SOURCES:
        raise portable.PortableError(f"Unknown browser source: {answers.browser_source}")

    # Validate the auxiliary folders first — they carry no existing data.
    portable.probe_writable(browser_dir, "browser folder")
    portable.probe_writable(temp_dir, "temp folder")

    # Move existing data over only when the user says which way. Nothing may
    # silently walk away from data that already exists, and nothing may touch
    # its key: migrate keeps the old copy as the rollback, "fresh" leaves the
    # old data (and its key file) exactly where it is.
    migrated_from = None
    current = existing_data_dir(root, env)
    if current is not None and current != data_dir:
        if answers.data_choice == "fresh":
            logger.warning(f"Starting fresh at {data_dir}; the previous data stays at {current}")
        elif answers.data_choice == "migrate":
            ctx = portable.PortableContext(
                program_dir=root,
                data_dir=current,
                profiles_dir=current / "profiles",
                logs_dir=current / "logs",
                config_path=current / portable.CONFIG_NAME,
                browser_dir=browser_dir,
                temp_dir=temp_dir,
            )
            migrated_from = portable.migrate_data(ctx, data_dir)
        else:
            raise portable.PortableError(
                f"Profiles already exist at {current}. Choose explicitly what to do: "
                f"migrate them to {data_dir} (the old copy is kept as a backup), or "
                "start fresh there (the old data stays where it is and keeps its "
                "own key)."
            )

    portable.prepare_data_dir(data_dir)

    # Keep the answer on the result, so a caller can act on the fact that a
    # fresh choice has just been made.
    just_wrote = not (root / portable.PATHS_NAME).exists()

    # Record the choice beside the program, keeping any other keys.
    overrides = portable.load_path_overrides(root)
    overrides.update(
        {
            "CPM_DATA_DIR": str(data_dir),
            "CPM_BROWSER_DIR": str(browser_dir),
            "CPM_TEMP_DIR": str(temp_dir),
        }
    )
    portable.save_path_overrides(root, overrides)

    from camoufox_pm import browser_env

    browser_env.use_browser_root(browser_dir)
    browser_path = None
    if answers.browser_source == "download":
        browser_path = browser_env.install_from_download()
    elif answers.browser_source == "zip":
        if answers.zip_path is None:
            raise portable.PortableError("No browser ZIP was chosen.")
        browser_path = browser_env.install_from_zip(Path(answers.zip_path))

    logger.info(
        f"First-run setup: data={data_dir} browser={browser_dir} temp={temp_dir} "
        f"source={answers.browser_source}"
    )
    return WizardResult(
        data_dir=data_dir,
        browser_dir=browser_dir,
        temp_dir=temp_dir,
        migrated_from=migrated_from,
        browser_path=browser_path,
        paths_written=just_wrote,
    )


def run_wizard(
    program_dir: Path,
    *,
    environ: MutableMapping[str, str] | None = None,
    scripted: WizardAnswers | None = None,
) -> WizardResult | None:
    """Show the wizard (or take scripted answers) and apply the result.

    Returns ``None`` only when the user cancels — and cancelling means the
    program stops here. Continuing with the default locations is a different
    button, and a recorded decision.
    """
    if scripted is not None:
        return apply_answers(Path(program_dir), scripted, environ=environ)
    return _open_window(Path(program_dir), environ)


def _start_work(state: dict[str, Any], work: Any, *, with_geoip: bool, window: Any = None) -> str:
    """Run the setup on a worker thread so the window can report progress.

    The bridge call returns at once; the page polls ``progress()`` for the
    live stage and byte counts. A second Start while one runs is refused —
    two concurrent installs would fight over the same Browser folder.
    """
    from camoufox_pm import browser_env

    def run() -> None:
        try:
            result = work()
            if with_geoip and result is not None and result.browser_path is not None:
                # The GeoIP database the proxy checks need, fetched while the
                # wizard still shows progress. Best-effort by design: a missing
                # database only limits proxy location checks, and the app's
                # background preparation retries it after the wizard closes.
                browser_env._emit_progress("preparing")  # noqa: SLF001 - same package
                browser_env.ensure_geoip()
            state["result"] = result
            state["ok"] = True
            # A completed packaged wizard asks its caller
            # for a relaunch, so the wizards' own loop never has to become the
            # manager's loop. Every other path stays in-process.
            if portable.is_windowed() and portable.is_frozen():
                state["relaunch"] = True
        except Exception as exc:  # noqa: BLE001 - shown in the wizard, not swallowed
            logger.exception("First-run setup failed")
            progress = browser_env.browser_progress()
            if progress["stage"] not in ("failed",):
                browser_env._emit_progress("failed", error=str(exc))  # noqa: SLF001
            state["ok"] = False
            state["error"] = str(exc)
        if state.get("ok") and window is not None:
            window.destroy()

    with _START_LOCK:
        worker = state.get("worker")
        if worker is not None and worker.is_alive():
            return json.dumps({"ok": False, "error": "Setup is already running."})
        state["ok"] = None
        state["error"] = None
        browser_env.reset_browser_progress()
        thread = threading.Thread(target=run, daemon=True, name="wizard-setup")
        state["worker"] = thread
        # A second caller must not see an assigned but not-yet-alive worker.
        thread.start()
    return json.dumps({"ok": True, "started": True})


def _open_window(program_dir: Path, environ: MutableMapping[str, str] | None = None):
    """The wizard window itself: collect the choice, apply it, close.

    Three distinct outcomes: ``Start`` applies what the form shows, ``Use
    default locations`` applies the program-root defaults, and ``Cancel and
    exit`` returns ``None`` — which stops the program instead of pretending
    the question was answered.
    """
    import webview

    state: dict[str, Any] = {"result": None}
    window = None

    def pick_folder(field: str) -> str | None:
        if window is None:
            return None
        file_dialog = getattr(webview, "FileDialog", None)
        dialog_type = file_dialog.FOLDER if file_dialog is not None else webview.FOLDER_DIALOG
        picked = window.create_file_dialog(dialog_type)
        return picked[0] if picked else None

    def pick_zip() -> str | None:
        if window is None:
            return None
        file_dialog = getattr(webview, "FileDialog", None)
        dialog_type = file_dialog.OPEN if file_dialog is not None else webview.OPEN_DIALOG
        picked = window.create_file_dialog(dialog_type, file_types=("ZIP archives (*.zip)",))
        return picked[0] if picked else None

    def _answers_from(data: dict[str, Any]) -> WizardAnswers:
        return WizardAnswers(
            data_dir=Path(data["data_dir"]),
            browser_dir=Path(data["browser_dir"]),
            temp_dir=Path(data["temp_dir"]),
            browser_source=data.get("browser_source", "skip"),
            zip_path=Path(data["zip_path"]) if data.get("zip_path") else None,
            data_choice=data.get("data_choice"),
        )

    def submit(payload: str) -> str:
        return _start_work(
            state,
            lambda: apply_answers(program_dir, _answers_from(json.loads(payload)), environ=environ),
            with_geoip=True,
            window=window,
        )

    def use_defaults_api(payload: str) -> str:
        data = json.loads(payload)
        return _start_work(
            state,
            lambda: use_defaults(
                program_dir, environ=environ, browser_source=data.get("browser_source", "skip")
            ),
            with_geoip=True,
            window=window,
        )

    def cancel() -> None:
        if window is not None:
            window.destroy()

    def progress() -> str:
        """The install progress snapshot for the UI poll loop."""
        from camoufox_pm import browser_env

        snapshot = browser_env.browser_progress()
        snapshot["done"] = snapshot["stage"] in ("done", "failed")
        snapshot["ok"] = state.get("ok")
        snapshot["relaunch"] = state.get("relaunch", False)
        return json.dumps(snapshot)

    class WizardApi:
        """PyWebView discovers bound object methods, not keys in a dict."""

        def pick_folder(self, field: str) -> str | None:
            return pick_folder(field)

        def pick_zip(self) -> str | None:
            return pick_zip()

        def submit(self, payload: str) -> str:
            return submit(payload)

        def use_defaults(self, payload: str) -> str:
            return use_defaults_api(payload)

        def progress(self) -> str:
            return progress()

        def cancel(self) -> None:
            cancel()

    defaults = default_answers(program_dir)
    existing = existing_data_dir(program_dir, environ)
    lang = wizard_lang(environ)
    html = (
        _WIZARD_HTML.replace("__DATA__", str(defaults.data_dir))
        .replace("__BROWSER__", str(defaults.browser_dir))
        .replace("__TEMP__", str(defaults.temp_dir))
        .replace("__EXISTING__", str(existing) if existing else "")
        .replace("__LANG__", lang)
        .replace("__STRINGS__", json.dumps(_WIZARD_STRINGS))
    )

    window = webview.create_window(
        _WIZARD_STRINGS[lang]["window_title"],
        html=html,
        width=720,
        height=660,
        js_api=WizardApi(),
    )
    webview.start()
    return state["result"]


_WIZARD_STRINGS: dict[str, dict[str, str]] = {
    "zh-CN": {
        "window_title": "Fingerprint Lite — 首次启动",
        "title": "欢迎使用 Fingerprint Lite",
        "sub": "选择各项数据的存放位置。默认放在程序所在的磁盘上 —— 不会强制占用 C: 盘。",
        "language": "语言",
        "data": "数据",
        "browser": "浏览器",
        "temp": "临时文件",
        "browse": "浏览…",
        "data_hint": "数据库、Profile、配置、密钥与日志。",
        "browser_hint": "Camoufox 浏览器安装与 GeoIP 数据库。",
        "temp_hint": "下载与导入过程中的临时文件。",
        "engine_legend": "浏览器引擎（固定版本，SHA256 校验）",
        "src_download": "现在下载（约 470 MB）",
        "src_zip": "使用我已有的官方 ZIP 安装",
        "src_skip": "暂时跳过",
        "zip_placeholder": "camoufox-...-win.x86_64.zip 的路径",
        "choose": "选择…",
        "existing_legend": "在以下位置发现已有数据",
        "choice_migrate": "把数据搬到新的数据文件夹（保留旧副本，密钥不变）",
        "choice_fresh": "在这里全新开始（旧数据原位保留，使用自己的密钥）",
        "existing_hint": "两种方式都不会删除或改写数据 —— 只是决定程序用哪一份。",
        "start": "开始",
        "use_defaults": "使用默认位置",
        "cancel": "取消并退出",
        "working": "处理中…",
        "downloading": "正在下载",
        "reading_zip": "正在读取 ZIP",
        "verifying": "正在验证 SHA256…",
        "extracting": "正在解压并安装…",
        "preparing": "正在准备 GeoIP…",
        "done": "完成。",
        "failed": "失败。",
    },
    "en": {
        "window_title": "Fingerprint Lite — first start",
        "title": "Welcome to Fingerprint Lite",
        "sub": "Choose where things should live. The defaults sit on this program's own disk — nothing is forced onto C:.",
        "language": "Language",
        "data": "Data",
        "browser": "Browser",
        "temp": "Temp",
        "browse": "Browse…",
        "data_hint": "Database, profiles, configuration, secret key and logs.",
        "browser_hint": "The Camoufox browser install and GeoIP databases.",
        "temp_hint": "Temporary files during downloads and imports.",
        "engine_legend": "Browser engine (fixed build, SHA256-verified)",
        "src_download": "Download it now (about 470 MB)",
        "src_zip": "Install from an official ZIP I have",
        "src_skip": "Skip for now",
        "zip_placeholder": "path to camoufox-...-win.x86_64.zip",
        "choose": "Choose…",
        "existing_legend": "Existing profiles found at",
        "choice_migrate": "Move them to the new Data folder (the old copy is kept as a backup, keys unchanged)",
        "choice_fresh": "Start fresh here (the old data stays where it is, with its own key)",
        "existing_hint": "Nothing is deleted or rewritten either way — this just says which data the program should use.",
        "start": "Start",
        "use_defaults": "Use default locations",
        "cancel": "Cancel and exit",
        "working": "Working…",
        "downloading": "Downloading",
        "reading_zip": "Reading ZIP",
        "verifying": "Verifying SHA256…",
        "extracting": "Extracting and installing…",
        "preparing": "Preparing GeoIP…",
        "done": "Done.",
        "failed": "Failed.",
    },
}

WIZARD_LANGS = ("zh-CN", "en")


def wizard_lang(environ: MutableMapping[str, str] | None = None) -> str:
    """The dialog language: Simplified Chinese unless the OS speaks English.

    Anything starting with ``zh`` gets ``zh-CN``; an explicitly English OS
    locale gets ``en``; when nothing can be detected the default is ``zh-CN``.
    The dialog itself lets the user switch either way.
    """
    import locale

    env = os.environ if environ is None else environ
    for key in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        value = (env.get(key) or "").lower()
        if not value:
            continue
        if value.startswith("zh"):
            return "zh-CN"
        if value.startswith("en"):
            return "en"
    detected, _encoding = locale.getdefaultlocale()
    if detected and detected.lower().startswith("en"):
        return "en"
    return "zh-CN"


_WIZARD_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Fingerprint Lite</title><style>
body { font: 14px/1.5 system-ui, sans-serif; background:#14161b; color:#e8e8ea; margin:0; padding:28px 32px; }
h1 { font-size:20px; margin:0 0 4px; }
p.sub { color:#9aa0ab; margin:0 0 22px; }
.row { display:flex; gap:8px; align-items:center; margin:12px 0; }
.row label { width:88px; color:#b8bdc7; }
.row input[type=text] { flex:1; padding:8px 10px; border:1px solid #2c313a; border-radius:6px; background:#1b1e25; color:#e8e8ea; }
button { padding:8px 14px; border:1px solid #2c313a; border-radius:6px; background:#23272f; color:#e8e8ea; cursor:pointer; }
button.primary { background:#ff6b35; border-color:#ff6b35; color:#fff; }
fieldset { border:1px solid #2c313a; border-radius:8px; margin:20px 0; padding:12px 16px; }
legend { color:#b8bdc7; padding:0 6px; }
.hint { color:#9aa0ab; font-size:12px; margin-top:2px; }
.actions { display:flex; gap:10px; margin-top:24px; }
.error { color:#ff8a80; margin-top:12px; white-space:pre-wrap; }
</style></head><body>
<div class="row" style="margin-top:0"><label data-i18n="language">Language</label><div><label style="margin-right:12px"><input type="radio" name="lang" value="zh-CN"> 中文</label><label><input type="radio" name="lang" value="en"> English</label></div></div>
<h1 data-i18n="title">Welcome to Fingerprint Lite</h1>
<p class="sub" data-i18n="sub">Choose where things should live. The defaults sit on this program's own disk — nothing is forced onto C:.</p>
<div class="row"><label data-i18n="data">Data</label><input id="data" type="text" value="__DATA__"><button onclick="browse('data')" data-i18n="browse">Browse…</button></div>
<div class="hint" data-i18n="data_hint">Database, profiles, configuration, secret key and logs.</div>
<div class="row"><label data-i18n="browser">Browser</label><input id="browser" type="text" value="__BROWSER__"><button onclick="browse('browser')" data-i18n="browse">Browse…</button></div>
<div class="hint" data-i18n="browser_hint">The Camoufox browser install and GeoIP databases.</div>
<div class="row"><label data-i18n="temp">Temp</label><input id="temp" type="text" value="__TEMP__"><button onclick="browse('temp')" data-i18n="browse">Browse…</button></div>
<div class="hint" data-i18n="temp_hint">Temporary files during downloads and imports.</div>
<fieldset><legend data-i18n="engine_legend">Browser engine (fixed build, SHA256-verified)</legend>
<label><input type="radio" name="src" value="download" checked> <span data-i18n="src_download">Download it now (about 470 MB)</span></label><br>
<label><input type="radio" name="src" value="zip"> <span data-i18n="src_zip">Install from an official ZIP I have</span></label>
<div class="row" id="ziprow" style="display:none"><input id="zippath" type="text" data-i18n-ph="zip_placeholder" placeholder="path to camoufox-...-win.x86_64.zip"><button onclick="pickzip()" data-i18n="choose">Choose…</button></div>
<label><input type="radio" name="src" value="skip"> <span data-i18n="src_skip">Skip for now</span></label>
</fieldset>
<div id="existingbox" style="display:none">
<fieldset><legend><span data-i18n="existing_legend">Existing profiles found at</span> __EXISTING__</legend>
<label><input type="radio" name="choice" value="migrate"> <span data-i18n="choice_migrate">Move them to the new Data folder (the old copy is kept as a backup, keys unchanged)</span></label><br>
<label><input type="radio" name="choice" value="fresh"> <span data-i18n="choice_fresh">Start fresh here (the old data stays where it is, with its own key)</span></label>
<div class="hint" data-i18n="existing_hint">Nothing is deleted or rewritten either way — this just says which data the program should use.</div>
</fieldset>
</div>
<div class="actions">
<button class="primary" onclick="submitAll()" data-i18n="start">Start</button>
<button onclick="useDefaults()" data-i18n="use_defaults">Use default locations</button>
<button onclick="pywebview.api.cancel()" data-i18n="cancel">Cancel and exit</button>
</div>
<div id="progress" style="display:none;margin-top:16px">
<div id="plabel" style="color:#b8bdc7;font-size:13px;margin-bottom:6px">Working…</div>
<div style="height:8px;border-radius:4px;background:#23272f;overflow:hidden">
<div id="pbar" style="height:100%;width:0%;background:#ff6b35"></div>
</div>
</div>
<div class="error" id="error"></div>
<script>
var STRINGS = __STRINGS__;
var LANG = "__LANG__";
function applyLang(lang) {
  LANG = lang;
  var dict = STRINGS[lang] || STRINGS["en"];
  document.querySelectorAll('[data-i18n]').forEach(function (el) {
    var v = dict[el.getAttribute('data-i18n')];
    if (v) el.textContent = v;
  });
  document.querySelectorAll('[data-i18n-ph]').forEach(function (el) {
    var v = dict[el.getAttribute('data-i18n-ph')];
    if (v) el.placeholder = v;
  });
  document.querySelectorAll('input[name=lang]').forEach(function (r) { r.checked = (r.value === lang); });
}
document.querySelectorAll('input[name=lang]').forEach(function (r) {
  r.addEventListener('change', function () { if (r.checked) applyLang(r.value); });
});
function browse(field) { pywebview.api.pick_folder(field).then(function (p) { if (p) document.getElementById(field).value = p; }); }
function pickzip() { pywebview.api.pick_zip().then(function (p) { if (p) document.getElementById('zippath').value = p; }); }
document.querySelectorAll('input[name=src]').forEach(function (r) {
  r.addEventListener('change', function () {
    document.getElementById('ziprow').style.display = (r.value === 'zip' && r.checked) ? 'flex' : 'none';
  });
});
var EXISTING = "__EXISTING__";
function refreshExisting() {
  var box = document.getElementById('existingbox');
  if (EXISTING && document.getElementById('data').value !== EXISTING) {
    box.style.display = 'block';
  } else {
    box.style.display = 'none';
    document.querySelectorAll('input[name=choice]').forEach(function (r) { r.checked = false; });
  }
}
document.getElementById('data').addEventListener('input', refreshExisting);
window.addEventListener('pywebviewready', function () { applyLang(LANG); refreshExisting(); });
function payload() {
  var chosen = document.querySelector('input[name=choice]:checked');
  return {
    data_dir: document.getElementById('data').value,
    browser_dir: document.getElementById('browser').value,
    temp_dir: document.getElementById('temp').value,
    browser_source: document.querySelector('input[name=src]:checked').value,
    zip_path: document.getElementById('zippath').value || null,
    data_choice: chosen ? chosen.value : null
  };
}
function fmtMB(bytes) { return (bytes / 1048576).toFixed(1) + ' MB'; }
function stageLabel(source, stage, downloaded, total) {
  var dict = STRINGS[LANG] || STRINGS["en"];
  if (stage === 'downloading') {
    var prefix = (source === 'zip' ? dict.reading_zip : dict.downloading) + ': ';
    if (total > 0) {
      var pct = Math.floor((downloaded / total) * 100);
      return prefix + fmtMB(downloaded) + ' / ' + fmtMB(total) + ' (' + pct + '%)';
    }
    return prefix + fmtMB(downloaded);
  }
  if (stage === 'verifying') return dict.verifying;
  if (stage === 'extracting') return dict.extracting;
  if (stage === 'preparing') return dict.preparing;
  if (stage === 'done') return dict.done;
  if (stage === 'failed') return dict.failed;
  return dict.working;
}
function pollProgress(source) {
  pywebview.api.progress().then(function (raw) {
    var p = JSON.parse(raw);
    document.getElementById('progress').style.display = 'block';
    document.getElementById('plabel').textContent = stageLabel(source, p.stage, p.downloaded, p.total);
    var bar = document.getElementById('pbar');
    bar.style.width = p.total > 0 ? Math.floor((p.downloaded / p.total) * 100) + '%' : (p.stage === 'done' ? '100%' : '12%');
    if (!p.done) {
      setTimeout(function () { pollProgress(source); }, 300);
    } else if (p.ok === false) {
      document.getElementById('error').textContent = p.error || 'Setup failed.';
    }
  });
}
function submitAll() {
  document.getElementById('error').textContent = '';
  var source = document.querySelector('input[name=src]:checked').value;
  pywebview.api.submit(JSON.stringify(payload())).then(function (raw) {
    var res = JSON.parse(raw);
    if (!res.ok) { document.getElementById('error').textContent = res.error; return; }
    if (res.started) pollProgress(source);
  });
}
function useDefaults() {
  document.getElementById('error').textContent = '';
  var source = document.querySelector('input[name=src]:checked').value;
  pywebview.api.use_defaults(JSON.stringify({ browser_source: source })).then(function (raw) {
    var res = JSON.parse(raw);
    if (!res.ok) { document.getElementById('error').textContent = res.error; return; }
    if (res.started) pollProgress(source);
  });
}
</script>
</body></html>
"""
