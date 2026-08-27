"""
rndrSBC - Native Linux Framebuffer Driver (/dev/fb0)
Direct byte stream memory writing for LCD, TFT, and HDMI display HATs.
"""

import os
import logging
from PIL import Image
from displays.base import BaseDisplay

logger = logging.getLogger("rndrSBC.framebuffer")

class FramebufferDisplay(BaseDisplay):
    """Direct Linux Framebuffer device writer with RGB565 / RGB888 packing."""

    # LCD/OLED/TFT framebuffers update natively per-pixel — partial refresh
    # is always safe and grayscale/color preserving.
    SUPPORTS_PARTIAL_REFRESH = True
    PARTIAL_PRESERVES_GRAYSCALE = True
    PARTIAL_RECHARGE_LIMIT = 999999

    def __init__(self, fb_path="/dev/fb0", width=800, height=480, bpp=16, orientation=0):
        super().__init__()
        self.fb_path = fb_path
        self.width = width
        self.height = height
        self.bpp = bpp
        self.orientation = orientation
        self._fb_file = None

    def get_resolution(self) -> tuple[int, int]:
        if self.orientation in [90, 270]:
            return (self.height, self.width)
        return (self.width, self.height)

    def init_hardware(self):
        if os.path.exists(self.fb_path):
            try:
                self._fb_file = open(self.fb_path, "wb")
                logger.info(f"[Framebuffer] Opened {self.fb_path} ({self.width}x{self.height}, {self.bpp}bpp)")
            except Exception as e:
                logger.error(f"[Framebuffer] Failed to open {self.fb_path}: {e}")
                self._fb_file = None
        else:
            logger.info(f"[Framebuffer Mock] {self.fb_path} not found. Running in simulation mode.")

    def update(self, canvas: Image.Image, dirty_rects: list = None):
        """Streams raw pixel byte buffer directly into the framebuffer."""
        image = canvas
        if self.orientation != 0:
            image = image.rotate(-self.orientation, expand=True)

        if image.size != (self.width, self.height):
            image = image.resize((self.width, self.height), Image.Resampling.LANCZOS)

        if self._fb_file is not None:
            try:
                if self.bpp == 16:
                    # Convert to RGB565 (5 bits R, 6 bits G, 5 bits B)
                    raw_rgb = image.convert("RGB")
                    pixels = list(raw_rgb.getdata())
                    fb_bytes = bytearray(len(pixels) * 2)
                    for idx, (r, g, b) in enumerate(pixels):
                        rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
                        fb_bytes[idx*2] = rgb565 & 0xFF
                        fb_bytes[idx*2 + 1] = (rgb565 >> 8) & 0xFF
                    # Partial update: write only the dirty-region rows (LCD/TFT
                    # framebuffers update per-pixel natively, so this is safe).
                    if dirty_rects and self.SUPPORTS_PARTIAL_REFRESH and self.refresh_mode == "auto":
                        row_bytes = self.width * 2
                        stride = 2
                        for rect in dirty_rects:
                            x0, y0, x1, y1 = rect
                            x0 = max(0, int(x0)); y0 = max(0, int(y0))
                            x1 = min(self.width, int(x1)); y1 = min(self.height, int(y1))
                            for y in range(y0, y1):
                                self._fb_file.seek(y * row_bytes + x0 * stride)
                                self._fb_file.write(fb_bytes[y*row_bytes + x0*stride : y*row_bytes + x1*stride])
                        logger.info(f"[Framebuffer] Partial written {len(dirty_rects)} region(s) to {self.fb_path}")
                    else:
                        self._fb_file.seek(0)
                        self._fb_file.write(fb_bytes)
                        logger.info(f"[Framebuffer] Streamed full frame to {self.fb_path}")
                else: # 32-bit / 24-bit
                    raw_rgba = image.convert("RGBA")
                    self._fb_file.seek(0)
                    self._fb_file.write(raw_rgba.tobytes())
                    logger.info(f"[Framebuffer] Streamed frame buffer to {self.fb_path}")
                self._fb_file.flush()
            except Exception as e:
                logger.error(f"[Framebuffer] Write failed: {e}")
        else:
            logger.info(f"[Framebuffer Mock] Frame write {image.size} completed.")

    def display_image(self, image: Image.Image):
        self.update(image)

    def sleep(self):
        pass
