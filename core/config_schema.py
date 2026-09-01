"""
Fail-fast configuration validation for rndrSBC.

Wiring level: the platform previously loaded ``config.json`` with ``json.load``
and read keys lazily via ``.get()`` — a typo'd value, a wrong type, or an
out-of-range value degraded *silently* at runtime instead of failing at boot.

Design rules (deliberate, tested):
  * PROVABLE defects  -> hard failure (ConfigError). These are defects we can
    prove break runtime: wrong type, invalid enum, out-of-range number, or a
    missing *required* key.
  * UNKNOWN/EXTRA keys -> *warning*, never a hard failure. The config is read
    lazily with ``.get()`` throughout, so an unrecognised key is harmless, and
    hard-failing on it would lock out legitimate future/legacy fields (e.g. the
    default config ships ``quiet_hours`` which predates this schema). This is
    the difference between fail-fast and fail-bricked.

Usage (fail-fast at boot):
    from core.config_schema import validate_config, ConfigError
    try:
        config, warnings = validate_config(raw)
    except ConfigError as e:
        logger.critical("config rejected: %s", e)
        sys.exit(1)
    for w in warnings:
        logger.warning("config: %s", w)
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

SUPPORTED_DRIVERS = {"auto", "virtual", "waveshare", "inky", "framebuffer"}
SUPPORTED_ORIENTATION = {0, 90, 180, 270}
REFRESH_MODES = {"auto", "full"}
_LANG_CODES = {"en","de","fr","es","it","nl","pt","pl","ru","ja","zh","ko"}
_REQUIRED_TOP = {"display": (dict,), "active_playlist": (str,), "playlists": (dict,)}


class ConfigError(Exception):
    """Raised when configuration is provably invalid. Message lists all problems."""


def validate_config(raw: Any, self_heal: bool = False) -> Tuple[Dict[str, Any], List[str]]:
    """Validate config; returns ``(config, warnings)`` or raises :class:`ConfigError`.

    Accepts a parsed dict or a JSON string. All *provable* problems are raised
    together in one message; harmless unknowns are returned as warnings.
    When ``self_heal=True`` is passed, missing required keys are populated
    from safe defaults and flagged as warnings rather than raising.
    """
    if isinstance(raw, (str, bytes)):
        raw = json.loads(raw)
    if not isinstance(raw, dict):
        raise ConfigError("config root must be a JSON object")

    problems: List[str] = []
    warnings: List[str] = []
    _known = {
        "display", "device", "active_playlist", "playlists", "_comment",
        "schema_version", "quiet_hours", "app", "rotation", "refresh_mode",
        "admin_password_hash", "migrated_at", "buttons", "wifi", "weather",
        "language", "secrets", "api_keys",
    }

    for key in raw:
        if key not in _known:
            warnings.append(f"{key}: unknown top-level key (ignored)")

    for key, (typ,) in _REQUIRED_TOP.items():
        if key not in raw:
            if self_heal:
                # Self-heal: a partial/corrupt/legacy on-disk config must never
                # hard-crash an unattended device at boot. Fill the missing required
                # key with a safe default and flag it so the operator can see why.
                raw[key] = _default_for(key)
                warnings.append(
                    f"{key}: required top-level key was missing - regenerated, "
                    f"consider re-saving config from the dashboard"
                )
            else:
                problems.append(f"{key}: required top-level key is missing")
        elif not isinstance(raw[key], typ):
            problems.append(f"{key}: expected {typ.__name__}")

    _validate_display(raw.get("display"), problems, warnings)
    _validate_device(raw.get("device"), problems, warnings)
    _validate_playlists(raw.get("playlists"), raw.get("active_playlist"), problems, warnings)

    if problems:
        raise ConfigError("; ".join(problems))
    return raw, warnings


def _default_for(key: str):
    """Safe value used to self-heal a missing required top-level key."""
    if key == "display":
        # Hardware-first, matching main.py's default and bootstrap's intent
        # (driver=auto). A virtual default silently abandons the physical
        # e-paper, so a self-healed config must resolve toward the real panel.
        return {"driver": "auto", "model": "impression_7_3", "orientation": 0}
    if key == "active_playlist":
        return "setup"
    if key == "playlists":
        # Onboarding-only default matching the packaged fresh config: a device
        # with a missing/partial config is by definition unclaimed, so pin it on
        # the QR setup tile until an owner configures it via the dashboard.
        return {
            "setup": {
                "name": "Setup",
                "items": [
                    {"widget": "onboarding", "duration_minutes": 999,
                     "settings": {}},
                ],
            }
        }
    return {}


def _validate_display(disp: Any, problems: List[str], warnings: List[str]) -> None:
    if disp is None or not isinstance(disp, dict):
        return
    drv = disp.get("driver")
    if drv is not None:
        if not isinstance(drv, str):
            problems.append("display.driver: expected string")
        elif drv not in SUPPORTED_DRIVERS:
            problems.append(f"display.driver:{drv!r} unsupported "
                            f"(expected {sorted(SUPPORTED_DRIVERS)})")
    o = disp.get("orientation")
    if o is not None:
        if isinstance(o, bool) or not isinstance(o, (int,)):
            problems.append(f"display.orientation: expected int, got {type(o).__name__}")
        elif o not in SUPPORTED_ORIENTATION:
            problems.append(f"display.orientation:{o!r} invalid (expected {sorted(SUPPORTED_ORIENTATION)})")
    rm = disp.get("refresh_mode")
    if rm is not None and rm not in REFRESH_MODES:
        problems.append(f"display.refresh_mode:{rm!r} invalid (expected {sorted(REFRESH_MODES)})")
    ph = disp.get("panel_health")
    if ph is not None:
        if not isinstance(ph, dict):
            problems.append("display.panel_health: expected object")
        else:
            for k in ("known_wear_units", "panel_age_years", "full_refresh_interval_min"):
                if k in ph and not isinstance(ph[k], (int, float)):
                    problems.append(f"display.panel_health.{k}: expected number")
    for k in disp:
        if k not in {"driver", "model", "orientation", "rotation", "rotate",
                     "refresh_mode", "panel_health", "width", "height",
                     "screen_width", "screen_height", "output_path",
                     "dither", "color_mode", "spi", "pins", "sleep",
                     "partial", "rot_sequence", "lut", "update_interval",
                     "h_flip", "v_flip", "pixel_pair_swap", "saturation"}:
            warnings.append(f"display.{k}: unknown key (ignored)")


def _validate_device(dev: Any, problems: List[str], warnings: List[str]) -> None:
    if dev is None or not isinstance(dev, dict):
        return
    nm = dev.get("name")
    if nm is not None and not isinstance(nm, str):
        problems.append("device.name: expected string")
    lang = dev.get("language")
    if lang is not None:
        if not isinstance(lang, str):
            problems.append("device.language: expected string")
        elif lang not in _LANG_CODES:
            warnings.append(f"device.language:{lang!r} not in known codes")
    for k in dev:
        if k not in {"name","language","timezone","model","hw","wlans","hostname"}:
            warnings.append(f"device.{k}: unknown key (ignored)")


def _validate_playlists(pls: Any, active: Any, problems: List[str],
                        warnings: List[str]) -> None:
    if pls is None or not isinstance(pls, dict):
        return
    for pname, items in pls.items():
        if isinstance(items, dict):
            if "items" in items:
                if not isinstance(items["items"], list):
                    problems.append(f"playlists.{pname}.items: expected array")
                else:
                    _validate_items(pname, items["items"], problems, warnings)
            cz = (items.get("layout") or {}).get("zones") or {}
            for z in cz.values():
                if not isinstance(z, dict):
                    warnings.append(f"playlists.{pname}.layout.zones: non-object zone")
        elif not isinstance(items, list):
            problems.append(f"playlists.{pname}: expected array or object")
        else:
            _validate_items(pname, items, problems, warnings)
    if isinstance(active, str) and isinstance(pls, dict) and pls and active not in pls:
        warnings.append(f"active_playlist:{active!r} does not match a known playlist")


def _validate_items(pname: str, items: List[Any], problems: List[str],
                    warnings: List[str]) -> None:
    for i, it in enumerate(items):
        if isinstance(it, str):
            continue  # simple widget name is fine
        if not isinstance(it, dict):
            warnings.append(f"playlists.{pname}.items[{i}]: expected widget object")
            continue
        w = it.get("widget")
        if w is not None and not isinstance(w, str):
            problems.append(f"playlists.{pname}.items[{i}].widget: expected string")
        dur = it.get("duration_minutes")
        if dur is not None and not isinstance(dur, (int, float)):
            problems.append(f"playlists.{pname}.items[{i}].duration_minutes: expected number")

# Display driver auto-detection: "auto" probes attached panel,
# falling back to the virtual display when no hardware is present.
