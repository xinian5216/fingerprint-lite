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
from collections.abc import MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from camoufox_pm import portable

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
        try:
            state["result"] = apply_answers(
                program_dir, _answers_from(json.loads(payload)), environ=environ
            )
        except Exception as exc:  # noqa: BLE001 - shown in the wizard, not swallowed
            return json.dumps({"ok": False, "error": str(exc)})
        if window is not None:
            window.destroy()
        return json.dumps({"ok": True})

    def use_defaults_api(payload: str) -> str:
        data = json.loads(payload)
        try:
            state["result"] = use_defaults(
                program_dir, environ=environ, browser_source=data.get("browser_source", "skip")
            )
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"ok": False, "error": str(exc)})
        if window is not None:
            window.destroy()
        return json.dumps({"ok": True})

    def cancel() -> None:
        if window is not None:
            window.destroy()

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

        def cancel(self) -> None:
            cancel()

    defaults = default_answers(program_dir)
    existing = existing_data_dir(program_dir, environ)
    html = (
        _WIZARD_HTML.replace("__DATA__", str(defaults.data_dir))
        .replace("__BROWSER__", str(defaults.browser_dir))
        .replace("__TEMP__", str(defaults.temp_dir))
        .replace("__EXISTING__", str(existing) if existing else "")
    )

    window = webview.create_window(
        "Fingerprint Lite — first start",
        html=html,
        width=720,
        height=620,
        js_api=WizardApi(),
    )
    webview.start()
    return state["result"]


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
<h1>Welcome to Fingerprint Lite</h1>
<p class="sub">Choose where things should live. The defaults sit on this program's own disk — nothing is forced onto C:.</p>
<div class="row"><label>Data</label><input id="data" type="text" value="__DATA__"><button onclick="browse('data')">Browse…</button></div>
<div class="hint">Database, profiles, configuration, secret key and logs.</div>
<div class="row"><label>Browser</label><input id="browser" type="text" value="__BROWSER__"><button onclick="browse('browser')">Browse…</button></div>
<div class="hint">The Camoufox browser install and GeoIP databases.</div>
<div class="row"><label>Temp</label><input id="temp" type="text" value="__TEMP__"><button onclick="browse('temp')">Browse…</button></div>
<div class="hint">Temporary files during downloads and imports.</div>
<fieldset><legend>Browser engine (fixed build, SHA256-verified)</legend>
<label><input type="radio" name="src" value="download" checked> Download it now (about 470 MB)</label><br>
<label><input type="radio" name="src" value="zip"> Install from an official ZIP I have</label>
<div class="row" id="ziprow" style="display:none"><input id="zippath" type="text" placeholder="path to camoufox-...-win.x86_64.zip"><button onclick="pickzip()">Choose…</button></div>
<label><input type="radio" name="src" value="skip"> Skip for now</label>
</fieldset>
<div id="existingbox" style="display:none">
<fieldset><legend>Existing profiles found at __EXISTING__</legend>
<label><input type="radio" name="choice" value="migrate"> Move them to the new Data folder (the old copy is kept as a backup, keys unchanged)</label><br>
<label><input type="radio" name="choice" value="fresh"> Start fresh here (the old data stays where it is, with its own key)</label>
<div class="hint">Nothing is deleted or rewritten either way — this just says which data the program should use.</div>
</fieldset>
</div>
<div class="actions">
<button class="primary" onclick="submitAll()">Start</button>
<button onclick="useDefaults()">Use default locations</button>
<button onclick="pywebview.api.cancel()">Cancel and exit</button>
</div>
<div class="error" id="error"></div>
<script>
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
window.addEventListener('pywebviewready', refreshExisting);
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
function submitAll() {
  document.getElementById('error').textContent = 'Working…';
  pywebview.api.submit(JSON.stringify(payload())).then(function (raw) {
    var res = JSON.parse(raw);
    if (!res.ok) document.getElementById('error').textContent = res.error;
  });
}
function useDefaults() {
  document.getElementById('error').textContent = 'Working…';
  pywebview.api.use_defaults(JSON.stringify({ browser_source: document.querySelector('input[name=src]:checked').value })).then(function (raw) {
    var res = JSON.parse(raw);
    if (!res.ok) document.getElementById('error').textContent = res.error;
  });
}
</script>
</body></html>
"""
