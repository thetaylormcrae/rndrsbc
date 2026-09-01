"""
rndrSBC - First-Run Onboarding Widget
Shows a scannable QR claim-code and simple WiFi/AP onboarding instructions on
the e-Paper display so a phone can claim the device and complete setup.
"""

import os
import json
import time
from PIL import Image

from core.canvas import ResponsiveCanvas, Rect
from widgets.base import BaseWidget, register_widget


def _claim_url():
    """Build the claim URL from the persisted onboarding state.

    Reads the claim token directly so the QR widget keeps working even when
    the ``server`` package isn't importable from the widget's sys.path (the
    fragile import-then-fallback path previously returned a useless
    ``http://rndrsbc.local`` placeholder, which rendered a QR that could not
    actually claim the device).
    """
    try:
        from server.onboarding import claim_url_for_token, onboarding_state
        state = onboarding_state()
        token = state.get("token")
        return claim_url_for_token(token), state
    except Exception:
        # Robust local fallback: read .claim_token like server.onboarding does.
        import glob
        candidates = [
            os.path.join(os.path.expanduser("~/.rndrsbc"), ".claim_token"),
            "/home/pi/.rndrsbc/.claim_token",
            "/home/logiadmin/.rndrsbc/.claim_token",
            "/var/lib/rndrsbc/.claim_token",
        ]
        token = ""
        for c in candidates:
            if os.path.isfile(c):
                try:
                    with open(c) as fh:
                        token = fh.read().strip()
                except Exception:
                    pass
                if token:
                    break
        if not token:
            # Last-ditch: scan for any .claim_token under the home dir.
            for c in glob.glob(os.path.expanduser("~/.rndrsbc/**/.claim_token"), recursive=True):
                try:
                    with open(c) as fh:
                        token = fh.read().strip()
                except Exception:
                    pass
                if token:
                    break
        from urllib.parse import urlencode
        claim_url = f"https://app.rool.cloud/claim?token={token}" if token else "http://rndrsbc.local"
        return claim_url, {"token": token, "ap_active": False}


@register_widget("onboarding", "Device Setup & QR Claim")
class OnboardingWidget(BaseWidget):
    """Displays a QR claim code + quick instructions for first-run provisioning."""

    name = "Device Setup"
    description = "QR claim-token onboarding + AP recovery instructions"
    default_interval_minutes = 1

    def get_config_schema(self) -> dict:
        return {
            "fields": [
                {"name": "title", "label": "Header Title", "type": "string", "default": "Let's set up your display"},
            ]
        }

    def render(self, dimensions: tuple[int, int], settings: dict, bounds: Rect = None) -> Image.Image:
        try:
            import qrcode  # declared hard dep: qrcode[pil]>=7.4.0 in pyproject (no lazy pip)
        except ImportError:
            raise RuntimeError("qrcode is required for the onboarding widget (pip install 'qrcode[pil]')")

        url, state = _claim_url()
        token = state.get("token", "")
        ap_active = state.get("ap_active", False)

        qr = qrcode.QRCode(box_size=10, border=2)
        qr.add_data(url)
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")

        with ResponsiveCanvas(dimensions, bg_color="#ffffff") as canvas:
            content = bounds if bounds is not None else canvas.bounds.inset(canvas.pt(28))

            # Split 45% QR / 55% Text
            qr_box, info_box = content.split_columns([4.5, 5.5], gap=canvas.pt(32))

            # Draw QR container
            canvas.draw_card(qr_box, radius=12, fill="#ffffff", outline="#000000", width=2)
            qr_inner = qr_box.inset(canvas.pt(16))
            qr_size = min(qr_inner.w, qr_inner.h)
            qr_resized = qr_img.resize((qr_size, qr_size), Image.Resampling.LANCZOS)
            qr_x = qr_inner.x + (qr_inner.w - qr_size) // 2
            qr_y = qr_inner.y + (qr_inner.h - qr_size) // 2
            canvas.image.paste(qr_resized, (qr_x, qr_y))

            # Info column
            title_font = canvas.get_token_font("title")
            subhead_font = canvas.get_token_font("subhead")
            body_font = canvas.get_token_font("body")
            caption_font = canvas.get_token_font("caption")

            # Title
            canvas.draw_text(settings.get("title", "Let's set up your display"),
                             (info_box.x, info_box.y + canvas.pt(8)), font=title_font, fill="#000000")

            if ap_active:
                canvas.draw_text("Temporary setup network is live",
                                 (info_box.x, info_box.y + canvas.pt(60)), font=subhead_font, fill="#e65c00")
                steps = [
                    ("1. Join Wi-Fi network", "rndrSBC-Setup-XXXX (no password)"),
                    ("2. Open browser", "Go to http://rndrsbc.local"),
                    ("3. Enter your Wi-Fi credentials", "Display joins your home network automatically")
                ]
            else:
                canvas.draw_text("Scan this QR with your phone",
                                 (info_box.x, info_box.y + canvas.pt(60)), font=subhead_font, fill="#000000")
                steps = [
                    ("1. Connect to the same network", "Ensure phone is on your local Wi-Fi"),
                    ("2. Scan with camera", "Directly opens the device setup portal"),
                    ("3. Set admin password", "Choose credentials and start rendering")
                ]

            y_pos = info_box.y + canvas.pt(105)
            for title, desc in steps:
                canvas.draw_text(title, (info_box.x, y_pos), font=body_font, fill="#000000")
                canvas.draw_text(desc, (info_box.x, y_pos + canvas.pt(20)), font=caption_font, fill="#666666")
                y_pos += canvas.pt(48)

            # Footer
            tok_label = f"Claim Token: {token[:12]}..." if token else "Ready to claim"
            canvas.draw_text(tok_label, (info_box.x, info_box.bottom - canvas.pt(30)), font=caption_font, fill="#555555")
            clean_url = url.replace("http://", "")
            canvas.draw_text(f"Or visit: {clean_url}", (info_box.x, info_box.bottom - canvas.pt(14)), font=caption_font, fill="#888888")

            if bounds is None:
                canvas.draw_frame("Corner", color="#111111")

            return canvas.to_image()
