"""The windowed entry: start the desktop app with no console to fail into.

``FingerprintLite.exe`` is a windowed build — it has no console. Every line the
server, the API or a launch prints would go nowhere, and a startup failure
would look exactly like "nothing happened". So this entry:

* captures all output into ``Data/logs/console.log`` (redacted, rotated),
* forces desktop mode — double-click means the window,
* turns any startup failure into a native error dialog and a non-zero exit.

The console entry (``camoufox-pm.exe``) shares the backend, the web UI, the
Data folder and the instance lock; only the way failures reach the user
differs.
"""

from __future__ import annotations

import sys
import traceback


def main(argv: list[str] | None = None) -> int:
    """Run the desktop app; return the process exit code."""
    from camoufox_pm import cli, portable

    portable.set_windowed(True)
    saved_out, saved_err = sys.stdout, sys.stderr
    capture = None
    try:
        program_dir = portable.resolve_program_dir()
        data_dir = portable.resolve_data_dir(program_dir)
        portable.prepare_data_dir(data_dir)
        capture = portable.ConsoleCapture(data_dir / "logs" / "console.log")
        sys.stdout = sys.stderr = capture

        sys.argv = [sys.argv[0], "--desktop", *(sys.argv[1:] if argv is None else argv)]
        cli.main()
        return 0
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
        if code != 0 and not portable.was_notified():
            portable.notify_fatal(
                f"Fingerprint Lite could not start (exit code {code}). "
                f"Details were written to {data_dir / 'logs' / 'console.log'}.",
                force_box=True,
            )
        return code
    except BaseException as exc:  # noqa: BLE001 - the whole point is to never fail silently
        traceback.print_exc()
        portable.notify_fatal(
            f"Fingerprint Lite could not start: {exc}\n\n"
            "Details were written to Data/logs/console.log.",
            force_box=True,
        )
        return 2
    finally:
        if capture is not None:
            capture.close()
        sys.stdout, sys.stderr = saved_out, saved_err


if __name__ == "__main__":
    raise SystemExit(main())
