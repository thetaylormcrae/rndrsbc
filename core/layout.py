"""
rndrSBC - Multi-Widget Spatial Layout & Grid Compositor
Allows splitting the e-Paper canvas into responsive multi-zone layouts:
  - 'split_horizontal' (Left / Right columns)
  - 'split_vertical' (Top / Bottom rows)
  - 'quad' (2x2 grid)
  - 'sidebar_right' (Main 70% left, Sidebar 30% right)
  - 'sidebar_left' (Sidebar 30% left, Main 70% right)
  - 'header_split' (Top 35% banner, Bottom 2 columns)

Renders child widgets into assigned bounding boxes and composites into a single frame.
"""

import logging
from typing import List, Dict, Any, Tuple
from PIL import Image

from core.canvas import ResponsiveCanvas, Rect
from widgets.base import BaseWidget, WIDGET_REGISTRY, register_widget, min_size_for, fits_zone

logger = logging.getLogger("rndrSBC.layout")


LAYOUT_PRESETS = {
    "single": {"description": "Full Screen (1 Widget)", "zones": ["main"]},
    "split_horizontal": {"description": "Two Columns (50/50)", "zones": ["left", "right"]},
    "split_vertical": {"description": "Two Rows (50/50)", "zones": ["top", "bottom"]},
    "sidebar_right": {"description": "Main + Right Sidebar (70/30)", "zones": ["main", "sidebar"]},
    "sidebar_left": {"description": "Left Sidebar + Main (30/70)", "zones": ["sidebar", "main"]},
    "quad": {"description": "4-Zone Grid (2x2)", "zones": ["top_left", "top_right", "bottom_left", "bottom_right"]},
    "header_split": {"description": "Header + 2 Columns", "zones": ["header", "bottom_left", "bottom_right"]}
}


def calculate_zone_rects(bounds: Rect, layout_type: str, gap: int = 8) -> Dict[str, Rect]:
    """Computes exact Rect bounds for each named zone in a layout preset."""
    rects = {}
    if layout_type == "split_horizontal":
        l, r = bounds.split_columns([1, 1], gap=gap)
        rects["left"] = l
        rects["right"] = r
    elif layout_type == "split_vertical":
        t, b = bounds.split_rows([1, 1], gap=gap)
        rects["top"] = t
        rects["bottom"] = b
    elif layout_type == "sidebar_right":
        m, s = bounds.split_columns([7, 3], gap=gap)
        rects["main"] = m
        rects["sidebar"] = s
    elif layout_type == "sidebar_left":
        s, m = bounds.split_columns([3, 7], gap=gap)
        rects["sidebar"] = s
        rects["main"] = m
    elif layout_type == "quad":
        top, bottom = bounds.split_rows([1, 1], gap=gap)
        tl, tr = top.split_columns([1, 1], gap=gap)
        bl, br = bottom.split_columns([1, 1], gap=gap)
        rects["top_left"] = tl
        rects["top_right"] = tr
        rects["bottom_left"] = bl
        rects["bottom_right"] = br
    elif layout_type == "header_split":
        h, lower = bounds.split_rows([3.5, 6.5], gap=gap)
        bl, br = lower.split_columns([1, 1], gap=gap)
        rects["header"] = h
        rects["bottom_left"] = bl
        rects["bottom_right"] = br
    else:
        rects["main"] = bounds
    return rects


@register_widget("composite_grid", "Multi-Zone Layout Grid")
class CompositeGridWidget(BaseWidget):
    """Composites multiple child widgets into a single partitioned display screen."""

    name = "Multi-Zone Layout"
    description = "Arrange multiple widgets side-by-side or stacked in a single screen"
    default_interval_minutes = 15

    def get_config_schema(self) -> dict:
        return {
            "fields": [
                {
                    "name": "layout_type",
                    "label": "Grid Layout",
                    "type": "select",
                    "options": list(LAYOUT_PRESETS.keys()),
                    "default": "sidebar_right"
                },
                {
                    "name": "gap",
                    "label": "Zone Spacing (px)",
                    "type": "number",
                    "default": 8
                },
                {
                    "name": "responsive",
                    "label": "Responsive Behaviour",
                    "type": "select",
                    "options": ["shrink", "hide", "none"],
                    "default": "shrink"
                }
            ]
        }

    def render(self, dimensions: Tuple[int, int], settings: dict, bounds: Rect = None) -> Image.Image:
        layout_type = settings.get("layout_type", "sidebar_right")
        gap = int(settings.get("gap", 8))
        responsive = settings.get("responsive", "shrink")
        zone_configs = settings.get("zones", {})

        with ResponsiveCanvas(dimensions, bg_color="#ffffff") as canvas:
            root_bounds = bounds if bounds is not None else canvas.bounds
            zone_rects = calculate_zone_rects(root_bounds, layout_type, gap=gap)

            for zone_name, z_rect in zone_rects.items():
                z_cfg = zone_configs.get(zone_name, {})
                widget_type = z_cfg.get("widget")

                if widget_type and widget_type in WIDGET_REGISTRY:
                    widget_instance = WIDGET_REGISTRY[widget_type]
                    child_settings = z_cfg.get("settings") or {}
                    self._render_widget_into_zone(
                        canvas, widget_instance, child_settings, z_rect, responsive
                    )
                else:
                    # Render clean placeholder zone box
                    canvas.draw_card(z_rect, radius=6, fill="#fafafa", outline="#e0e0e0", width=1)
                    canvas.draw_text(
                        f"Zone: {zone_name}",
                        (z_rect.x + z_rect.w // 2, z_rect.y + z_rect.h // 2),
                        font=canvas.get_token_font("caption"),
                        fill="#999999",
                        anchor="mm"
                    )

            if bounds is None:
                canvas.draw_frame(settings.get("frame", "Corner"), color="#111111")

            return canvas.to_image()

    def _render_widget_into_zone(self, canvas, widget_instance, child_settings, z_rect, responsive: str):
        """Render a widget into a zone, honoring the responsive setting.

        - ``shrink`` (default): if the zone is smaller than the widget's minimum
          size it is rendered at its minimum and scaled down to fit — content is
          never clipped and nothing is dropped.
        - ``hide``: if the zone cannot fit the widget, show a compact fallback
          instead of a broken render.
        - ``none``: legacy behaviour — render at the zone size regardless.
        """
        mw, mh = min_size_for(widget_instance)
        fits = fits_zone(widget_instance, z_rect.w, z_rect.h)

        if responsive == "none" or fits or (mw <= 0 or mh <= 0):
            zone_img = widget_instance.safe_render(
                (z_rect.w, z_rect.h),
                child_settings,
                bounds=Rect(0, 0, z_rect.w, z_rect.h)
            )
            canvas.image.paste(zone_img, (z_rect.x, z_rect.y))
            return

        if responsive == "hide":
            self._render_hidden_placeholder(canvas, widget_instance, child_settings, z_rect)
            return

        # responsive == "shrink": render at min size, then downscale to fit.
        # Guard against pathological min dimensions larger than the zone.
        render_w = min(z_rect.w, mw)
        render_h = min(z_rect.h, mh)
        zone_img = widget_instance.safe_render(
            (render_w, render_h),
            child_settings,
            bounds=Rect(0, 0, render_w, render_h)
        )
        if zone_img.size == (render_w, render_h):
            # Downscale the rendered frame to the actual zone.
            try:
                with zone_img.resize((z_rect.w, z_rect.h), Image.Resampling.LANCZOS) as scaled:
                    canvas.image.paste(scaled, (z_rect.x, z_rect.y))
            except Exception as e:  # noqa: BLE001
                logger.error(f"shrink resize failed for {widget_instance.name}: {e}")
                canvas.image.paste(zone_img, (z_rect.x, z_rect.y))
        else:
            # Widget ignored the min size and rendered at zone size anyway.
            canvas.image.paste(zone_img, (z_rect.x, z_rect.y))

    def _render_hidden_placeholder(self, canvas, widget_instance, child_settings, z_rect):
        """Compact fallback shown when a zone can't fit a widget in 'hide' mode."""
        canvas.draw_card(z_rect, radius=5, fill="#f7f7f7", outline="#dddddd", width=1)
        label = getattr(widget_instance, "name", str(widget_instance.__class__.__name__))
        icon = getattr(widget_instance, "kicker", None) or getattr(widget_instance, "_min_icon", "∅")
        canvas.draw_text(
            f"{icon} {label}",
            (z_rect.x + z_rect.w // 2, z_rect.y + z_rect.h // 2 + z_rect.h // 8),
            font=canvas.get_token_font("caption"),
            fill="#777777",
            anchor="mm"
        )
        canvas.draw_text(
            "too small to render",
            (z_rect.x + z_rect.w // 2, z_rect.y + z_rect.h - canvas.pt(8)),
            font=canvas.get_token_font("caption"),
            fill="#bbbbbb",
            anchor="mm"
        )
