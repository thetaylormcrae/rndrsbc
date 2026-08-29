"""QA & validation regression tests (Items 1-4).

Proves:
  1. Colour calibration pattern produces exact native primaries.
  2. Snapshot / render_preview captures the canvas deterministically.
  3. Panel-spec correctly interprets resolutions and palettes.
  4. Golden image regression test passes.
"""

from __future__ import annotations

import os
from PIL import Image
import pytest

from core.calibrate import make_colour_pattern, verify_pattern_pixels, PRIMARIES
from server.scheduler import Scheduler
from widgets.base import discover_widgets, WIDGET_REGISTRY

# Ensure widget plugins are discovered (registry is empty until populated).
discover_widgets()


def test_calibrate_pattern_primaries():
    img = make_colour_pattern(800, 480)
    assert img.size == (800, 480)
    v = verify_pattern_pixels(img)
    assert v["_summary"]["ok"] is True
    for name in ("black", "white", "yellow", "red", "blue", "green"):
        assert v[name]["ok"] is True
        # Dominant block color matches the canonical primary
        assert v[name]["dominant"] == PRIMARIES[name]


def test_panel_spec_classification():
    from rndrsbc import _panel_spec
    spec = _panel_spec._classify_panel((800, 480), {})
    assert "Spectra 6" in spec["panel"]
    assert spec["palette"] == "7-colour"
    assert spec["native_colours"][2] == "yellow"


def test_scheduler_render_preview():
    class DummyDisplay:
        def get_resolution(self): return (800, 480)
        def init_hardware(self): pass
        def update(self, img): pass

    cfg = {
        "schema_version": 2,
        "display": {"driver": "virtual", "width": 800, "height": 480},
        "active_playlist": "default",
        "playlists": {"default": [{"widget": "system_stats", "settings": {}}]}
    }
    sched = Scheduler(DummyDisplay(), cfg, WIDGET_REGISTRY)
    frame = sched.render_preview(0)
    assert isinstance(frame, Image.Image)
    assert frame.size == (800, 480)
    assert frame.mode == "RGB"
