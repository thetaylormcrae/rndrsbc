"""``rndrsbc`` console-script entrypoint.

Primary: ``rndrsbc [port]`` runs the dashboard + scheduler (canonical
``main.py`` entry). Secondary: registry subcommands for community widgets::

    rndrsbc search <query>     search the community widget registry
    rndrsbc install <id>       install a community widget (verified, git-free)
    rndrsbc remove <id>        uninstall a community widget
    rndrsbc list               list installed community widgets

Diagnostics::

    rndrsbc doctor [--json]    run platform/dep/config/widget self-checks

Honours RNDRSBC_HOME (or ``--home <dir>``) so writable state is kept out of
site-packages: ``pip install -U rndrsbc`` upgrades code without touching user
data or community widgets.
"""

import os
import sys

# Top-level subcommands that do NOT boot the frame.
_REGISTRY_COMMANDS = ("search", "install", "remove", "list", "ls")
_SELF_COMMANDS = ("update", "upgrade", "version")
_AUTH_COMMANDS = ("reset-password", "set-password")


def _auth_command(argv) -> int | None:
    """Handle CLI password management subcommands."""
    if not argv or argv[0] not in _AUTH_COMMANDS:
        return None
    cmd = argv[0]
    from core.paths import CONFIG_PATH
    import json
    if not os.path.exists(CONFIG_PATH):
        print(f"Error: Config file not found at {CONFIG_PATH}")
        return 1

    if cmd == "reset-password":
        with open(CONFIG_PATH, "r") as f:
            cfg = json.load(f)
        if "admin_password_hash" in cfg:
            del cfg["admin_password_hash"]
            with open(CONFIG_PATH, "w") as f:
                json.dump(cfg, f, indent=2)
            print("Admin password cleared. First-run setup modal will now be active in the dashboard.")
        else:
            print("No admin password is currently set.")
        return 0

    if cmd == "set-password":
        from werkzeug.security import generate_password_hash
        import getpass
        pwd = argv[1] if len(argv) > 1 else None
        if not pwd:
            try:
                pwd = getpass.getpass("Enter new admin password (min 8 chars): ")
                confirm = getpass.getpass("Confirm new admin password: ")
                if pwd != confirm:
                    print("Error: Passwords do not match.")
                    return 1
            except (KeyboardInterrupt, EOFError):
                print("\nAborted.")
                return 1
        if len(pwd.strip()) < 8:
            print("Error: Password must be at least 8 characters long.")
            return 1

        with open(CONFIG_PATH, "r") as f:
            cfg = json.load(f)
        cfg["admin_password_hash"] = generate_password_hash(pwd.strip(), method="pbkdf2:sha256")
        with open(CONFIG_PATH, "w") as f:
            json.dump(cfg, f, indent=2)
        print("Admin password updated successfully.")
        return 0
    return None


def _self_command(argv) -> int | None:
    """Handle engine self-management subcommands; None if not one."""
    if argv and argv[0] in ("version", "--version", "-V"):
        from core import __version__
        print(f"rndrsbc {__version__}")
        return 0
    if argv and argv[0] in ("update", "upgrade"):
        from rndrsbc import _update
        return _update.main(argv)
    return None


def _set_home(argv):
    argv = list(argv)
    if "--home" in argv:
        i = argv.index("--home")
        if i + 1 < len(argv):
            os.environ["RNDRSBC_HOME"] = argv.pop(i + 1)
        argv.pop(i)
    return argv


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    argv = _set_home(argv)

    # Engine self-management (version / self-update).
    self_rc = _self_command(argv)
    if self_rc is not None:
        return self_rc

    # Password management subcommands.
    auth_rc = _auth_command(argv)
    if auth_rc is not None:
        return auth_rc

    # Pure diagnostic; doesn't boot the frame.
    if argv and argv[0] == "doctor":
        from rndrsbc import doctor
        return doctor.main(argv[1:])

    # Registry subcommands don't need the frame.
    if argv and argv[0] in _REGISTRY_COMMANDS:
        from rndrsbc import _registry_cli
        return _registry_cli.main(argv)

    # Frame mode: delegate to the canonical main.main().
    _pkg_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for cand in (_pkg_dir, os.path.dirname(_pkg_dir)):
        if cand not in sys.path:
            sys.path.insert(0, cand)

    sys.argv = [os.path.join(_pkg_dir, "main.py")] + argv

    from main import main as run
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
