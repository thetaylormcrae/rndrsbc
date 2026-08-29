"""Registry / install subcommands for the ``rndrsbc`` console script."""

import os
import sys

# Ensure top-level packages importable from a wheel install.
_pkg = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for cand in (_pkg, os.path.dirname(_pkg)):
    if cand not in sys.path:
        sys.path.insert(0, cand)


def _registry():
    from core import registry
    return registry


def _pop_catalog_flag(argv):
    """Extract a leading ``--catalog <url>`` pair if present."""
    argv = list(argv)
    for flag in ("--catalog", "-c"):
        if flag in argv:
            i = argv.index(flag)
            if i + 1 < len(argv):
                url = argv.pop(i + 1)
                argv.pop(i)
                return url, argv
    return None, argv


def cmd_search(argv, catalog_url=None):
    from core import registry
    catalog_url, argv = _pop_catalog_flag(argv)
    # Interactive search: always hit the network so the user sees current widgets.
    catalog = registry.fetch_catalog(catalog_url, refresh=True)
    q = " ".join(argv).lower()
    rows = [w for w in catalog["widgets"] if q in w["id"].lower() or q in w.get("name","").lower()]
    if not rows:
        print(f"No widgets found matching '{q}'.")
        return 1
    print(f"{len(rows)} widget(s):")
    for w in rows:
        print(f"  {w['id']:<28} v{w.get('version','?')}  {w.get('summary','')[:64]}")
    return 0


def cmd_install(argv, catalog_url=None):
    from core import registry
    if not argv:
        print("usage: rndrsbc install <widget-id> [--force] [--catalog <url>]")
        return 2
    catalog_url, argv = _pop_catalog_flag(argv)
    wid = argv[0]
    force = "--force" in argv
    catalog = registry.fetch_catalog(catalog_url, refresh=True)
    entry = registry.find(catalog, wid)
    if not entry:
        print(f"Widget '{wid}' not found in registry.")
        return 1
    dest = registry.install(entry, force=force)
    print(f"Installed '{wid}' -> {dest}")
    return 0


def cmd_remove(argv):
    from core import registry
    if not argv:
        print("usage: rndrsbc remove <widget-id>")
        return 2
    if registry.uninstall(argv[0]):
        print(f"Removed widget '{argv[0]}'.")
        return 0
    print(f"Widget '{argv[0]}' is not installed.")
    return 1


def cmd_list(argv):
    from core import registry
    installed = registry.list_installed()
    if not installed:
        print("No community widgets installed.")
        return 0
    print("Installed community widgets:")
    for i in installed:
        print(f"  {i}")
    return 0


def main(argv):
    if not argv:
        print(__doc__)
        return 0
    cmd = argv[0]
    rest = argv[1:]
    if cmd == "search":
        return cmd_search(rest)
    if cmd == "install":
        return cmd_install(rest)
    if cmd == "remove":
        return cmd_remove(rest)
    if cmd in ("list", "ls"):
        return cmd_list(rest)
    print(f"Unknown command: {cmd}")
    return 2
