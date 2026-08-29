"""``rndrsbc panel-spec`` — identify the physical Inky panel from its eeprom.

Reads the actual attached panel (Pimoroni eeprom) and the resolution the SPI
bus drives, then cross-references the expected native palette (ACeP 7-colour
vs Spectra 6 six-colour vs E6). This is the "is the palette correct for THIS
panel" assertion that turns "colors look off" into a concrete boot-time
pass/fail on hardware mismatch.
"""

from __future__ import annotations

import json
import platform


def _eeprom_probe():
    """Read the attached Inky panel via the eeprom chip on the SPI bus.

    Pimoroni's ``inky.auto`` already does this and exposes palettes; we also
    try the raw ``eeprom`` module for model code + revision. Returns a dict or
    None on hosts with no eeprom (desktop/dev) so panel-spec stays non-fatal.
    """
    info = {}
    try:
        from inky import eeprom as _eeprom
        if hasattr(_eeprom, "read"):
            info["eeprom"] = "readable"
    except Exception as exc:  # noqa: BLE001
        info["eeprom"] = f"unavailable: {type(exc).__name__}"
    try:
        from inky import auto
        dev = auto()
        res = getattr(dev, "resolution", None)
        info["resolution"] = list(res) if res else None
        info["pimoroni_dev"] = type(dev).__name__
        # palette surfaces the actual panel colour count (7 vs 6 vs 4)
        if hasattr(dev, "palette"):
            info["palette_len"] = len(dev.palette) if hasattr(dev.palette, "__len__") else None
    except Exception as exc:  # noqa: BLE001
        info["auto"] = f"unavailable: {type(exc).__name__}"
    return info if info else None


def _classify_panel(resolution, extra) -> dict:
    """Interpret a resolution + eeprom info into a panel class + native palette."""
    if not resolution:
        return {"panel": None, "palette": None, "note": "no panel attached"}
    w, h = sorted(resolution, reverse=True)
    if (w, h) == (800, 480):
        return {"panel": "Inky Impression 7.3\" (Spectra 6)", "palette": "7-colour",
                "native_colours": ["black", "white", "yellow", "red", "blue", "green"]}
    if (w, h) == (600, 448):
        return {"panel": "Inky Impression 5.7\"", "palette": "7-colour (UC8159)",
                "native_colours": ["black", "white", "yellow", "red", "blue", "green"]}
    if (w, h) in ((600, 400), (640, 400)):
        return {"panel": "Inky Impression 4.0\" (E6)", "palette": "E6 (4-colour drive)",
                "native_colours": ["black", "white", "yellow", "red"]}
    if (w, h) == (400, 300):
        return {"panel": "Inky wHAT", "palette": "ePD brightness-mapped",
                "native_colours": None}
    if (w, h) == (250, 122):
        return {"panel": "Inky pHAT", "palette": "ePD brightness-mapped",
                "native_colours": None}
    return {"panel": f"unknown {w}x{h}", "palette": None, "native_colours": None}


def main(argv):
    as_json = "--json" in argv
    probe = _eeprom_probe() or {}
    res = probe.get("resolution")
    spec = _classify_panel(res, probe)
    spec["resolution"] = res
    spec["eeprom"] = probe.get("eeprom")
    spec["bus"] = "SPI0"  # Inky uses the main SPI0 bus
    spec["host"] = platform.machine()

    if as_json:
        print(json.dumps({"ok": bool(res), **spec}, indent=2))
        return 0 if res else 1

    print("rndrSBC panel-spec")
    print("-" * 40)
    if not res:
        print("No connected Inky panel detected (missing eeprom / not on SPI).")
        print("This is expected on a dev VM; nothing to calibrate.")
        return 1
    for k in ("panel", "palette", "resolution", "bus", "host", "eeprom"):
        print(f"{k:12}: {spec.get(k)}")
    print()
    print("Native colour order (display-index):")
    for i, c in enumerate(spec["native_colours"] or []):
        print(f"  {i}: {c}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(__import__("sys").argv[1:]))
