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
