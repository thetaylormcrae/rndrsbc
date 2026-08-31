"""
rndrSBC - Daily Quotes & Inspiration Widget
Renders inspirational quotes, philosophical thoughts, and word-of-the-day
with elegant typography, large pull-quotes, and author attribution.
"""

import time
import random
import logging
from PIL import Image

from core.canvas import ResponsiveCanvas, Rect
from widgets.base import BaseWidget, register_widget

logger = logging.getLogger("rndrSBC.quotes")

FALLBACK_QUOTES = [
    {"quote": "Simplicity is the ultimate sophistication.", "author": "Leonardo da Vinci"},
    {"quote": "The only way to do great work is to love what you do.", "author": "Steve Jobs"},
    {"quote": "It always seems impossible until it's done.", "author": "Nelson Mandela"},
    {"quote": "We are what we repeatedly do. Excellence, then, is not an act, but a habit.", "author": "Aristotle"},
    {"quote": "In the middle of difficulty lies opportunity.", "author": "Albert Einstein"},
    {"quote": "Do what you can, with what you have, where you are.", "author": "Theodore Roosevelt"},
    {"quote": "The journey of a thousand miles begins with one step.", "author": "Lao Tzu"},
    {"quote": "Creativity is intelligence having fun.", "author": "Albert Einstein"},
    {"quote": "Action is the foundational key to all success.", "author": "Pablo Picasso"},
]


@register_widget("quotes", "Daily Quotes & Thoughts")
class QuotesWidget(BaseWidget):
    name = "Daily Quotes"
    description = "Daily inspirational quotes, philosophy, and ideas"
    default_interval_minutes = 60

    def get_config_schema(self) -> dict:
        return {
            "fields": [
                {
                    "name": "category",
                    "label": "Quote Style",
                    "type": "select",
                    "options": ["Inspirational", "Philosophy", "Minimalist"],
                    "default": "Inspirational"
                },
                {
                    "name": "custom_quote",
                    "label": "Custom Quote (Overrides)",
                    "type": "string",
                    "default": ""
                },
                {
                    "name": "custom_author",
                    "label": "Custom Author",
                    "type": "string",
                    "default": ""
                }
            ]
        }

    def _get_quote(self, settings: dict) -> tuple[str, str]:
        # Custom override
        custom_q = settings.get("custom_quote", "").strip()
        if custom_q:
            return custom_q, settings.get("custom_author", "Custom").strip() or "Anonymous"

        # Try online quote API (ZenQuotes / Quotable) with 6-hour cache
        url = "https://zenquotes.io/api/today"
        data, is_stale = self.fetch_remote_json(url, ttl=21600, default=[])
        if isinstance(data, list) and len(data) > 0 and "q" in data[0]:
            return data[0]["q"], data[0].get("a", "Unknown")

        # Deterministic day-of-year rotation through fallback quotes
        day_idx = int(time.time() / 86400) % len(FALLBACK_QUOTES)
        item = FALLBACK_QUOTES[day_idx]
        return item["quote"], item["author"]

    def render(self, dimensions: tuple[int, int], settings: dict, bounds: Rect = None) -> Image.Image:
        quote_text, author = self._get_quote(settings)

        with ResponsiveCanvas(dimensions, bg_color="#ffffff") as canvas:
            content = bounds if bounds is not None else canvas.bounds.inset(canvas.pt(24))
            
            # Card background
            canvas.draw_card(content, radius=10, fill="#ffffff", outline="#111111", width=1)
            inner = content.inset(canvas.pt(20))

            # Header / Tag
            font_tag = canvas.get_token_font("caption")
            canvas.draw_text("✦  THOUGHT OF THE DAY", (inner.x, inner.y), font=font_tag, fill="#e65c00")

            # Large Quotation Mark
            font_hero = canvas.get_token_font("hero")
            canvas.draw_text("“", (inner.x, inner.y + canvas.pt(16)), font=font_hero, fill="#d1d5db")

            # Quote Body (formatted nicely across lines)
            font_quote = canvas.get_token_font("headline")
            words = quote_text.split()
            lines = []
            cur_line = []
            max_line_len = int(inner.w / canvas.pt(11))
            
            for w in words:
                if len(" ".join(cur_line + [w])) <= max_line_len:
                    cur_line.append(w)
                else:
                    lines.append(" ".join(cur_line))
                    cur_line = [w]
            if cur_line:
                lines.append(" ".join(cur_line))

            y_text = inner.y + canvas.pt(60)
            line_height = canvas.pt(26)
            for line in lines[:5]:
                canvas.draw_text(line, (inner.x + canvas.pt(8), y_text), font=font_quote, fill="#111111")
                y_text += line_height

            # Author Attribution
            font_author = canvas.get_token_font("body")
            canvas.draw_text(f"— {author}", (inner.right - canvas.pt(16), inner.bottom - canvas.pt(20)),
                             font=font_author, fill="#4a5568", anchor="ra")

            if bounds is None:
                canvas.draw_frame("Corner", color="#111111")

            return canvas.to_image()
