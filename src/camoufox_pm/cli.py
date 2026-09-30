"""Command-line launcher: run the API + bundled web UI as one process.

Installed as the ``camoufox-pm`` console script. Starts the server and opens the
web UI in the default browser. The ``user`` subcommands manage login accounts —
they exist on the CLI, not the API, so the first user can be created without a
chicken-and-egg lockout and creating accounts requires shell access to the host.
"""

import argparse
import asyncio
import getpass
import os
import sys
import threading
import webbrowser
from pathlib import Path
from typing import NoReturn

import uvicorn
from loguru import logger

from camoufox_pm import browser_env, portable
from camoufox_pm.config import get_settings
from camoufox_pm.core.database import StorageManager
from camoufox_pm.core.leases import lease_expired


def main() -> None:
    # The first-start wizard runs before anything else: it decides where the
    # data, the browser and the temp files will live, and records that in
    # paths.env — which the bootstrap below reads. Starting the backend first
    # would mean half-applying a path choice.
    try:
        _maybe_run_first_start_wizard()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - a failed setup must be loud, never silent
        portable.notify_fatal(f"The first-run setup could not finish: {exc}")
        raise SystemExit(2) from exc

    # Portable mode comes next: it points the database, the secret key and the
    # logs at the Data folder, and must do so before anything reads settings.
    try:
        ctx = portable.bootstrap()
    except portable.DataDirNotWritable as exc:
        portable.notify_fatal(exc)
        raise SystemExit(2) from exc
    if ctx is not None:
        # Keep the browser install and GeoIP on the chosen disk, and the
        # window's own cache out of the user profile on C:.
        browser_env.use_browser_root(ctx.browser_dir)

    settings = get_settings()
    parser = argparse.ArgumentParser(
        prog="camoufox-pm",
        description="Run Fingerprint Lite (API + web UI) on one port.",
    )
    parser.add_argument("--host", default=settings.host, help="Bind address")
    parser.add_argument("--port", type=int, default=settings.port, help="Port")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser")
    parser.add_argument(
        "--desktop",
        action="store_true",
        help="Open a native desktop window instead of a browser tab (needs the 'desktop' extra)",
    )
    parser.add_argument(
        "--portable",
        action="store_true",
        help="Keep data, config and logs in a Data folder beside the program",
    )
    parser.add_argument(
        "--wizard",
        action="store_true",
        help="Run the first-start setup dialog (shown automatically on a first desktop start)",
    )
    parser.add_argument(
        "--wizard-answers",
        metavar="FILE",
        default=None,
        help="Automation: apply these wizard answers from a JSON file instead of showing the dialog",
    )

    subcommands = parser.add_subparsers(dest="command")
    browser_parser = subcommands.add_parser(
        "browser",
        help="Prepare the pinned Camoufox browser (offline ZIP or download)",
        description=(
            "Install or inspect the fixed Camoufox browser build this program "
            "runs. Installs always verify the pinned SHA256; there is no way to "
            "skip the check and no automatic upgrade to a newer build."
        ),
    )
    browser_commands = browser_parser.add_subparsers(dest="browser_command", required=True)
    browser_install = browser_commands.add_parser(
        "install", help="Install the pinned browser from a ZIP or by downloading it"
    )
    browser_install.add_argument(
        "zip", nargs="?", default=None, help="Path to the official browser ZIP (optional)"
    )
    browser_install.add_argument(
        "--download",
        action="store_true",
        help="Retry the online download even after an earlier failure",
    )
    browser_install.add_argument(
        "--replace", action="store_true", help="Reinstall even if the pinned build is present"
    )
    browser_commands.add_parser("status", help="Show the pinned browser and where it lives")

    data_parser = subcommands.add_parser(
        "data",
        help="Manage where the portable data folder lives",
        description=(
            "Move the Data folder to another disk. The old data is kept as a "
            "backup; a failed move changes nothing."
        ),
    )
    data_commands = data_parser.add_subparsers(dest="data_command", required=True)
    data_migrate = data_commands.add_parser(
        "migrate", help="Copy Data to a new folder and switch to it"
    )
    data_migrate.add_argument("target", help="Absolute path of the new data folder")
    data_migrate.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")

    user_parser = subcommands.add_parser(
        "user",
        help="Manage login accounts (creating the first one turns login on)",
        description=(
            "Manage web UI login accounts. As long as any account exists, the API "
            "requires a login session or the API key; removing the last one turns "
            "login off again."
        ),
    )
    user_commands = user_parser.add_subparsers(dest="user_command", required=True)

    add = user_commands.add_parser("add", help="Create an account (prompts for the password)")
    add.add_argument("username")
    add.add_argument(
        "--password-stdin",
        action="store_true",
        help="Read the password from the first line of stdin instead of prompting",
    )

    passwd = user_commands.add_parser("passwd", help="Change an account's password")
    passwd.add_argument("username")
    passwd.add_argument(
        "--password-stdin",
        action="store_true",
        help="Read the password from the first line of stdin instead of prompting",
    )

    remove = user_commands.add_parser("remove", help="Delete an account and its sessions")
    remove.add_argument("username")

    user_commands.add_parser("list", help="List accounts (never shows password hashes)")

    # Shell-only on purpose, like the user commands above. A force-unlock in the
    # HTTP API would become a button, and a button is the shortest path back to
    # two machines driving one identity — the corruption the lease prevents.
    unlock = subcommands.add_parser(
        "unlock",
        help="Force-release the lease on a profile",
        description=(
            "Clear the lease holding a profile, whatever it says. Needed only when a "
            "machine died in a way its lease cannot notice, or when you have checked "
            "that the holder is really gone: a live lease means another instance may "
            "be driving this profile right now."
        ),
    )
    unlock.add_argument("profile_id")
    unlock.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")

    subcommands.add_parser(
        "leases",
        help="List the profiles currently leased, and by whom",
    )

    args = parser.parse_args()

    if args.command == "browser":
        _run_browser_command(args)
        return

    if args.command == "data":
        _run_data_command(args, ctx)
        return

    if args.command == "user":
        asyncio.run(_run_user_command(args))
        return

    if args.command == "unlock":
        asyncio.run(_run_unlock_command(args))
        return

    if args.command == "leases":
        asyncio.run(_run_leases_command())
        return

    # Make the settings match what we are about to bind, so everything that reads
    # them (the Settings screen, CORS, logs) reports the real address rather than
    # the default the flags just overrode.
    os.environ["CPM_HOST"] = args.host
    os.environ["CPM_PORT"] = str(args.port)
    get_settings.cache_clear()

    # One server per data folder: a second double-click must be told the first
    # one is running rather than starting a second server (or worse, opening a
    # window at someone else's socket). Plain source runs keep today's freedom
    # to start several instances.
    lock = None
    if ctx is not None:
        try:
            lock = portable.acquire_instance_lock(ctx, args.port)
        except portable.InstanceAlreadyRunning as exc:
            portable.notify_fatal(exc)
            raise SystemExit(0) from exc

    try:
        if ctx is not None:
            # File logging starts here, not at bootstrap: the log file lives in
            # Data, and commands like `data migrate` need to rename that folder
            # — an open handle would make the rename fail on Windows.
            portable.install_file_logging(ctx)
            # Prepare the pinned browser without holding up startup: the window
            # opens at once, and a launch says clearly if it is still coming.
            browser_env.ensure_in_background()

        if args.desktop:
            from camoufox_pm.desktop import run_desktop

            run_desktop(
                host=args.host,
                port=args.port,
                storage_path=str(ctx.temp_dir / "webview") if ctx is not None else None,
            )
            return

        if not args.no_browser:
            url = f"http://{'localhost' if args.host in ('0.0.0.0', '127.0.0.1') else args.host}:{args.port}/"
            threading.Timer(1.5, lambda: webbrowser.open(url)).start()

        # Import the app object (not an import string) so this works inside a frozen
        # PyInstaller bundle where uvicorn cannot resolve the module by name.
        from camoufox_pm.main import app

        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    finally:
        if lock is not None:
            lock.release()


def _fail(message: str) -> NoReturn:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def _maybe_run_first_start_wizard() -> None:
    """Offer the first-start setup when it applies; apply its answer if given.

    Cancel is not "continue with defaults": it is a different button in the
    dialog, and it stops the program. Automation answers come from an explicit
    ``--wizard-answers`` file — never from ambient environment.

    On a packaged first-start that completes the setup and writes paths.env,
    a fresh copy of the program is started (this one skips the wizard and
    enters the desktop manager) and this process ends there — one GUI loop
    per process, instead of a second pywebview run beside the wizard's.
    """
    from camoufox_pm import portable, wizard

    program_dir = portable.resolve_program_dir()
    argv = list(sys.argv[1:])
    answers_file = None
    if "--wizard-answers" in argv:
        index = argv.index("--wizard-answers")
        answers_file = argv[index + 1] if index + 1 < len(argv) else None
        if answers_file is None:
            _fail("--wizard-answers needs a JSON file path.")
    scripted = wizard.answers_from_file(Path(answers_file)) if answers_file else None

    if not wizard.should_show_wizard(program_dir):
        logger.info("Startup pid={}: wizard skipped", os.getpid())
        return
    logger.info("Startup pid={}: wizard starting", os.getpid())
    result = wizard.run_wizard(program_dir, scripted=scripted)
    if result is None:
        # The user chose to leave: that is a decision, not a failure — exit
        # quietly. (Silent-failure rules are for failures; this is not one.)
        print("First-run setup cancelled. Nothing was changed.", file=sys.stderr)
        raise SystemExit(0)

    logger.info("Startup pid={}: wizard completed", os.getpid())
    # Explicit setup and retries also consumed this process's GUI loop, even
    # when paths.env already existed before the successful attempt.
    if portable.is_windowed() and portable.is_frozen():
        _relaunch_for_manager(program_dir, argv)


def _relaunch_for_manager(program_dir: Path, argv: list[str]) -> None:
    """Start the manager in a fresh process and end this one.

    Only ever called after the wizard completed and paths.env is fully on
    disk. The child therefore skips the wizard (paths.env exists) and runs
    the desktop path's single pywebview loop. Source runs never call this helper.
    """
    import subprocess

    from camoufox_pm import portable

    exe = portable.resolve_program_exe(program_dir)
    if exe is None or not (Path(program_dir) / portable.PATHS_NAME).is_file():
        portable.notify_fatal(
            "Setup finished, but the executable or paths.env is missing. "
            "The manager was not started. Restore the portable program files and try again."
        )
        raise SystemExit(3)

    # Setup-only flags would force the child back into the wizard, recursively.
    forward = []
    args = iter(argv)
    for arg in args:
        if arg == "--wizard-answers":
            next(args, None)
        elif arg not in ("--desktop", "--wizard") and not arg.startswith("--wizard-answers="):
            forward.append(arg)
    try:
        child = subprocess.Popen([str(exe), "--desktop", *forward], cwd=str(program_dir))
    except OSError as exc:  # noqa: BLE001 - a clear failure beats a silent skip
        logger.error(f"Could not start the manager process: {exc}")
        portable.notify_fatal(
            "Setup finished, but the manager could not be started. "
            "Please start Fingerprint Lite again manually."
        )
        raise SystemExit(3) from exc
    logger.info("First-run handoff: parent_pid={} child_pid={} exe={}", os.getpid(), child.pid, exe)
    raise SystemExit(0)


def _run_browser_command(args: argparse.Namespace) -> None:
    """``camoufox-pm browser install/status``: the manual door for the browser."""
    from camoufox_pm import browser_env

    try:
        if args.browser_command == "status":
            status = browser_env.browser_status()
            print(f"pinned version : {status.version} (sha256 {status.sha256[:12]}…)")
            print(f"cache folder   : {status.cache_dir}")
            print(f"installed      : {'yes' if status.installed else 'no'}")
            if status.path is not None:
                print(f"install path   : {status.path}")
            if status.download_failed:
                print("download       : failed earlier this session; use --download to retry")
            return

        if args.zip:
            path = browser_env.install_from_zip(Path(args.zip), replace=args.replace)
        elif args.download:
            path = browser_env.install_from_download(replace=args.replace)
        else:
            path = browser_env.ensure_browser(allow_download=True, replace=args.replace)
        print(f"Camoufox {browser_env.pin_version_string()} ready at {path}")
    except browser_env.BrowserInstallError as exc:
        _fail(str(exc))


def _run_data_command(args: argparse.Namespace, ctx) -> None:
    """``camoufox-pm data migrate``: move Data, keep the old copy as the rollback."""
    from camoufox_pm import portable

    if ctx is None:
        _fail("Data migration is for portable mode: run the packaged program or use --portable.")
    if args.data_command != "migrate":
        _fail(f"Unknown data command: {args.data_command}")

    target = Path(args.target)
    if not args.yes:
        answer = input(f"Copy data from {ctx.data_dir} to {target}? The old copy is kept. [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            print("Cancelled; nothing moved.")
            return
    try:
        backup = portable.migrate_data(ctx, target)
    except Exception as exc:  # noqa: BLE001 - whatever went wrong, say it and stop
        _fail(str(exc))
    print(f"Data moved to {target}. The previous copy is kept at {backup}.")


def _read_password(args: argparse.Namespace) -> str:
    """Collect a password without it ever appearing in argv or the environment."""
    from camoufox_pm.core.auth import MIN_PASSWORD_LENGTH

    if args.password_stdin:
        password = sys.stdin.readline().rstrip("\n")
    else:
        password = getpass.getpass("Password: ")
        if getpass.getpass("Repeat password: ") != password:
            _fail("Passwords do not match.")
    if len(password) < MIN_PASSWORD_LENGTH:
        _fail(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    return password


async def _run_unlock_command(args: argparse.Namespace) -> None:
    """Force-release one profile's lease; shell-only (see the parser)."""
    storage = StorageManager(get_settings().db_path)
    await storage.initialize()
    try:
        lease = await storage.get_lease(args.profile_id)
        if lease is None:
            _fail(f"No profile with ID '{args.profile_id}'.")
        holder, expires = lease
        if holder is None:
            print(f"Profile {args.profile_id} is not leased.")
            return
        state = "expired" if lease_expired(expires) else "live"
        print(
            f"Profile {args.profile_id} is leased by {holder}"
            + (f" until {expires} UTC ({state})" if expires else " (no expiry)")
        )
        if not args.yes and input("Force-release this lease? (yes/no): ").strip().lower() not in (
            "yes",
            "y",
        ):
            print("Cancelled; the lease stands.")
            return
        previous = await storage.force_release_lease(args.profile_id)
        print(f"Lease released (was held by {previous}).")
    finally:
        await storage.close()


async def _run_leases_command() -> None:
    """Show who holds what, so `unlock` is a decision and not a guess."""
    storage = StorageManager(get_settings().db_path)
    await storage.initialize()
    try:
        holders = await storage.get_lease_holders()
        if not holders:
            print("No profiles are leased.")
            return
        for entry in holders:
            state = "expired" if entry["expired"] else "live"
            print(
                f"{entry['id']}  {entry['name']}  {entry['locked_by']}  "
                f"expires {entry['lock_expires']} UTC ({state})"
            )
    finally:
        await storage.close()


async def _run_user_command(args: argparse.Namespace) -> None:
    # Imported here so plain `camoufox-pm` startup does not pay for them.
    from camoufox_pm.core import auth
    from camoufox_pm.core.database import StorageManager

    storage = StorageManager(get_settings().db_path)
    await storage.initialize()
    try:
        if args.user_command == "add":
            password = _read_password(args)
            try:
                await storage.create_user(
                    auth.new_user_id(), args.username, auth.hash_password(password)
                )
            except ValueError as exc:
                _fail(str(exc))
            print(
                f"User '{args.username}' created. The API and web UI now require "
                "a login session (or the API key, if CPM_API_KEY is set)."
            )
        elif args.user_command == "passwd":
            password = _read_password(args)
            if not await storage.update_user_password(args.username, auth.hash_password(password)):
                _fail(f"No user named '{args.username}'.")
            print(f"Password updated for '{args.username}'.")
        elif args.user_command == "remove":
            if not await storage.delete_user(args.username):
                _fail(f"No user named '{args.username}'.")
            print(f"User '{args.username}' removed, along with any open sessions.")
            if await storage.count_users() == 0:
                print(
                    "No users remain: login is now disabled and the API is back to its API-key/open behaviour."
                )
        elif args.user_command == "list":
            users = await storage.list_users()
            if not users:
                print("No users. Create one with: camoufox-pm user add <name>")
            for user in users:
                print(f"{user['username']}\t(created {user['created_at']})")
    finally:
        await storage.close()


if __name__ == "__main__":
    main()
