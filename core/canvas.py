"""
rndrSBC - High-Performance Native Canvas Engine
Vector and layout rendering engine designed specifically for Single-Board Computers and E-Paper displays.

Memory Management & Buffer Safety:
- Context manager support (`with ResponsiveCanvas(...) as canvas:`) for automatic image disposal.
- Explicit `.close()` cleanup to eliminate PIL memory leaks on low-RAM SBC targets.
- High-level layout helpers (fit_text, padded_text_box, badges, tokens) to prevent repetitive coordinate math.
"""

from PIL import Image, ImageDraw, ImageFont, ImageColor
import os
import math
import logging
import gc

logger = logging.getLogger("rndrSBC.canvas")


class Rect:
    """Represents a 2D bounding box with layout splitting capabilities."""
    def __init__(self, x: float, y: float, w: float, h: float):
        self.x = int(x)
        self.y = int(y)
        self.w = max(0, int(w))
        self.h = max(0, int(h))

    @property
    def right(self): return self.x + self.w
    @property
    def bottom(self): return self.y + self.h
    @property
    def center(self): return (self.x + self.w // 2, self.y + self.h // 2)
    @property
    def bbox(self): return (self.x, self.y, self.right, self.bottom)

    def inset(self, top: int, right: int = None, bottom: int = None, left: int = None):
        """Applies padding/margin to create an inner rectangle."""
        if right is None: right = top
        if bottom is None: bottom = top
        if left is None: left = right
        return Rect(
            self.x + left,
            self.y + top,
            max(0, self.w - left - right),
            max(0, self.h - top - bottom)
        )

    def split_columns(self, weights: list[float], gap: int = 0) -> list["Rect"]:
        """Divides this rectangle horizontally into column rects based on weights."""
        if not weights: return []
        total_gaps = gap * (len(weights) - 1)
        available_w = max(0, self.w - total_gaps)
        total_weight = sum(weights) or 1.0

        cols = []
        cur_x = self.x
        for w in weights:
            col_w = int((w / total_weight) * available_w)
            cols.append(Rect(cur_x, self.y, col_w, self.h))
            cur_x += col_w + gap
        return cols

    def split_rows(self, weights: list[float], gap: int = 0) -> list["Rect"]:
        """Divides this rectangle vertically into row rects based on weights."""
        if not weights: return []
        total_gaps = gap * (len(weights) - 1)
        available_h = max(0, self.h - total_gaps)
        total_weight = sum(weights) or 1.0

        rows = []
        cur_y = self.y
        for w in weights:
            row_h = int((w / total_weight) * available_h)
            rows.append(Rect(self.x, cur_y, self.w, row_h))
            cur_y += row_h + gap
        return rows


class ResponsiveCanvas:
    """Resolution-independent drawing canvas with proportional scaling and memory lifecycle management."""

    # Standardized typography token scale (reference size in pt at 800x480)
    FONT_TOKENS = {
        "hero": ("Roboto-Bold", 46, "bold"),
        "title": ("Roboto-Bold", 26, "bold"),
        "headline": ("Roboto-Bold", 20, "bold"),
        "subhead": ("Roboto-Regular", 16, "normal"),
        "body": ("Roboto-Regular", 14, "normal"),
        "body_bold": ("Roboto-Bold", 14, "bold"),
        "caption": ("Roboto-Regular", 12, "normal"),
        "caption_bold": ("Roboto-Bold", 12, "bold"),
        "metric": ("Roboto-Bold", 13, "bold"),
        "small": ("Roboto-Regular", 11, "normal"),
    }

    def __init__(self, target_dimensions: tuple[int, int], ref_dimensions=(800, 480), bg_color=(255, 255, 255, 255)):
        self.width, self.height = target_dimensions
        self.ref_w, self.ref_h = ref_dimensions

        self.sx = self.width / self.ref_w
        self.sy = self.height / self.ref_h
        self.scale = min(self.sx, self.sy)

        if isinstance(bg_color, str):
            try:
                bg_color = ImageColor.getrgb(bg_color)
            except Exception:
                bg_color = (255, 255, 255)

        self.image = Image.new("RGBA", (self.width, self.height), bg_color)
        self.draw = ImageDraw.Draw(self.image)
        self.bounds = Rect(0, 0, self.width, self.height)
        self._is_closed = False

    def __enter__(self):
        """Context manager entry for safe memory tracking."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit automatically cleans up allocated buffers."""
        self.close()

    def close(self):
        """Explicitly frees underlying PIL Image and draw buffers to prevent OOM."""
        if not self._is_closed:
            try:
                if self.draw is not None:
                    del self.draw
                    self.draw = None
                if self.image is not None:
                    self.image.close()
                    self.image = None
            except Exception as e:
                logger.debug(f"Error closing canvas: {e}")
            finally:
                self._is_closed = True

    def pt(self, base_val: float) -> int:
        """Scales a pixel value proportionally from base reference resolution."""
        return max(1, int(base_val * self.scale))

    def get_token_font(self, token: str) -> ImageFont.FreeTypeFont:
        """Resolves standard design system typography tokens."""
        font_name, size, weight = self.FONT_TOKENS.get(token, ("Roboto-Regular", 14, "normal"))
        return self.get_font(font_name, size, font_weight=weight)

    def get_font(self, font_name: str, base_size: int, font_weight: str = "normal") -> ImageFont.FreeTypeFont:
        """Loads and scales font size relative to display DPI, with cross-platform fallbacks."""
        scaled_size = max(8, int(base_size * self.scale))
        
        candidate_files = []
        is_bold = font_weight in ["bold", "b", "semibold", "SemiBold", "700", "600"]
        if is_bold:
            candidate_files.extend([
                f"{font_name}-Bold.ttf", f"{font_name}-SemiBold.ttf", f"{font_name}Bold.ttf",
                f"{font_name}.ttf", "arialbd.ttf", "segoeuib.ttf", "DejaVuSans-Bold.ttf"
            ])
        else:
            candidate_files.extend([
                f"{font_name}-Regular.ttf", f"{font_name}.ttf", "arial.ttf", "segoeui.ttf", "DejaVuSans.ttf"
            ])

        try:
            from core.paths import FONTS_DIR, package_path
            pkg_fonts = package_path("assets", "fonts")
        except Exception:
            FONTS_DIR = None
            pkg_fonts = None

        search_dirs = [
            d for d in [
                FONTS_DIR,
                pkg_fonts,
                os.path.join(os.path.dirname(__file__), "..", "assets", "fonts"),
                os.path.join(os.path.dirname(__file__), "..", "static", "fonts"),
                os.path.join(os.path.dirname(__file__), "fonts"),
                "C:\\Windows\\Fonts",
                "/usr/share/fonts/truetype/dejavu",
                "/usr/share/fonts/truetype",
                "/usr/share/fonts"
            ] if d and os.path.exists(d)
        ]

        for s_dir in search_dirs:
            if not os.path.exists(s_dir): continue
            for c_file in candidate_files:
                p = os.path.join(s_dir, c_file)
                if os.path.exists(p):
                    try:
                        return ImageFont.truetype(p, scaled_size)
                    except Exception:
                        pass

        try:
            return ImageFont.truetype(font_name, scaled_size)
        except Exception:
            pass

        try:
            return ImageFont.truetype("arial.ttf", scaled_size)
        except Exception:
            return ImageFont.load_default()

    def fit_text(self, text: str, max_rect: Rect, font_name: str = "Roboto-Bold",
                 max_pt: int = 32, min_pt: int = 10, font_weight: str = "bold") -> ImageFont.FreeTypeFont:
        """Calculates the optimal font size to fit text within the target bounding box."""
        for size in range(max_pt, min_pt - 1, -2):
            font = self.get_font(font_name, size, font_weight=font_weight)
            bbox = self.draw.textbbox((0, 0), str(text), font=font)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]
            if text_w <= max_rect.w and text_h <= max_rect.h:
                return font
        return self.get_font(font_name, min_pt, font_weight=font_weight)

    def draw_padded_text_box(self, rect: Rect, text: str, font: ImageFont.FreeTypeFont = None,
                             fill="black", bg_color=None, padding=8, align="left",
                             border_color=None, border_width=1, radius=6):
        """Draws a responsive container box with padded text inside."""
        p = self.pt(padding)
        if bg_color or border_color:
            self.draw_card(rect, radius=radius, fill=bg_color or "white", outline=border_color, width=border_width)
        
        inner = rect.inset(p)
        if font is None:
            font = self.get_token_font("body")

        if align == "center":
            xy = (inner.center[0], inner.center[1])
            anchor = "mm"
        elif align == "right":
            xy = (inner.right, inner.center[1])
            anchor = "rm"
        else: # left
            xy = (inner.x, inner.center[1])
            anchor = "lm"

        self.draw.text(xy, str(text), font=font, fill=fill, anchor=anchor)

    def draw_badge(self, rect: Rect, text: str, font: ImageFont.FreeTypeFont = None,
                   bg_color="#e65c00", text_color="#ffffff", radius=4):
        """Draws a compact status or tag badge."""
        if font is None:
            font = self.get_token_font("caption_bold")
        self.draw_card(rect, radius=radius, fill=bg_color, outline=None)
        self.draw.text((rect.center[0], rect.center[1]), str(text), font=font, fill=text_color, anchor="mm")

    def draw_key_value(self, rect: Rect, key: str, value: str, icon_path: str = None,
                       key_font: ImageFont.FreeTypeFont = None, val_font: ImageFont.FreeTypeFont = None,
                       align="left"):
        """Draws a standard metric key-value pair with optional icon."""
        if key_font is None:
            key_font = self.get_token_font("small")
        if val_font is None:
            val_font = self.get_token_font("metric")

        cur_rect = rect
        if icon_path and os.path.exists(icon_path):
            ico_box, txt_box = rect.split_columns([2.5, 7.5], gap=self.pt(4))
            self.paste_icon(icon_path, ico_box.inset(self.pt(2)), size_pt=14)
            cur_rect = txt_box

        self.draw.text((cur_rect.x, cur_rect.y), str(key), font=key_font, fill="#555555", anchor="la")
        self.draw.text((cur_rect.x, cur_rect.y + self.pt(12)), str(value), font=val_font, fill="#000000", anchor="la")

    def draw_stale_indicator(self, rect: Rect, tooltip: str = "Offline", fill="#e65c00"):
        """Renders a subtle badge/dot indicating cached/offline data."""
        dot_r = self.pt(4)
        dot_box = (rect.right - dot_r * 2 - self.pt(4), rect.y + self.pt(4), rect.right - self.pt(4), rect.y + dot_r * 2 + self.pt(4))
        self.draw.ellipse(dot_box, fill=fill)
        font = self.get_token_font("small")
        self.draw.text((rect.right - dot_r * 2 - self.pt(8), rect.y + dot_r + self.pt(4)), tooltip, font=font, fill=fill, anchor="rm")

    def draw_frame(self, frame_style: str, color="black", width_pt=4):
        """Draws standard frame styles (Rectangle, Corner, Top and Bottom)."""
        if not frame_style or frame_style == "None":
            return
        w = self.pt(width_pt)
        if frame_style == "Rectangle":
            self.draw.rectangle(self.bounds.bbox, outline=color, width=w)
        elif frame_style == "Top and Bottom":
            self.draw.line([(0, 0), (self.width, 0)], fill=color, width=w)
            self.draw.line([(0, self.height - w), (self.width, self.height - w)], fill=color, width=w)
        elif frame_style == "Corner":
            arm = self.pt(35)
            self.draw.line([(0, 0), (arm, 0)], fill=color, width=w)
            self.draw.line([(0, 0), (0, arm)], fill=color, width=w)
            self.draw.line([(self.width - arm, 0), (self.width, 0)], fill=color, width=w)
            self.draw.line([(self.width - w, 0), (self.width - w, arm)], fill=color, width=w)
            self.draw.line([(0, self.height - w), (arm, self.height - w)], fill=color, width=w)
            self.draw.line([(0, self.height - arm), (0, self.height)], fill=color, width=w)
            self.draw.line([(self.width - arm, self.height - w), (self.width, self.height - w)], fill=color, width=w)
            self.draw.line([(self.width - w, self.height - arm), (self.width - w, self.height)], fill=color, width=w)

    def draw_card(self, rect: Rect, radius=8, fill="white", outline=None, width=1):
        """Draws a responsive rounded rectangle card."""
        r = self.pt(radius)
        w = self.pt(width) if outline else 0
        self.draw.rounded_rectangle(rect.bbox, radius=r, fill=fill, outline=outline, width=w)

    def draw_text(self, text: str, xy: tuple[int, int], font: ImageFont.FreeTypeFont, fill="black", anchor="la"):
        """Draws single line text and returns bounding box."""
        self.draw.text(xy, str(text), font=font, fill=fill, anchor=anchor)
        return self.draw.textbbox(xy, str(text), font=font, anchor=anchor)

    def draw_progress_bar(self, rect: Rect, percent: float, fill_color="black", bg_color=(230, 230, 230), radius=6):
        """Draws a progress bar with rounded corners."""
        r = self.pt(radius)
        self.draw.rounded_rectangle(rect.bbox, radius=r, fill=bg_color)
        if percent > 0:
            fill_w = max(r * 2, int(rect.w * min(1.0, percent / 100.0)))
            fill_rect = (rect.x, rect.y, min(rect.right, rect.x + fill_w), rect.bottom)
            self.draw.rounded_rectangle(fill_rect, radius=r, fill=fill_color)

    def paste_icon(self, icon_path: str, rect: Rect, size_pt: int = None):
        """Loads, scales, and centers an icon within a target bounding box with safe buffer disposal."""
        if not icon_path or not os.path.exists(icon_path):
            return
        try:
            with Image.open(icon_path) as raw_icon:
                with raw_icon.convert("RGBA") as icon:
                    target_size = self.pt(size_pt) if size_pt else min(rect.w, rect.h)
                    if target_size <= 0: return
                    with icon.resize((target_size, target_size), Image.Resampling.LANCZOS) as resized:
                        x = rect.x + (rect.w - target_size) // 2
                        y = rect.y + (rect.h - target_size) // 2
                        self.image.paste(resized, (x, y), resized)
        except Exception as e:
            logger.error(f"Failed to paste icon {icon_path}: {e}")

    def fit_image(self, image, rect: Rect, fit: str = "contain", bg_color=None):
        """Scale an image to fit a target Rect while preserving aspect ratio.

        ``fit`` modes:
          - ``"contain"`` (default): scale to the largest size that fits inside
            rect. Any remaining letter-box margin is filled with ``bg_color``
            (or left as-is when ``bg_color`` is None). Never crops, never
            distorts, and never exceeds the container.
          - ``"cover"``: scale to completely fill rect, cropping overflow.
          - ``"stretch"``: fill rect exactly (distorts aspect ratio).
          - ``"fill-height"`` / ``"fill-width"``: fit one axis only, keeping
            aspect ratio, other axis may overflow (clipped by caller).
        """
        if image is None or rect.w <= 0 or rect.h <= 0:
            return
        try:
            src = image.convert("RGBA")
            sw, sh = src.size
            if sw <= 0 or sh <= 0:
                src.close()
                return

            if fit == "cover":
                scale = max(rect.w / sw, rect.h / sh)
                nw, nh = max(1, int(sw * scale)), max(1, int(sh * scale))
                with src.resize((nw, nh), Image.Resampling.LANCZOS) as scaled:
                    # center-crop to exact container
                    left = (nw - rect.w) // 2
                    top = (nh - rect.h) // 2
                    cropped = scaled.crop((left, top, left + rect.w, top + rect.h))
                    self.image.paste(cropped, (rect.x, rect.y), cropped)
                    cropped.close()
            elif fit == "stretch":
                with src.resize((rect.w, rect.h), Image.Resampling.LANCZOS) as resized:
                    self.image.paste(resized, (rect.x, rect.y), resized)
            elif fit in ("fill-height", "fill-width"):
                if fit == "fill-height":
                    nw = max(1, int(sh * rect.h / sh) if sh else 1)
                    nw = max(1, int(sw * rect.h / sh))
                    nh = rect.h
                else:
                    nh = max(1, int(sh * rect.w / sw))
                    nw = rect.w
                with src.resize((nw, nh), Image.Resampling.LANCZOS) as resized:
                    x = rect.x + (rect.w - nw) // 2
                    y = rect.y + (rect.h - nh) // 2
                    self.image.paste(resized, (x, y), resized)
            else:  # contain (default)
                scale = min(rect.w / sw, rect.h / sh)
                if scale >= 1.0:
                    nw, nh = sw, sh
                else:
                    nw, nh = max(1, int(sw * scale)), max(1, int(sh * scale))
                if bg_color is not None and (nw < rect.w or nh < rect.h):
                    box = (rect.x, rect.y, rect.x + rect.w, rect.y + rect.h)
                    ImageDraw.Draw(self.image).rectangle(box, fill=bg_color)
                x = rect.x + (rect.w - nw) // 2
                y = rect.y + (rect.h - nh) // 2
                with src.resize((nw, nh), Image.Resampling.LANCZOS) as resized:
                    self.image.paste(resized, (x, y), resized)
            src.close()
        except Exception as e:  # noqa: BLE001
            logger.error(f"fit_image failed ({fit}): {e}")
            src_final = locals().get("src")
            if src_final is not None and not getattr(src_final, "_closed", False):
                try:
                    src_final.close()
                except Exception:
                    pass

    def to_image(self) -> Image.Image:
        """Returns final RGB PIL Image ready for display rendering."""
        return self.image.convert("RGB")

