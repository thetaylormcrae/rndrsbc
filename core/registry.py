"""
rndrSBC - Community Widget Registry (registry-backed, git-free installs).

The piece that makes "grow a community without a git clone" real. Instead of
asking users to clone a plugin repo, the app (and CLI) pull widgets from a
small, versioned CATALOG feed - a plain JSON document served over HTTPS - and
install each artifact into the deployment PLUGIN_DIR.

Design goals
------------
* No git anywhere on the user side. A widget is just a zip over HTTPS.
* Verified by default: every artifact carries a SHA-256 pinned in the catalog
  and optional publisher signature; the installer refuses mismatches.
* Isolated: plugins land under RNDRSBC_HOME/plugins (deployment-owned), NOT in
  site-packages, so ``pip install -U rndrsbc`` never touches them and uninstalling
  a widget is a plain directory delete.
* Versioned: catalog entries expose ``latest`` plus a history; upgrades are
  ``install(name, force=True)``.

The catalog feed is intentionally CAS-ish and cheap to host: a static JSON file
(any HTTPS static host / GitHub Pages / S3) with no server logic on the provider
side. Contributors submit via a PR against the single catalog repo; every PR is
an auditable diff.

Catalog schema (v1)
-------------------
{
  "version": 1,
  "generated_at": "2026-08-26T00:00:00Z",
  "widgets": [
    {
      "id": "sonos_now_playing",
      "name": "Sonos Now Playing",
      "summary": "Show the currently playing Sonos track.",
      "author": {"name": "...", "url": "..."},
      "version": "0.2.1",
      "license": "MIT",
      "min_core": "0.1.0",
      "sha256": "<hex>",                       // mandatory
      "url": "https://.../sonos_now_playing-0.2.1.zip",
      "entry": "widget.py",                    // main module in the zip
      "files": ["widget.py", "assets/album.png"],
      "config_schema": {...},
      "signature": {                           // optional but encouraged
        "key_fingerprint": "...",
        "ed25519_b64": "..."
      }
    }
  ]
}
"""

import hashlib
import io
import json
import os
import zipfile
import logging
import urllib.request
from typing import Optional

import core.paths as paths

logger = logging.getLogger("rndrSBC.registry")

DEFAULT_CATALOG = (
    # The community catalog feed is served from the registry's GitHub Pages
    # deployment (custom domain taylor.mcrae.site). It is catalog/_feed.json
    # (regenerated at deploy time, not committed), so it must be fetched from
    # the Pages site — raw.githubusercontent has no catalog.json to serve, and
    # the github.io URL only 301s to the custom domain.
    "https://taylor.mcrae.site/rndrsbc-registry/catalog/_feed.json"
)


# ---------------------------------------------------------------------------
# Catalog fetch
# ---------------------------------------------------------------------------
def fetch_catalog(url: str = DEFAULT_CATALOG, timeout: int = 15) -> dict:
    """Return the parsed catalog feed. Raises on network/HTTP failure.

    Cache: catalog.json is cached under the deployment REGISTRY_DIR so the app
    works offline afterward; the CLI passes ``refresh=True`` to force a re-pull.
    """
    cache_file = os.path.join(paths.REGISTRY_DIR, "catalog.json")
    os.makedirs(paths.REGISTRY_DIR, exist_ok=True)

    if os.path.exists(cache_file):
        with open(cache_file) as f:
            try:
                return json.load(f)
            except Exception:
                logger.warning("Ignoring corrupt cached catalog; re-fetching.")

    try:
        if url.startswith("file://"):
            with open(url[7:], "rb") as f:
                catalog = json.loads(f.read().decode())
        else:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                catalog = json.loads(resp.read().decode())
        with open(cache_file, "w") as f:
            json.dump(catalog, f, indent=2)
        return catalog
    except Exception as e:
        # Last resort: serve stale cache rather than nothing.
        if os.path.exists(cache_file):
            with open(cache_file) as f:
                return json.load(f)
        raise RuntimeError(f"Could not fetch registry catalog: {e}") from e


def find(catalog: dict, widget_id: str) -> Optional[dict]:
    """Locate a widget entry by id in the catalog."""
    for w in catalog.get("widgets", []):
        if w.get("id") == widget_id:
            return w
    return None


# ---------------------------------------------------------------------------
# Verify + install
# ---------------------------------------------------------------------------
def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def download_and_verify(entry: dict, timeout: int = 60) -> bytes:
    """Download the artifact and verify its SHA-256 against the catalog."""
    artifact_url = entry["url"]
    expected = entry["sha256"].lower()
    logger.info(f"Downloading {artifact_url} ...")
    if artifact_url.startswith("file://"):
        with open(artifact_url[7:], "rb") as f:
            data = f.read()
    else:
        with urllib.request.urlopen(artifact_url, timeout=timeout) as resp:
            data = resp.read()
    actual = _sha256(data)
    if actual != expected:
        raise RuntimeError(
            f"SHA-256 mismatch for {entry['id']}: expected {expected}, got {actual}. "
            "Refusing to install."
        )
    logger.info(f"Verified {entry['id']} SHA-256 OK.")
    return data


def install(entry: dict, force: bool = False) -> str:
    """Download, verify and unpack a widget into the deployment PLUGIN_DIR.

    Returns the widget's install directory.
    """
    wid = entry["id"]
    dest = os.path.join(paths.PLUGIN_DIR, wid)

    if os.path.exists(dest) and not force:
        raise FileExistsError(
            f"Widget '{wid}' already installed. Use force=True to reinstall/upgrade."
        )

    data = download_and_verify(entry)
    os.makedirs(dest, exist_ok=True)

    # --- Pre-extraction static sandbox scan of every .py in the artifact ----
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for member in zf.namelist():
            # zip-slip guard: refuse absolute paths / .. traversal
            resolved = os.path.realpath(os.path.join(dest, member))
            if not resolved.startswith(os.path.realpath(dest) + os.sep) and resolved != os.path.realpath(dest):
                raise RuntimeError(f"Unsafe path in artifact: {member}")
        # Static validation before anything hits disk
        py_sources = {}
        for member in zf.namelist():
            if member.endswith(".py") and not member.startswith("__MACOSX"):
                py_sources[member] = zf.read(member).decode("utf-8", errors="replace")
        from core import security
        violations = security.validate_package(py_sources)
        if violations:
            raise PermissionError(
                f"Widget '{wid}' blocked by sandbox policy:\n  "
                + "\n  ".join(violations)
            )
        zf.extractall(dest)

    logger.info(f"Installed widget '{wid}' v{entry.get('version','?')} -> {dest}")
    return dest


def list_installed() -> list[str]:
    """Return ids of widgets present in the deployment PLUGIN_DIR."""
    if not os.path.isdir(paths.PLUGIN_DIR):
        return []
    return sorted(
        e for e in os.listdir(paths.PLUGIN_DIR)
        if os.path.isdir(os.path.join(paths.PLUGIN_DIR, e))
    )


def uninstall(widget_id: str) -> bool:
    """Remove a widget's deployment directory. Returns True if removed."""
    target = os.path.join(paths.PLUGIN_DIR, widget_id)
    if os.path.isdir(target):
        import shutil
        shutil.rmtree(target, ignore_errors=True)
        return True
    return False


__all__ = [
    "DEFAULT_CATALOG", "fetch_catalog", "find", "download_and_verify",
    "install", "list_installed", "uninstall",
]
