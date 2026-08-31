"""
rndrSBC - Network Diagnostics Widget
Shows live network status (Wi-Fi SSID, signal %, IP, gateway/ping, DNS) on the
display. Useful for headless troubleshooting without SSH.
"""

import os
import subprocess
import logging
import socket
from PIL import Image

from core.canvas import ResponsiveCanvas, Rect
from widgets.base import BaseWidget, register_widget

logger = logging.getLogger("rndrSBC.network")


def _run(args, timeout=4):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except Exception:
        return ""


def _get_ssid() -> str:
    # 1. NetworkManager (Bookworm / modern Linux)
    out = _run(["nmcli", "-t", "-f", "active,ssid", "dev", "wifi"])
    for line in out.splitlines():
        if line.startswith("yes:"):
            ssid = line.split(":", 1)[1].strip()
            if ssid:
                return ssid
    # 2. iwgetid (Bullseye / legacy wpa_supplicant)
    out_iw = _run(["iwgetid", "-r"])
    if out_iw:
        return out_iw
    # 3. wpa_cli
    out_wpa = _run(["wpa_cli", "status"])
    for line in out_wpa.splitlines():
        if line.startswith("ssid="):
            return line.split("=", 1)[1].strip()
    return "Not connected"


def _get_signal() -> str:
    out = _run(["nmcli", "-t", "-f", "active,signal", "dev", "wifi"])
    for line in out.splitlines():
        if line.startswith("yes:"):
            sig = line.split(":", 1)[1].strip()
            if sig:
                return sig + "%"
    # Fallback: parse /proc/net/wireless quality metric
    try:
        with open("/proc/net/wireless", "r") as f:
            lines = f.readlines()
            if len(lines) >= 3:
                parts = lines[2].split()
                if len(parts) >= 3:
                    quality = float(parts[2].replace(".", ""))
                    pct = min(100, int((quality / 70.0) * 100))
                    return f"{pct}%"
    except Exception:
        pass
    return "—"


def _get_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "offline"


def _get_gateway() -> str:
    return _run(["ip", "route", "show", "default"]).split(" via ")[-1].split(" ")[0] or "—"


def _ping_gateway(host: str) -> str:
    if host == "—" or host.startswith("offline"):
        return "—"
    out = _run(["ping", "-c", "1", "-W", "2", host])
    if "1 received" in out or "1 packets received" in out:
        return "OK"
    return "FAIL"


def _get_hostname() -> str:
    return socket.gethostname()


@register_widget("network", "Network Diagnostics")
class NetworkWidget(BaseWidget):
    """Live Wi-Fi / gateway / IP status panel."""

    name = "Network Status"
    description = "Wi-Fi SSID, signal, IP, gateway latency"
    default_interval_minutes = 5

    def get_config_schema(self) -> dict:
        return {"fields": []}

    def render(self, dimensions: tuple[int, int], settings: dict, bounds: Rect = None) -> Image.Image:
        with ResponsiveCanvas(dimensions, bg_color="#ffffff") as canvas:
            content = bounds if bounds is not None else canvas.bounds.inset(canvas.pt(24))
            title_font = canvas.get_token_font("title")
            body_font = canvas.get_token_font("body")
            caption_font = canvas.get_token_font("caption")

            canvas.draw_text("Network Diagnostics", (content.x, content.y), font=title_font, fill="#000000")
            y = content.y + canvas.pt(52)

            hostname = _get_hostname()
            ssid = _get_ssid()
            signal = _get_signal()
            ip = _get_ip()
            gateway = _get_gateway()
            ping = _ping_gateway(gateway)

            rows = [
                ("Hostname", hostname),
                ("Wi-Fi SSID", ssid),
                ("Signal", signal),
                ("IP Address", ip),
                ("Gateway", gateway),
                ("Gateway Ping", ping),
            ]
            for label, value in rows:
                canvas.draw_text(label, (content.x, y), font=caption_font, fill="#888888")
                canvas.draw_text(value, (content.x, y + canvas.pt(16)), font=body_font, fill="#000000")
                y += canvas.pt(44)

            canvas.draw_text("No SSH? Scan QR on the onboarding widget for setup.",
                             (content.x, content.bottom - canvas.pt(14)), font=caption_font, fill="#aaaaaa")

            return canvas.to_image()
