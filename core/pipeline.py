"""
rndrSBC - Multi-Stage Render Pipeline & Cache Engine
Splits rendering into distinct stages:
  1. Layout & Draw (RGB Image)
  2. Frame Comparison & Dirty Rect Detection
  3. Palette Quantization & Floyd-Steinberg / Atkinson Dithering
  4. Display Driver Hardware Waveform Dispatch

Caches rendered & dithered frames keyed by (widget_name, settings_hash, resolution).
"""

import hashlib
import json
import logging
import time
from typing import List, Tuple, Optional
from PIL import Image, ImageChops

logger = logging.getLogger("rndrSBC.pipeline")


class RenderStageCache:
    """In-memory cache for rendered and dithered frames to eliminate duplicate work."""

    def __init__(self, max_entries: int = 20):
        self._cache = {}
        self._max_entries = max_entries

    def _compute_key(self, widget_name: str, settings: dict, dimensions: Tuple[int, int]) -> str:
        payload = json.dumps({"w": widget_name, "s": settings, "d": list(dimensions)}, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def get(self, widget_name: str, settings: dict, dimensions: Tuple[int, int]) -> Optional[Image.Image]:
        key = self._compute_key(widget_name, settings, dimensions)
        entry = self._cache.get(key)
        if entry:
            img, timestamp, ttl = entry
            if time.time() - timestamp < ttl:
                return img.copy()
            else:
                del self._cache[key]
        return None

    def put(self, widget_name: str, settings: dict, dimensions: Tuple[int, int], image: Image.Image, ttl: int = 60):
        if len(self._cache) >= self._max_entries:
            # Evict oldest entry
            oldest_key = min(self._cache.keys(), key=lambda k: self._cache[k][1])
            del self._cache[oldest_key]
        key = self._compute_key(widget_name, settings, dimensions)
        self._cache[key] = (image.copy(), time.time(), ttl)

    def clear(self):
        self._cache.clear()


# Global stage cache
RENDER_CACHE = RenderStageCache()


def compute_dirty_rects(
    prev_img: Optional[Image.Image],
    new_img: Image.Image,
    grid_size: int = 32,
    max_regions: int = 8,
) -> List[Tuple[int, int, int, int]]:
    """
    Compares two images and returns one or more disjoint bounding boxes that changed.

    Uses grid-based block comparison, then clusters adjacent changed blocks into up to
    ``max_regions`` spatially-separated rectangles. A single fat bounding box is often
    wasteful on e-paper: if the clock ticks in the top-right corner *and* a note updates
    in the bottom-left, a lone bbox spans the whole panel. Splitting into disjoint
    regions dramatically shrinks the partial-refresh waveform area.

    Falls back to a whole-frame rectangle when the frames are incomparable (or when the
    change is diffuse enough that many small regions would be *worse* for the panel).
    """
    if prev_img is None or prev_img.size != new_img.size:
        return [(0, 0, new_img.width, new_img.height)]

    diff = ImageChops.difference(prev_img.convert("RGB"), new_img.convert("RGB"))
    bbox = diff.getbbox()
    if not bbox:
        return []  # No changes at all

    # ---- Build a coarse block state: 1 = block contains a pixel change ---------
    w, h = new_img.width, new_img.height
    cols = max(1, (w + grid_size - 1) // grid_size)
    rows = max(1, (h + grid_size - 1) // grid_size)
    small = diff.resize((cols, rows))  # downsampled: any bright pixel flags the block
    block = [1 if small.getpixel((cx, cy)) != (0, 0, 0) else 0
             for cy in range(rows) for cx in range(cols)]

    # ---- Flood-fill connected regions of changed blocks (4-connectivity) ------
    regions = []  # list of [min_c, min_r, max_c, max_r] block coords
    seen = [False] * len(block)
    for start in range(len(block)):
        if not block[start] or seen[start]:
            continue
        stack = [start]
        seen[start] = True
        mc, mr, Mc, Mr = start % cols, start // cols, start % cols, start // cols
        while stack:
            idx = stack.pop()
            c, r = idx % cols, idx // cols
            mc, mr = min(mc, c), min(mr, r)
            Mc, Mr = max(Mc, c), max(Mr, r)
            for dc, dr in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nc, nr = c + dc, r + dr
                if 0 <= nc < cols and 0 <= nr < rows:
                    ni = nr * cols + nc
                    if block[ni] and not seen[ni]:
                        seen[ni] = True
                        stack.append(ni)
        regions.append([mc, mr, Mc, Mr])

    if not regions:
        return []

    # ---- Cap the region count --------------------------------------------------
    # Too many small regions is worse than one moderate box; merge farthest-apart
    # regions until we're within budget. A diffuse change degrades gracefully to a
    # single full-frame box (same as the original behaviour).
    while len(regions) > max_regions:
        # Find the pair of regions whose bounding union adds the most area.
        best_pair = None
        best_cost = -1
        for i in range(len(regions)):
            for j in range(i + 1, len(regions)):
                a, b = regions[i], regions[j]
                un = (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))
                area = (un[2] - un[0] + 1) * (un[3] - un[1] + 1)
                a_area = (a[2] - a[0] + 1) * (a[3] - a[1] + 1)
                b_area = (b[2] - b[0] + 1) * (b[3] - b[1] + 1)
                cost = area - a_area - b_area
                if cost > best_cost:
                    best_cost = cost
                    best_pair = (i, j, un)
        if best_pair is None:
            break
        i, j, un = best_pair
        regions[j] = un
        del regions[i]

    # ---- Convert block coords -> pixel coords (grid-aligned, clamped) ----------
    out = []
    for mc, mr, Mc, Mr in regions:
        x0 = mc * grid_size
        y0 = mr * grid_size
        x1 = min(w, (Mc + 1) * grid_size)
        y1 = min(h, (Mr + 1) * grid_size)
        out.append((x0, y0, x1 - x0, y1 - y0))
    return out


def quantize_and_dither(image: Image.Image, color_mode: str = "7color", dither: bool = True) -> Image.Image:
    """
    Stage 3: Quantize and dither image using e-paper target palettes.
    Supports 'mono' (1-bit BW), 'tri' (Black/White/Red/Yellow), and '7color' (Spectra 7).
    """
    img_rgb = image.convert("RGB")
    if color_mode == "mono":
        # 1-bit threshold / Floyd-Steinberg
        if dither:
            return img_rgb.convert("1", dither=Image.Dither.FLOYDSTEINBERG).convert("RGB")
        return img_rgb.convert("1", dither=Image.Dither.NONE).convert("RGB")
    elif color_mode == "tri":
        # 3-color palette (Black, White, Red)
        pal_img = Image.new("P", (1, 1))
        pal_img.putpalette([
            0, 0, 0,        # Black
            255, 255, 255,  # White
            255, 0, 0,      # Red
            255, 255, 0,    # Yellow (optional 4th)
        ] + [0] * (256 * 3 - 12))
        return img_rgb.quantize(palette=pal_img, dither=Image.Dither.FLOYDSTEINBERG if dither else Image.Dither.NONE).convert("RGB")
    elif color_mode == "7color":
        # ACeP 7-Color palette: Black, White, Green, Blue, Red, Yellow, Orange
        pal_img = Image.new("P", (1, 1))
        pal_img.putpalette([
            0, 0, 0,        # Black
            255, 255, 255,  # White
            0, 255, 0,      # Green
            0, 0, 255,      # Blue
            255, 0, 0,      # Red
            255, 255, 0,    # Yellow
            255, 128, 0,    # Orange
        ] + [0] * (256 * 3 - 21))
        return img_rgb.quantize(palette=pal_img, dither=Image.Dither.FLOYDSTEINBERG if dither else Image.Dither.NONE).convert("RGB")
    else:
        return img_rgb
