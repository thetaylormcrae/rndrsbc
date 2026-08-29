"""Schema migration runner for config.json.

The frame's config schema evolves over time (new settings, reorganised keys).
To guarantee zero-downtime upgrades we declare a `schema_version` in the config
and step it forward through an ordered list of migration functions.

Rules:
  - Every written config carries ``schema_version``.
  - On load, if a config's version is behind CURRENT, the incremental
    migrations run in order and the result is persisted.
  - Migrations are idempotent and never destructive: they add/normalise keys,
    never remove unknown user data without migrating it.
"""
from __future__ import annotations

import copy
import logging

logger = logging.getLogger("rndrSBC.migrations")

# Latest schema revision the codebase understands.
CURRENT_SCHEMA_VERSION = 2


def _v1_to_v2(cfg: dict) -> dict:
    """First real migration: introduce `display.rotate` and normalise
    `display.orientation` (int degrees) into the canonical bool-key pair,
    and backfill per-widget `responsive` on the active playlist."""
    cfg = copy.deepcopy(cfg)

    disp = cfg.setdefault("display", {})
    orient = disp.get("orientation", 0)
    if isinstance(orient, (int, float)):
        disp["rotation"] = int(orient) % 360
        disp["rotate"] = bool(int(orient) % 360)

    # Backfill responsive layout default for any composite grids.
    # Playlists come in two historical shapes: a mapping of name -> widget
    # list(s) (the current template: {"default": ["weather", ...]}) or the
    # older name -> {layout: {zones: ...}} dict form. Only the dict form has
    # a per-widget layout to backfill, and it may itself hold widget lists
    # under a "zones" key, so non-dict entries are skipped (never crash).
    for pl_name, pl in (cfg.get("playlists") or {}).items():
        if not isinstance(pl, dict):
            continue  # list-of-names playlist has no layout to migrate
        zones = (pl.get("layout") or {}).get("zones") or {}
        for z in zones.values():
            z.setdefault("responsive", "shrink")

    cfg["migrated_at"] = cfg.get("migrated_at") or None
    return cfg


MIGRATIONS = {
    # 1 -> 2 : from a config that predates schema_version
    2: _v1_to_v2,
}


def get_version(cfg: dict) -> int:
    v = cfg.get("schema_version")
    try:
        return int(v or 1)
    except (TypeError, ValueError):
        return 1


def migrate(cfg: dict) -> dict:
    """Return a migrated copy of ``cfg``, running all pending migrations."""
    version = get_version(cfg)
    if version >= CURRENT_SCHEMA_VERSION:
        return cfg
    out = copy.deepcopy(cfg)
    for target in sorted(int(k) for k in MIGRATIONS):
        if target <= version:
            continue
        out = MIGRATIONS[target](out) if callable(MIGRATIONS[target]) else out
        out["schema_version"] = target
        version = target
        logger.info(f"config migrated to schema_version={target}")
    out["schema_version"] = CURRENT_SCHEMA_VERSION
    return out


__all__ = ["migrate", "get_version", "CURRENT_SCHEMA_VERSION"]
