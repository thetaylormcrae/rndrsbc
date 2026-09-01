"""rndrSBC - QA/validation helpers shared by CLI subcommands and the doctor.

Centralises the "build a display headlessly from config" path so that
``doctor --render``, ``calibrate``, ``snapshot`` and ``panel-spec`` all
construct the *same* display object using the *same* config keys. This is the
single source of truth for invocating the physical/virtual panel.
"""

from __future__ import annotations

import importlib
import inspect
import json
import logging
import os

from core import paths

logger = logging.getLogger("rndrSBC.qa")


def load_qa_config(path=None):
    """Load config.json, preferring the CLI-supplied path and falling back to
    the RNDRSBC_HOME path used by the rest of the engine."""
    if path:
        with open(path) as fh:
            return json.load(fh)
    cfg_path = getattr(paths, "CONFIG_PATH", None) or "config.json"
    if os.path.exists(cfg_path):
        with open(cfg_path) as fh:
            return json.load(fh)
    return {}


def resolve_display(cfg: dict):
    """Resolve the active display object from config, WITHOUT touching hardware.

    This is the single source of truth for driver dispatch, mirroring the
    production daemon (main.py). Handles ``driver: "auto"`` by probing the
    attached panel via ``InkyDisplay.detect()`` and falling back to the
    configured ``model``, then to a virtual display — exactly as the daemon
    does. Returns the constructed (un-initialised) display object.
    Raises ValueError/LookupError with a clear message when unusable.
    """
    disp_cfg = cfg.get("display", {}) or {}
    # Hardware-first: when no driver is declared (missing/partial config
    # section, a self-healed or freshly-regenerated config), default to ``auto``
    # so a real panel attached to the Pi is still driven. A bare ``virtual``
    # default silently abandons the physical display -- see the loud fallback
    # warning below.
    driver = (disp_cfg.get("driver") or "auto").replace("driver_", "")
    orientation = disp_cfg.get("orientation", disp_cfg.get("rotation", 0))
    saturation = float(disp_cfg.get("saturation", 0.5))
    saturation = max(0.1, min(1.0, saturation))

    # ---- auto: probe physical panel, then model fallback, then virtual ----
    if driver == "auto":
        from displays.inky import InkyDisplay
        detected = InkyDisplay.detect()
        if detected is not None:
            logger.info("[Display] auto-detected Inky panel '%s'", detected)
            return InkyDisplay(model=detected, orientation=orientation, saturation=saturation)
        fallback_model = disp_cfg.get("model", "impression_7_3")
        try_display = InkyDisplay(model=fallback_model, orientation=orientation, saturation=saturation)
        if try_display._inky is not None:
            logger.info("[Display] attached Inky panel via explicit model '%s'", fallback_model)
            return try_display
        logger.warning(
            "[Display] NO physical panel detected (auto probe failed, model '%s' "
            "had no hardware). Falling back to VIRTUAL display -- the e-paper will "
            "NOT update. Check SPI/I2C and that 'inky' + RPi.GPIO are installed "
            "(rndrsbc[pi] extras), then set display.driver to 'auto' or 'inky'.",
            fallback_model,
        )
        from displays.virtual import VirtualDisplay
        return VirtualDisplay(
            width=disp_cfg.get("width", 800),
            height=disp_cfg.get("height", 480),
            output_path=paths.resolve("live_screen.png"),
        )

    # ---- named drivers ----
    try:
        mod = importlib.import_module(f"displays.{driver}")
    except Exception as exc:  # pragma: no cover - path-specific
        raise ValueError(f"display driver '{driver}' not importable ({type(exc).__name__}: {exc})")

    if driver == "inky":
        from displays.inky import InkyDisplay
        return InkyDisplay(
            model=disp_cfg.get("model", "impression_7_3"),
            orientation=orientation,
            saturation=saturation,
        )
    if driver == "waveshare":
        from displays.waveshare import WaveshareDisplay
        return WaveshareDisplay(model=disp_cfg.get("model", "epd7in3f"), orientation=orientation)
    if driver == "framebuffer":
        from displays.framebuffer import FramebufferDisplay
        return FramebufferDisplay(orientation=orientation)

    # generic: pick a display class by name (virtual or custom)
    for attr in ("VirtualDisplay", driver.title() + "Display", "Display", "InkyDisplay"):
        cls = getattr(mod, attr, None)
        if cls is not None:
            break
    if cls is None:
        raise LookupError(f"no display class found in displays.{driver}")
    sig = inspect.signature(cls.__init__)
    var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD
                 for p in sig.parameters.values())
    cfg_kw = {k: v for k, v in disp_cfg.items()
              if k != "driver" and (k in sig.parameters or var_kw)}
    # A named ``virtual`` driver must still write its live preview into the
    # writable deployment home, never the process CWD (which is unwritable
    # under systemd -> PermissionError on boot). Anchor output_path the same
    # way the ``auto`` path does unless the config explicitly overrides it.
    from displays.virtual import VirtualDisplay
    if cls is VirtualDisplay and "output_path" not in cfg_kw:
        cfg_kw["output_path"] = paths.resolve("live_screen.png")
    return cls(**cfg_kw)


def build_display(cfg: dict):
    """Construct + hardware-init the configured display driver.

    Thin wrapper over :func:`resolve_display` that calls ``init_hardware()``.
    All CLI subcommands and the doctor go through here so the QA path and the
    production daemon can't diverge on driver resolution.
    """
    disp = resolve_display(cfg)
    # auto/virtual display classes already self-init in __init__; call the hook
    # if the class exposes it (guarded) to match daemon behaviour.
    init = getattr(disp, "init_hardware", None)
    if init is not None and type(disp).__name__ not in ("InkyDisplay", "VirtualDisplay"):
        init()
    return disp


def display_brief(disp) -> str:
    """Short human label for a display object (driver + resolution)."""
    try:
        w, h = disp.get_resolution()
        return f"{type(disp).__name__} {w}x{h}"
    except Exception:  # pragma: no cover
        return type(disp).__name__
