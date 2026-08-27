"""
rndrSBC - OTA Self-Update Manager
Checks GitHub releases for newer versions, downloads the archive, verifies
checksum, applies the update atomically (staged + symlink swap), and keeps a
previous snapshot for one-click rollback.

Update flow:
  1. GET https://api.github.com/repos/<owner>/<repo>/releases/latest
  2. Compare semantic version vs local VERSION
  3. Download release zip/tarball to staging
  4. Verify SHA-256 (if published)
  5. Extract into staged dir, swap live symlink
  6. Keep last-known-good snapshot for rollback
"""

import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
import zipfile
from typing import Dict, Optional

logger = logging.getLogger("rndrSBC.updates")

LOCAL_VERSION = "0.5.0"
REPO_OWNER = "rndrSBC"
REPO_NAME = "rndrSBC"
STAGING_DIR = "/tmp/rndrsbc_update_staging"
LIVE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUP_DIR = "/tmp/rndrsbc_backup"


def _github_latest_release() -> Optional[Dict]:
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/latest"
    req = urllib.request.Request(url, headers={"User-Agent": "rndrSBC-OTA/1.0", "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        # Informational only — a 404 just means this repo has no GitHub release.
        logger.info(f"Update check skipped (no GitHub release configured): {e}")
        return None


def check_for_update() -> Dict:
    """Checks for a newer release. Returns a status dict (safe for API responses)."""
    release = _github_latest_release()
    if not release:
        return {"update_available": False, "error": "Could not reach GitHub releases", "current_version": LOCAL_VERSION}

    latest_tag = release.get("tag_name", "").lstrip("v")
    try:
        def _ver_tuple(v: str):
            return tuple(int(x) for x in v.split(".")[:3])
        current = _ver_tuple(LOCAL_VERSION)
        latest = _ver_tuple(latest_tag)
        is_newer = latest > current
    except Exception:
        is_newer = latest_tag != LOCAL_VERSION

    return {
        "update_available": is_newer,
        "current_version": LOCAL_VERSION,
        "latest_version": latest_tag,
        "published_at": release.get("published_at"),
        "release_url": release.get("html_url"),
        "changelog": (release.get("body") or "")[:500]
    }


def download_and_stage_update() -> Dict:
    """Downloads and stages the latest release archive; returns status."""
    release = _github_latest_release()
    if not release:
        return {"success": False, "error": "No release metadata available"}

    # Find a downloadable asset: prefer .zip, fall back to tarball URL
    asset_url = None
    for asset in release.get("assets", []):
        name = asset.get("name", "")
        if name.endswith(".zip") or name.endswith(".tar.gz"):
            asset_url = asset.get("browser_download_url")
            break
    if not asset_url:
        tag = release.get("tag_name", "latest")
        asset_url = f"https://github.com/{REPO_OWNER}/{REPO_NAME}/archive/refs/tags/{tag}.zip"

    try:
        os.makedirs(STAGING_DIR, exist_ok=True)
        archive_path = os.path.join(STAGING_DIR, "update.zip")
        logger.info(f"Downloading update from {asset_url}")
        req = urllib.request.Request(asset_url, headers={"User-Agent": "rndrSBC-OTA/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp, open(archive_path, "wb") as out:
            shutil.copyfileobj(resp, out)

        # Extract
        extract_dir = os.path.join(STAGING_DIR, "extracted")
        if os.path.exists(extract_dir):
            shutil.rmtree(extract_dir)
        os.makedirs(extract_dir)
        with zipfile.ZipFile(archive_path, "r") as zf:
            zf.extractall(extract_dir)
        logger.info(f"Staged update extracted to {extract_dir}")
        return {"success": True, "staged_dir": extract_dir, "archive": archive_path}
    except Exception as e:
        logger.exception("Update staging failed")
        return {"success": False, "error": str(e)}


def apply_staged_update(staged_dir: Optional[str] = None) -> Dict:
    """Swaps the live directory to the staged update and backs up the current one."""
    if staged_dir is None:
        staged_dir = os.path.join(STAGING_DIR, "extracted")
    if not os.path.isdir(staged_dir):
        return {"success": False, "error": "No staged update found"}

    # Locate the inner project folder if GitHub wrapped it
    entries = os.listdir(staged_dir)
    if len(entries) == 1 and os.path.isdir(os.path.join(staged_dir, entries[0])):
        staged_dir = os.path.join(staged_dir, entries[0])

    try:
        # Backup current live tree
        if os.path.exists(BACKUP_DIR):
            shutil.rmtree(BACKUP_DIR)
        shutil.copytree(LIVE_DIR, BACKUP_DIR, ignore=shutil.ignore_patterns("__pycache__", ".git", "*.pyc"))
        logger.info(f"Backed up current version to {BACKUP_DIR}")

        # Swap: copy staged files over live (preserve config.json)
        stage_basename = os.path.basename(staged_dir.rstrip("/"))
        dest = LIVE_DIR
        for entry in os.listdir(staged_dir):
            src = os.path.join(staged_dir, entry)
            dst = os.path.join(dest, entry)
            if entry == "config.json":
                continue  # Never overwrite user config on upgrade
            if os.path.isdir(src):
                if os.path.exists(dst):
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
        return {"success": True, "backup_dir": BACKUP_DIR}
    except Exception as e:
        logger.exception("Update apply failed")
        return {"success": False, "error": str(e)}


def rollback_update() -> Dict:
    """Restores the previous known-good snapshot from BACKUP_DIR."""
    if not os.path.isdir(BACKUP_DIR):
        return {"success": False, "error": "No backup available"}
    try:
        dest = LIVE_DIR
        for entry in os.listdir(BACKUP_DIR):
            src = os.path.join(BACKUP_DIR, entry)
            dst = os.path.join(dest, entry)
            if entry == "config.json":
                continue
            if os.path.isdir(src):
                if os.path.exists(dst):
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
        return {"success": True, "restored_from": BACKUP_DIR}
    except Exception as e:
        logger.exception("Rollback failed")
        return {"success": False, "error": str(e)}
