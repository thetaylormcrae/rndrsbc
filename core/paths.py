"""
rndrSBC - Canonical runtime path resolution & self-bootstrapping.

Makes the whole platform RELOCATABLE: every on-disk path (config, live
preview, data/ photos, claim token) is anchored to the package location
rather than the process working directory. Combined with the vendored
dependency bootstrap below, this is what lets you "copy the folder onto a
Raspberry Pi and just run `python3 main.py`" with no install.sh.

The dependency bootstrap supports two strategies, checked in order:
  1. VENDORED WHEELS (fully offline): a ``vendor/deps/`` folder whose
     wheel files are added to ``sys.path`` like a site-packages bundle.
  2. PRIVATE VENV (self-provisioning): if ``.venv/`` exists next to the
     package, its site-packages are added to ``sys.path``.
If neither exists, the installer/venv creation path in ``bootstrap()``
is used on first run.
"""

import os
import sys


# ---------------------------------------------------------------------------
# Deployment root (RNDRSBC_HOME) vs package root.
#
# The platform supports TWO install modes backed by one codebase:
#
#   * SELF-CONTAINED  (default) - the copied ``rndrSBC/`` folder holds BOTH
#     the code and its writable state (config, data/, live preview, claim
#     token). ROOT == DEPLOY_ROOT.
#
#   * PYPI / SITE-INSTALLED   - the code lives in site-packages (read-only);
#     all writable runtime state is re-anchored into a deployment home the
#     user owns, supplied via the RNDRSBC_HOME env var (or --home flag on
#     the entrypoint). Code and state are cleanly separated, so
#     ``pip install -U rndrsbc`` never touches user data.
#
# RNDRSBC_HOME may be a colon-separated list: the FIRST existing entry wins,
# else the last is created.
# ---------------------------------------------------------------------------

# Package root: immutable, computed from this file's location.
#   core/paths.py  ->  <site-packages>/rndrsbc/core/paths.py
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _deployment_root() -> str:
    """Resolve the writable deployment home.

    Priority: $RNDRSBC_HOME (first existing component; else last is created)
    > package ROOT (self-contained mode).
    """
    override = os.environ.get("RNDRSBC_HOME", "").strip()
    if override:
        candidates = [os.path.expanduser(c) for c in override.split(os.pathsep)]
        for c in candidates:
            if os.path.isdir(c):
                return c
        # None exists yet -> create the last/most-specific candidate.
        return candidates[-1]
    return ROOT


DEPLOY_ROOT = _deployment_root()

# Runtime (writable) state - anchored to the deployment home.
DATA_DIR = os.path.join(DEPLOY_ROOT, "data")
CONFIG_PATH = os.path.join(DEPLOY_ROOT, "config.json")
CLAIM_TOKEN_PATH = os.path.join(DEPLOY_ROOT, ".claim_token")
REGISTRY_DIR = os.path.join(DEPLOY_ROOT, "registry")
PLUGIN_DIR = os.path.join(DEPLOY_ROOT, "plugins")

# SecretsStore home - holds OAuth tokens / provider credentials (0600 files).
SECRETS_DIR = os.path.join(DEPLOY_ROOT, "secrets")


def secrets_path() -> str:
    """Return the canonical secrets-store path, ensuring its parent exists."""
    os.makedirs(SECRETS_DIR, exist_ok=True)
    return os.path.join(SECRETS_DIR, "secrets.json")

# Package (immutable) resources.
VENDOR_DEPS_DIR = os.path.join(ROOT, "vendor", "deps")
VENV_DIR = os.path.join(ROOT, ".venv")
FONTS_DIR = os.path.join(ROOT, "assets", "fonts")


def resolve(*parts) -> str:
    """Join a path relative to the DEPLOYMENT root (writable state).

    ``resolve("live_screen.png")`` -> ``<deploy>/live_screen.png``. For the
    self-contained layout DEPLOY_ROOT == ROOT, so this is backward-
    compatible. Use :func:`package_path` for bundled (read-only) resources.
    """
    return os.path.join(DEPLOY_ROOT, *parts)


def package_path(*parts) -> str:
    """Join a path relative to the PACKAGE root (bundled, read-only)."""
    return os.path.join(ROOT, *parts)


def ensure_data_dir():
    """Ensure the deployment runtime data directory exists."""
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(REGISTRY_DIR, exist_ok=True)
    os.makedirs(PLUGIN_DIR, exist_ok=True)
    return DATA_DIR


# ---------------------------------------------------------------------------
# Dependency bootstrap
# ---------------------------------------------------------------------------

def _venv_site_packages():
    """Path to site-packages inside the private .venv, if present."""
    if not os.path.isdir(VENV_DIR):
        return None
    sp = os.path.join(VENV_DIR, "lib")
    for py in os.listdir(sp) if os.path.isdir(sp) else []:
        candidate = os.path.join(sp, py, "site-packages")
        if os.path.isdir(candidate):
            return candidate
    return None


def bootstrap_deps() -> bool:
    """Make third-party dependencies importable.

    Priority: vendored wheels > private venv. Returns True if a dep source
    was found and added to sys.path, False if we must rely on system/global
    packages.

    NOTE: compiled wheels (Pillow, MarkupSafe, etc.) CANNOT be imported from
    a .whl zip directly (zipimport can't load C extensions), so wheels are
    extracted into ``vendor/deps/_extracted/`` which then acts as a flat
    site-packages directory. Pure-python wheels would work either way.
    """
    # 1. Vendored wheels: extract (once) into a flat site-packages dir.
    if os.path.isdir(VENDOR_DEPS_DIR):
        wheels = [
            os.path.join(VENDOR_DEPS_DIR, f)
            for f in os.listdir(VENDOR_DEPS_DIR)
            if f.endswith(".whl")
        ]
        if wheels:
            extracted = _ensure_wheels_extracted(wheels)
            sys.path.insert(0, extracted)
            return True

    # 2. Private venv site-packages.
    sp = _venv_site_packages()
    if sp:
        sys.path.insert(0, sp)
        return True

    return False


def _ensure_wheels_extracted(wheels: list) -> str:
    """Extract vendored wheels into ``vendor/deps/_extracted/`` if needed.

    Returns the extracted directory path. Extraction is fast and idempotent:
    the dirs are flushed and rebuilt whenever the wheel set changes.
    """
    import zipfile as _zipfile
    target = os.path.join(VENDOR_DEPS_DIR, "_extracted")
    marker = os.path.join(target, ".digest")
    digest = "\x00".join(sorted(os.path.basename(w) for w in wheels))

    if os.path.isfile(marker) and open(marker).read() == digest:
        return target

    # Rebuild cleanly: flush previous extraction.
    import shutil
    if os.path.isdir(target):
        shutil.rmtree(target)
    os.makedirs(target, exist_ok=True)
    for w in wheels:
        with _zipfile.ZipFile(w) as z:
            z.extractall(target)
    with open(marker, "w") as f:
        f.write(digest)
    return target


def create_venv(requirements: str = "requirements.txt") -> bool:
    """Create a private .venv (if missing) and install requirements.

    Used only when vendored wheels are absent. Returns True on success.
    """
    import subprocess
    if os.path.isdir(os.path.join(VENV_DIR, "bin")):
        return True

    req = os.path.join(ROOT, requirements)
    try:
        subprocess.check_call([sys.executable, "-m", "venv", VENV_DIR])
        py = os.path.join(VENV_DIR, "bin", "python")
        cmd = [py, "-m", "pip", "install", "-r", req]
        subprocess.check_call(cmd)
        return True
    except Exception:
        return False


__all__ = [
    "ROOT", "DEPLOY_ROOT", "DATA_DIR", "CONFIG_PATH", "CLAIM_TOKEN_PATH",
    "REGISTRY_DIR", "PLUGIN_DIR", "VENDOR_DEPS_DIR", "VENV_DIR", "FONTS_DIR",
    "resolve", "package_path", "ensure_data_dir",
    "bootstrap_deps", "create_venv",
    "SECRETS_DIR", "secrets_path",
]
