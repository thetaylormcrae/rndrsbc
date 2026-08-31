"""
rndrSBC - Crypto & Market Ticker Widget
Displays live prices, 24h price change, and market trends for popular
cryptocurrencies (BTC, ETH, SOL) and market assets using CoinGecko API.
"""

import logging
from PIL import Image

from core.canvas import ResponsiveCanvas, Rect
from widgets.base import BaseWidget, register_widget

logger = logging.getLogger("rndrSBC.crypto")

COINGECKO_API = (
    "https://api.coingecko.com/api/v3/simple/price"
    "?ids=bitcoin,ethereum,solana,cardano,ripple&vs_currencies=usd,eur,gbp"
    "&include_24hr_change=true"
)


@register_widget("crypto", "Crypto & Markets")
class CryptoWidget(BaseWidget):
    name = "Crypto & Markets"
    description = "Live Bitcoin, Ethereum, and cryptocurrency price tracker"
    default_interval_minutes = 10

    def get_config_schema(self) -> dict:
        return {
            "fields": [
                {
                    "name": "currency",
                    "label": "Display Currency",
                    "type": "select",
                    "options": ["USD ($)", "EUR (€)", "GBP (£)"],
                    "default": "USD ($)"
                },
                {
                    "name": "coins",
                    "label": "Tracked Assets",
                    "type": "string",
                    "default": "bitcoin,ethereum,solana"
                }
            ]
        }

    def _resolve_currency(self, settings: dict) -> tuple[str, str]:
        c = settings.get("currency", "USD ($)")
        if "EUR" in c:
            return "eur", "€"
        elif "GBP" in c:
            return "gbp", "£"
        return "usd", "$"

    def render(self, dimensions: tuple[int, int], settings: dict, bounds: Rect = None) -> Image.Image:
        curr_key, curr_sym = self._resolve_currency(settings)
        
        # Async cached fetch with 5min TTL
        data, is_stale = self.fetch_remote_json(COINGECKO_API, ttl=300, default={})

        assets = [
            {"id": "bitcoin", "name": "Bitcoin", "symbol": "BTC", "fallback_price": 64250.0, "fallback_chg": 2.4},
            {"id": "ethereum", "name": "Ethereum", "symbol": "ETH", "fallback_price": 3450.0, "fallback_chg": -1.1},
            {"id": "solana", "name": "Solana", "symbol": "SOL", "fallback_price": 148.5, "fallback_chg": 5.8},
        ]

        for a in assets:
            coin_data = data.get(a["id"], {}) if isinstance(data, dict) else {}
            price = coin_data.get(curr_key, a["fallback_price"])
            chg = coin_data.get(f"{curr_key}_24h_change", a["fallback_chg"])
            a["price"] = price
            a["change"] = chg

        with ResponsiveCanvas(dimensions, bg_color="#ffffff") as canvas:
            content = bounds if bounds is not None else canvas.bounds.inset(canvas.pt(16))
            h_box, b_box = content.split_rows([1.0, 8.0], gap=canvas.pt(10))

            # Header
            font_title = canvas.get_token_font("title")
            font_sub = canvas.get_token_font("body")
            canvas.draw_text("⚡  Crypto & Market Overview", (h_box.x, h_box.y), font=font_title, fill="#111111")
            now = self.get_local_now(settings=settings)
            status_txt = f"{'Stale · ' if is_stale else ''}{now.strftime('%I:%M %p')}"
            canvas.draw_text(status_txt, (h_box.right, h_box.y + canvas.pt(6)), font=font_sub, fill="#666666", anchor="ra")

            # Asset Cards (3 Columns)
            col_boxes = b_box.split_columns([1, 1, 1], gap=canvas.pt(12))

            font_symbol = canvas.get_token_font("caption")
            font_name = canvas.get_token_font("headline")
            font_price = canvas.get_token_font("hero")
            font_chg = canvas.get_token_font("body")

            for a, box in zip(assets, col_boxes):
                canvas.draw_card(box, radius=8, fill="#ffffff", outline="#111111", width=1)
                
                # Symbol badge
                canvas.draw_text(a["symbol"], (box.x + canvas.pt(14), box.y + canvas.pt(14)), font=font_symbol, fill="#e65c00")
                canvas.draw_text(a["name"], (box.x + canvas.pt(14), box.y + canvas.pt(32)), font=font_name, fill="#111111")
                
                # Price
                p = a["price"]
                p_str = f"{curr_sym}{p:,.2f}" if p < 1000 else f"{curr_sym}{p:,.0f}"
                canvas.draw_text(p_str, (box.x + canvas.pt(14), box.y + canvas.pt(72)), font=font_price, fill="#111111")

                # 24h Change
                chg = a["change"]
                arrow = "▲" if chg >= 0 else "▼"
                chg_str = f"{arrow} {abs(chg):.2f}% (24h)"
                chg_col = "#2e7d32" if chg >= 0 else "#c62828"
                canvas.draw_text(chg_str, (box.x + canvas.pt(14), box.bottom - canvas.pt(24)), font=font_chg, fill=chg_col)

            if bounds is None:
                canvas.draw_frame("Corner", color="#111111")

            return canvas.to_image()
