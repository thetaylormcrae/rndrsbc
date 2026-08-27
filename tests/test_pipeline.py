"""Render pipeline tests: dirty-rect detection, quantization, cache."""
import pytest
from PIL import Image, ImageDraw

from core.pipeline import compute_dirty_rects, quantize_and_dither, RenderStageCache


def _img(size=(800, 480), bg=(255, 255, 255)):
    im = Image.new("RGB", size, bg)
    return im


def _paint(img, box, color=(0, 0, 0)):
    d = ImageDraw.Draw(img)
    d.rectangle(box, fill=color)
    return img


# --- dirty-rect ---

def test_no_change_returns_empty():
    assert compute_dirty_rects(_img(), _img()) == []


def test_first_frame_returns_whole_frame():
    rects = compute_dirty_rects(None, _img())
    assert rects == [(0, 0, 800, 480)]


def test_size_mismatch_returns_whole_frame():
    big, small = _img((800, 480)), _img((400, 300))
    assert compute_dirty_rects(big, small) == [(0, 0, 400, 300)]


def test_single_change_yields_single_region():
    prev = _img()
    new = _paint(_img(), (100, 100, 180, 200))
    rects = compute_dirty_rects(prev, new)
    assert len(rects) == 1
    x, y, w, h = rects[0]
    assert x <= 100 and y <= 100
    assert x + w >= 180 and y + h >= 200


def test_two_separated_changes_yield_two_disjoint_regions():
    prev = _img()
    new = _paint(_img(), (500, 20, 600, 80))   # top-right
    new = _paint(new, (20, 400, 120, 460))     # bottom-left
    rects = compute_dirty_rects(prev, new)
    # Two spatially separated regions should NOT collapse into one full box.
    assert len(rects) == 2
    total_area = sum(w * h for _, _, w, h in rects)
    full_area = 800 * 480
    assert total_area < full_area / 2  # meaningfully smaller than full frame


def test_dirty_rects_are_grid_aligned_and_not_fractional():
    prev = _img()
    new = _paint(_img(), (0, 0, 7, 7))  # tiny change at origin
    rects = compute_dirty_rects(prev, new)
    assert rects
    for x, y, w, h in rects:
        assert x % 32 == 0 and y % 32 == 0  # grid-aligned (grid_size=32)


# --- quantization ---

def test_quantize_7color_returns_rgb_passthrough_shape():
    out = quantize_and_dither(_img(), color_mode="7color")
    assert out.size == (800, 480)
    assert out.mode in ("RGB", "1", "L", "P")


def test_quantize_mono_returns_binary():
    out = quantize_and_dither(_img((200, 100)), color_mode="3color")
    assert out.size == (200, 100)


# --- cache ---

def test_stage_cache_hit_and_miss():
    c = RenderStageCache()
    img = _img((100, 60))
    c.put("clock", {"tz": "UTC"}, (100, 60), img)
    got = c.get("clock", {"tz": "UTC"}, (100, 60))
    assert got is not None and got.size == (100, 60)
    # Different settings -> different key -> miss
    assert c.get("clock", {"tz": "Europe/Dublin"}, (100, 60)) is None


def test_stage_cache_evicts_oldest():
    c = RenderStageCache(max_entries=2)
    for name in ("a", "b", "c"):
        c.put(name, {}, (10, 10), _img((10, 10)))
    assert c.get("a", {}, (10, 10)) is None  # evicted (oldest)
    assert c.get("c", {}, (10, 10)) is not None
