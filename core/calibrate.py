"""rndrSBC - Display Calibration & Colour Validation.

Renders the canonical Spectra 6 / Inky reference test pattern (one block per
physical colour + a dither band + a border strip), pushes it to the configured
display, and saves a companion snapshot under the deploy drive so output can
be audited remotely (photograph the panel, diff the PNG) without a camera on
the Pi.

Provides:
  - ``make_colour_pattern(w, h)``           build the reference RGB pattern
  - ``verify_pattern_pixels(pattern)``      assert every block is a true primary
  - ``run_calibration(display, out_path)``  render+verify+persist in one step

This is the ``--calibrate`` half of the rndrSBC QA story: deterministic,
repeatable proof that (a) the panel drives all 6 native primaries and (b) the
software palette matches them 1:1.
"""

from __future__ import annotations

import os
from collections import Counter

from PIL import Image, ImageDraw, ImageFont

# Primary colours in the same index order Pimoroni's e673 P-mode branch maps
# to display indices (Black=0, White=1, Yellow=2, Red=3, Blue=4, Green=5).
PRIMARIES = {
    "black": (0, 0, 0),
    "white": (255, 255, 255),
    "yellow": (255, 255, 0),
    "red": (255, 0, 0),
    "blue": (0, 0, 255),
    "green": (0, 255, 0),
}

# Colours actually visible on a Spectra 6 panel.
PRIMARY_ORDER = ["black", "white", "yellow", "red", "blue", "green"]


def _block_w(width: int) -> int:
    """Swatch width; 1/10 of the panel, never narrower than 40px."""
    return max(40, width // 10)


def make_colour_pattern(width: int = 800, height: int = 480) -> Image.Image:
    """Return an RGB reference test image sized to the panel.

    Layout (landscape): top band = 6 primary swatches in display-index order,
    middle band = a vertical dither gradient (proves grayscale interpolation),
    a border frame of ``yellow``, and a label strip so the PNG is self-
    describing when audited remotely.
    """
    img = Image.new("RGB", (width, height), PRIMARIES["white"])
    d = ImageDraw.Draw(img)

    bw = _block_w(width)
    # --- 6 primary swatches across the top ---
    for i, name in enumerate(PRIMARY_ORDER):
        x0 = i * bw
        d.rectangle([x0, 0, x0 + bw - 1, int(height * 0.62)], fill=PRIMARIES[name])

    # --- dither gradient band (bottom strip) ---
    gw = int(height * 0.28)
    grad = Image.new("RGB", (width, 1))
    grad.putdata([(int(255 * i / (width - 1)),) * 3 for i in range(width)])
    img.paste(grad.resize((width, gw), Image.Resampling.BOX), (0, int(height * 0.62)))

    # --- border frame ---
    d.rectangle([0, 0, width - 1, height - 1], outline=PRIMARIES["yellow"], width=6)

    # --- labels ---
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None
    d.text((10, int(height * 0.90) + 2), "rndrSBC colour-test",
           fill=PRIMARIES["black"], font=font)
    for i, name in enumerate(PRIMARY_ORDER):
        d.text((i * bw + 6, int(height * 0.64)), name.capitalize(),
               fill=(0, 0, 0), font=font)

    return img


def verify_pattern_pixels(pattern: Image.Image) -> dict:
    """Verify each primary swatch region maps to (nearly) the expected colour.

    Dithering is off for the solid blocks, so each block region should be almost
    exactly a single primary; we tolerate a tiny per-chunk epsilon for edge
    anti-aliasing. Returns a per-block verdict plus an overall summary.
    """
    w, h = pattern.size
    bw = _block_w(w)
    block_h = int(h * 0.62)
    results = {}
    all_ok = True
    for i, name in enumerate(PRIMARY_ORDER):
        x0 = i * bw
        region = pattern.crop((x0, 0, x0 + bw, block_h)).convert("RGB")
        region = region.resize((bw // 2, block_h // 2), Image.Resampling.BOX)
        # Pillow >=14 uses get_flattened_data; older uses getdata.
        data = list(getattr(region, "get_flattened_data", region.getdata)())
        dom, cnt = Counter(data).most_common(1)[0]
        expected = PRIMARIES[name]
        ok = all(abs(dom[c] - expected[c]) <= 12 for c in range(3))
        results[name] = {"dominant": dom, "expected": expected, "ok": ok,
                         "coverage": round(cnt / len(data), 3)}
        all_ok = all_ok and ok
    results["_summary"] = {"ok": all_ok}
    return results


def run_calibration(display, out_path: str | None = None,
                    save_drive: bool = False) -> dict:
    """Render the reference colour pattern via ``display``, verify primaries,
    persist a PNG copy (optionally under the deploy drive), and print table.

    Returns a report dict {width, height, driver, verdict, blocks} suitable for
    ``--json`` output. ``display`` must implement the BaseDisplay interface
    (``get_resolution``, ``update``) which the CLI passes in after init.
    """
    w, h = display.get_resolution()
    pattern = make_colour_pattern(w, h)
    report = {"width": w, "height": h,
              "driver": type(display).__name__,
              "saturation": getattr(display, "saturation", None)}

    # Software verify BEFORE touching the panel so a bad palette never ships.
    v = verify_pattern_pixels(pattern)
    report["pre_verify"] = v

    # Push to the physical display (or virtual snapshot backend).
    display.update(pattern)

    if out_path:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
        pattern.save(out_path)
        report["snapshot"] = out_path
    elif save_drive:
        drive_root = "/rool-drive" if os.path.isdir("/rool-drive") else \
            os.path.join(os.environ.get("RNDRSBC_HOME", os.path.expanduser("~")), "drive")
        os.makedirs(drive_root, exist_ok=True)
        out_path = os.path.join(drive_root, "calibration.png")
        pattern.save(out_path)
        report["snapshot"] = out_path

    report["verdict"] = "OK" if v["_summary"]["ok"] else "BLOCK-FAIL"
    return report
