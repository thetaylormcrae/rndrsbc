"""Self-update for the rndrsbc engine.

`rndrsbc update self` upgrades the installed engine in place via pip, then
re-runs the post-upgrade migrations/bootstrap so a running deployment comes
up clean. Deliberately small and shell-out based: pip is the single source of
truth for the engine's own upgrade path (the same mechanism the operator uses
for a fresh install, so there is only one code path to reason about).

Modes
-----
--check      Query the registry/PyPI for a newer version; print and exit.
             Network-free fallback: compare against what's already available.
--dry-run    Resolve what would change without installing.
(no flags)   Upgrade in place and re-bootstrap.
"""

import json
import os
import subprocess
import sys

from core import paths


def _current_version() -> str:
    import core
    return getattr(core, "__version__", "0.0.0")


def _pip(*args: str) -> int:
    """Run pip reliably whether we are inside a venv or a system install."""
    base = [sys.executable, "-m", "pip"]
    # `--user` and venvs conflict; suppress the global fallback that confuses it.
    return subprocess.call([*base, *args])


def _available_version(reg_url: str | None = None) -> str | None:
    """Ask the registry feed for the newest published engine version."""
    from core import registry
    try:
        # Interactive update check: force a refresh so it reflects newly-published releases.
        feed = registry.fetch_catalog(reg_url, refresh=True)
        eng = next((e for e in feed.get("engine", []) if e.get("package") == "rndrsbc"), None)
        if eng and eng.get("version"):
            return str(eng["version"])
        return None
    except Exception:  # noqa: BLE001 - offline / unreachable
        return None


def status(quiet: bool = False) -> dict:
    """Return a machine-readable status dict (single source for CLI + dashboard).

    Keys:
      current_version  installed engine version (core.__version__)
      latest_version   newest published version from the registry feed, or None
      update_available True when latest > current
      error            set when the registry feed could not be reached
    """
    cur = _current_version()
    avail = _available_version()
    out = {
        "current_version": cur,
        "latest_version": avail,
        "update_available": bool(avail) and avail > cur,
        "error": None,
    }
    if avail is None:
        out["error"] = "could not query feed — offline?"
        if not quiet:
            print(f"rndrsbc {cur} ({out['error']})")
    return out


def check(quiet: bool = False) -> int:
    """Return an exit code: 0 = up to date, 1 = update available, 2 = unknown."""
    st = status(quiet=quiet)
    cur, avail = st["current_version"], st["latest_version"]
    if avail is None:
        return 2
    if avail > cur:
        if not quiet:
            print(f"update available: {cur} -> {avail}")
        return 1
    if not quiet:
        print(f"rndrsbc {cur} is up to date")
    return 0


def apply(dry_run: bool = False) -> int:
    cur = _current_version()
    avail = _available_version()
    print(f"rndrsbc {cur} -> {'(dry-run) resolve to' if dry_run else 'upgrading to'} "
          f"{avail or 'latest'}")

    if dry_run:
        print(f"[dry-run] would run: {sys.executable} -m pip install --upgrade rndrsbc")
        return 0

    # --no-cache-dir: pip's HTTP index cache can serve stale metadata ("0.10.0 is
    # newest") right after a release lands, so the feed says 0.10.1 exists but pip
    # says "Requirement already satisfied". Bypass the cache so the version check
    # always hits PyPI fresh.
    rc = _pip("install", "--upgrade", "--no-cache-dir", "rndrsbc")
    if rc != 0:
        print("update failed", file=sys.stderr)
        return rc

    # Verify the install actually moved: an upgrade that "succeeded" but left the
    # old version behind (e.g. stale index metadata after a fresh release) is a
    # silent no-op that looks like success. Be loud when it happens.
    after = _current_version()
    if avail and after < avail:
        print(
            f"warning: still on {after}, wanted {avail} — pip resolved the index but\n"
            "did not pick up the new release. This is usually PyPI CDN propagation\n"
            "lag right after publish. Retry in a minute, or force it with:\n"
            f"  {sys.executable} -m pip install --force-reinstall --no-cache-dir rndrsbc",
            file=sys.stderr,
        )
        return 1

    # post-upgrade: refresh vendored deps + any migrations
    print("post-update bootstrap…")
    try:
        paths.bootstrap_deps()
    except Exception as exc:  # noqa: BLE001
        print(f"bootstrap warning: {exc}", file=sys.stderr)
    return 0


def main(argv: list[str]) -> int:
    args = [a for a in argv if a not in ("update", "self")]
    fn, flags = apply, []
    for a in args:
        if a in ("--check", "--dry-run"):
            flags.append(a)
    if "--check" in flags:
        return check()
    return apply(dry_run="--dry-run" in flags)
