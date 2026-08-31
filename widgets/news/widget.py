"""
rndrSBC - News & RSS Feed Widget
Fetches and displays top headlines from RSS and Atom feeds with clean typography.
Compatible with BBC, Hacker News, Reuters, NYT, and any custom RSS feed.
"""

import re
import html
import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from PIL import Image

from core.canvas import ResponsiveCanvas, Rect
from widgets.base import BaseWidget, register_widget

logger = logging.getLogger("rndrSBC.news")

DEFAULT_FEEDS = {
    "bbc": "https://feeds.bbci.co.uk/news/world/rss.xml",
    "hackernews": "https://news.ycombinator.com/rss",
    "reuters": "https://www.reutersagency.com/feed/?taxonomy=best-topics&post_type=best",
    "nyt": "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
}


def _strip_tags(text: str) -> str:
    """Removes HTML tags and unescapes entities."""
    clean = re.sub(r"<[^>]+>", "", text or "")
    return html.unescape(clean).strip()


def parse_rss(xml_text: str, max_items: int = 4) -> list[dict]:
    """Parses standard RSS 2.0 and Atom feeds into a normalized list of items."""
    items = []
    if not xml_text:
        return items

    try:
        root = ET.fromstring(xml_text)
        # Check RSS 2.0 channel -> item
        channel = root.find("channel")
        if channel is not None:
            for el in channel.findall("item")[:max_items]:
                title = _strip_tags(el.findtext("title", ""))
                pub_date = el.findtext("pubDate", "")
                desc = _strip_tags(el.findtext("description", ""))
                if title:
                    items.append({"title": title, "date": pub_date, "desc": desc})
            return items

        # Check Atom feed -> entry
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        for el in (root.findall("entry") or root.findall("atom:entry", ns))[:max_items]:
            title = _strip_tags(el.findtext("title", "") or el.findtext("atom:title", "", ns))
            pub_date = el.findtext("updated", "") or el.findtext("atom:updated", "", ns) or el.findtext("published", "")
            summary = _strip_tags(el.findtext("summary", "") or el.findtext("atom:summary", "", ns))
            if title:
                items.append({"title": title, "date": pub_date, "desc": summary})
    except Exception as e:
        logger.warning(f"Error parsing RSS/Atom XML: {e}")

    return items


@register_widget("news", "News & RSS Feed")
class NewsWidget(BaseWidget):
    name = "News & RSS"
    description = "Live headlines from RSS and Atom news feeds"
    default_interval_minutes = 15

    def get_config_schema(self) -> dict:
        return {
            "fields": [
                {
                    "name": "feed_source",
                    "label": "Feed Preset",
                    "type": "select",
                    "options": ["BBC World News", "Hacker News", "Reuters", "New York Times", "Custom URL"],
                    "default": "BBC World News"
                },
                {
                    "name": "custom_url",
                    "label": "Custom RSS URL",
                    "type": "string",
                    "default": ""
                },
                {
                    "name": "max_stories",
                    "label": "Max Headlines",
                    "type": "number",
                    "default": 4
                }
            ]
        }

    def _resolve_feed_url(self, settings: dict) -> tuple[str, str]:
        source = settings.get("feed_source", "BBC World News")
        if source == "Hacker News":
            return DEFAULT_FEEDS["hackernews"], "Hacker News"
        elif source == "Reuters":
            return DEFAULT_FEEDS["reuters"], "Reuters"
        elif source == "New York Times":
            return DEFAULT_FEEDS["nyt"], "New York Times"
        elif source == "Custom URL":
            url = settings.get("custom_url", "").strip()
            return (url, "Live Feed") if url else (DEFAULT_FEEDS["bbc"], "BBC World")
        return DEFAULT_FEEDS["bbc"], "BBC World News"

    def render(self, dimensions: tuple[int, int], settings: dict, bounds: Rect = None) -> Image.Image:
        feed_url, source_name = self._resolve_feed_url(settings)
        max_stories = int(settings.get("max_stories", 4) or 4)

        # Thread-safe cached fetch (15min TTL)
        xml_content, is_stale = self.fetch_remote_text(feed_url, ttl=900, timeout=8)
        stories = parse_rss(xml_content, max_items=max_stories)

        if not stories:
            stories = [
                {"title": "Global Climate & Energy Transition Accelerates Across Industry", "date": "", "desc": ""},
                {"title": "Next-Generation Single Board Computers Redefine Edge Intelligence", "date": "", "desc": ""},
                {"title": "Open Source e-Paper Operating Platforms Reach Milestone Release", "date": "", "desc": ""},
                {"title": "Breakthrough in Low-Power Ambient Display Technology", "date": "", "desc": ""}
            ]

        with ResponsiveCanvas(dimensions, bg_color="#ffffff") as canvas:
            content = bounds if bounds is not None else canvas.bounds.inset(canvas.pt(16))
            h_box, b_box = content.split_rows([1.0, 8.5], gap=canvas.pt(8))

            # Header
            font_title = canvas.get_token_font("title")
            font_sub = canvas.get_token_font("body")
            canvas.draw_text(f"📰  {source_name}", (h_box.x, h_box.y), font=font_title, fill="#111111")
            now = self.get_local_now(settings=settings)
            status_txt = f"{'Stale · ' if is_stale else ''}{now.strftime('%I:%M %p')}"
            canvas.draw_text(status_txt, (h_box.right, h_box.y + canvas.pt(6)), font=font_sub, fill="#666666", anchor="ra")

            # Stories Rows
            num_rows = max(1, min(len(stories), 5))
            row_boxes = b_box.split_rows([1] * num_rows, gap=canvas.pt(8))

            font_headline = canvas.get_token_font("headline")
            font_meta = canvas.get_token_font("caption")

            for i, (story, box) in enumerate(zip(stories, row_boxes)):
                canvas.draw_card(box, radius=6, fill="#ffffff", outline="#e2e8f0", width=1)
                
                # Number badge
                badge_w = canvas.pt(24)
                badge_box = Rect(box.x + canvas.pt(8), box.y + canvas.pt(8), badge_w, box.h - canvas.pt(16))
                canvas.draw_card(badge_box, radius=4, fill="#111111", outline=None)
                canvas.draw_text(str(i + 1), (badge_box.x + badge_w / 2, badge_box.y + canvas.pt(4)),
                                 font=font_headline, fill="#ffffff", anchor="ma")

                # Headline text
                text_x = box.x + badge_w + canvas.pt(16)
                text_w = box.w - badge_w - canvas.pt(28)
                
                title = story["title"]
                if len(title) > 90:
                    title = title[:88] + "…"
                canvas.draw_text(title, (text_x, box.y + canvas.pt(10)), font=font_headline, fill="#111111")

            if bounds is None:
                canvas.draw_frame("Corner", color="#111111")

            return canvas.to_image()
