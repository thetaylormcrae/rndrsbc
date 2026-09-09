"""rndrSBC - Config model: the single write path for config.json.

Every mutation goes through :func:`update_config`, which:

  1. Loads the current on-disk config.
  2. Deep-merges the submitted partial (missing keys are preserved, never
     stripped - this was the root cause of the old dashboard's
     "playlist config wiped on save" bug).
  3. Protects credential-bearing keys from being overwritten or removed.
  4. Validates the merged result via core.config_schema.validate_config.
  5. Persists atomically and hot-applies to a running scheduler.
"""
from __future__ import annotations

import copy
import json
import logging
import os
from typing import Any

from core.config_schema import validate_config, ConfigError
from core.migrations import migrate
from .security import load_config, save_config

logger = logging.getLogger("rndrSBC.web.model")

# Keys that must never be removed or overwritten by a web save.
PROTECTED_KEYS = ("admin_password_hash",)

# Keys that, if absent from a partial save, are carried over verbatim.
PRESERVE_ON_ABSENT = ("active_playlist", "playlists", "schema_version", "admin_sessions")


class ConfigUpdateError(Exception):
    pass


def normalize_display_dims(cfg: dict) -> None:
    """Fill display width/height from the authoritative model map (hot-resize)."""
    d = cfg.get("display")
    if not isinstance(d, dict):
        return
    w = d.get("width") or d.get("screen_width")
    h = d.get("height") or d.get("screen_height")
    model = d.get("model")
    if model:
        try:
            from displays.waveshare import DISPLAY_MODELS as _mods
            md = _mods.get(model) or {}
            w = w or md.get("width")
            h = h or md.get("height")
            if "color_mode" not in d and md.get("color_mode"):
                d["color_mode"] = md.get("color_mode")
        except ImportError:
            pass
    d["width"] = int(w) if w else None
    d["height"] = int(h) if h else None
    if not d.get("width") or not d.get("height"):
        d.pop("width", None)
        d.pop("height", None)


def sanitize_outbound(cfg: dict) -> dict:
    """Strip credential-bearing keys before returning config to the client."""
    out = copy.deepcopy(cfg)
    for k in PROTECTED_KEYS:
        out.pop(k, None)
    return out


def update_config(partial: dict, scheduler=None) -> tuple[dict, list[str]]:
    """Merge ``partial`` into the on-disk config, validate, persist, hot-apply.

    Returns (merged_config, warnings). Raises ConfigUpdateError on validation
    failure or I/O error.
    """
    if not isinstance(partial, dict):
        raise ConfigUpdateError("config payload must be a JSON object")

    current = load_config()
    merged = copy.deepcopy(current)

    for key, value in partial.items():
        if key in PROTECTED_KEYS:
            continue  # never accept a credential key from a web save
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(copy.deepcopy(value))
        else:
            merged[key] = copy.deepcopy(value)

    # Preserve keys the client omitted (naive client must not wipe playlists).
    for k in PRESERVE_ON_ABSENT:
        if k not in partial and k in current:
            merged[k] = current[k]

    normalize_display_dims(merged)

    try:
        validated, warnings = validate_config(merged, self_heal=True)
    except ConfigError as e:
        raise ConfigUpdateError(str(e)) from e

    # Run schema migrations so the on-disk shape stays forward-compatible.
    migrated = migrate(validated)

    try:
        save_config(migrated)
    except OSError as e:
        raise ConfigUpdateError(f"Could not write config: {e}") from e

    if scheduler is not None:
        try:
            scheduler.update_config(migrated)
        except Exception:  # noqa: BLE001
            logger.exception("scheduler hot-apply failed; config saved anyway")

    return migrated, warnings


def replace_playlists(playlists: dict, active_playlist: str | None = None, scheduler=None) -> tuple[dict, list[str]]:
    """Whole-playlist-tree replacement (the playlists page's Save & Apply)."""
    partial: dict[str, Any] = {"playlists": playlists}
    if active_playlist is not None:
        partial["active_playlist"] = active_playlist
    return update_config(partial, scheduler=scheduler)
