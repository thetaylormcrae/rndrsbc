"""rndrSBC — REFERENCE WIDGET (canonical community template)

This is the definitive example for writing a community widget. Copy this
`widget.py` + the `metadata` block into your own repo and PR it to the
rndrSBC Registry. A reviewer merges it; `rndrsbc install <id>` verifies +
installs it; the frame auto-discovers and renders it. No git clone, no manual
config and no "works on their machine" — everything below is the real airlock.

== The contract (read this once, you know the whole plugin interface) ==

 1. DECORATE: `@register_widget("id")` on your class. Auto-discovery imports
    the module and registers you with zero config. Class attrs `name`,
    `description`, `default_interval_minutes` drive the dashboard + favicon.

 2. `get_config_schema()` returns the *fields* your widget wants the user to
    tune in the web dashboard. The engine stores + hands these back in
    `render` as `settings`.

 3. `render(self, dimensions, settings, bounds=None)` is the one required
    method. It returns `canvas.to_image()`. `dimensions` is `(w, h)` of the
    tile; `bounds` is the Rect you *may* draw within (else use the canvas
    bounds). You draw via the `canvas` py: 
        `with ResponsiveCanvas(dimensions, bg_color="#fff") as canvas:`
        … draw … `return canvas.to_image()`

 4. `safe_render()` in the engine wraps render: raise anything and the frame
    shows a clean inline fallback box, never a crash/white screen.

 5. NETWORK: use `self.fetch_remote_json(url)` — threaded, cached, stale-data
    fallback. NEVER block render() on the socket.

 6. TIME: `self.get_local_now()` is timezone/DST aware via zoneinfo.

 == What you must NOT do ==
   - No bare HTTP in render() (use fetch_remote_json).
   - No foreign hardware/GPIO/subprocess/systemctl (the frame owns those).
   - No writing config.json / runtime state (the engine owns persistence).
"""

import logging
from widgets.base import BaseWidget, ResponsiveCanvas, register_widget

logger = logging.getLogger("rndrSBC.widgets.clock")

# Registry metadata — mirrors what a PR to the rndrSBC-Registry feed declares.
META = {
    "id": "clock",                                  # must equal @register_widget("clock")
    "title": "Classic Analog + Digital Clock",
    "author": "rndrSBC Team",
    "version": "1.0.0",
    "description": "Reference widget: the current time (digital) with full "
                   "timezone/DST awareness, plus an analog dial.",
    "license": "MIT",
    "tags": ["time", "reference", "starter"],
    "screensizes": ["any"],
}


@register_widget("clock", display_name="Clock (analog+digital)")
class ClockWidget(BaseWidget):
    """Reference clock. The cleanest complete BaseWidget you can copy."""

    name = "Clock"
    description = "Live time with timezone/DST awareness and an analog dial"
    default_interval_minutes = 1   # refresh cadence the engine scheduler uses

    def get_config_schema(self) -> dict:
        """What the dashboard settings UI exposes for this widget."""
        return {
            "fields": [
                {"name": "time_format", "label": "Time Format", "type": "select",
                 "options": ["24h", "12h"], "default": "24h"},
                {"name": "timezone", "label": "Timezone (IANA, empty = local)",
                 "type": "string", "default": ""},
                {"name": "show_seconds", "label": "Show Seconds", "type": "boolean",
                 "default": True},
                {"name": "show_analog", "label": "Show Analog Dial", "type": "boolean",
                 "default": True},
            ]
        }

    def render(self, dimensions, settings: dict, bounds: "Rect" = None) -> "Image.Image":
        tz = (settings.get("timezone") or "").strip() or None
        now = self.get_local_now(tz)                       # timezone/DST aware
        fmt_24 = settings.get("time_format", "24h") == "24h"
        show_sec = settings.get("show_seconds", True)
        show_analog = settings.get("show_analog", True)

        hh = now.hour if fmt_24 else (now.hour % 12 or 12)
        mm = now.minute
        ss = now.second
        hms = f"{hh:02d}:{mm:02d}" + (f":{ss:02d}" if show_sec else "")

        with ResponsiveCanvas(dimensions, bg_color="#ffffff") as canvas:
            region = bounds if bounds is not None else canvas.bounds
            font = canvas.get_font("Roboto-Bold", 40)
            # center the digital readout
            canvas.draw_text(hms, (region.center[0], region.center[1] - canvas.pt(8)),
                             font=font, fill="#111111", anchor="mm")

            # small tz label under the time, so DST-awareness is visible
            tz_name = _tz_label(now, tz)
            if tz_name:
                tiny = canvas.get_font("Roboto-Regular", 12)
                canvas.draw_text(tz_name, (region.center[0], region.bottom - canvas.pt(6)),
                                 font=tiny, fill="#666666", anchor="ms")

            # optional analog dial in the corner
            if show_analog:
                _draw_analog(canvas, now, corner=region, size=canvas.pt(90))

            return canvas.to_image()   # required: return the rendered frame


def _tz_label(now, tz):
    try:
        if tz:
            return now.tzinfo and str(now.tzinfo) or tz
        return "local"
    except Exception:  # noqa: BLE001
        return None


def _draw_analog(canvas, now, corner, size):
    """Simple analog dial in the corner of the tile."""
    import math
    from PIL import ImageDraw
    cx, cy = corner.x + size, corner.y + size
    r = size - canvas.pt(12)
    draw = ImageDraw.Draw(canvas.image)
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline="#999999", width=3)

    # Calculate real geometric angles for clock hands
    h_ang = (now.hour % 12 + now.minute / 60.0) * 30.0
    m_ang = (now.minute + now.second / 60.0) * 6.0
    s_ang = now.second * 6.0

    for (ang, ln, wd, col) in (
        (h_ang, r - canvas.pt(18), 4, "#111111"),      # hour hand
        (m_ang, r - canvas.pt(10), 3, "#111111"),      # minute hand
        (s_ang, r - canvas.pt(6),  1, "#e65c00"),      # second hand
    ):
        theta = math.radians(90 - ang)
        tip = (cx + ln * math.cos(theta), cy - ln * math.sin(theta))
        draw.line([cx, cy, tip], fill=col, width=wd)
