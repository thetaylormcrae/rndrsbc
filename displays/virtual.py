"""
rndrSBC - Virtual Display Driver
Simulates an e-paper display on Windows, Mac, or headless servers by saving PNG previews and updating a live mirror.
"""

from PIL import Image
import os
import logging
from displays.base import BaseDisplay

logger = logging.getLogger("rndrSBC.virtual_display")

class VirtualDisplay(BaseDisplay):
    def __init__(self, width=800, height=480, output_path="live_screen.png"):
        super().__init__()
        self.width = width
        self.height = height
        # Anchor a bare filename to the writable deployment home; a relative
        # path would land in the process CWD (unwritable under systemd).
        self.output_path = self._anchored(output_path)
        self.last_image = None

    def get_resolution(self) -> tuple[int, int]:
        return (self.width, self.height)

    @staticmethod
    def _anchored(path: str) -> str:
        """Anchor a bare filename into the writable deployment home so the
        preview never lands in the process CWD (unwritable under systemd)."""
        if os.path.isabs(path) or "/" in path:
            return path
        from core import paths
        return paths.resolve(path)

    def set_resolution(self, width: int, height: int):
        """Hot-resize the virtual canvas so the live preview reflects a new
        screen size immediately (no restart needed)."""
        self.width = int(width)
        self.height = int(height)
        logger.info(
            f"[VirtualDisplay] Resized preview target to {self.width}x{self.height} "
            f"({self.output_path})."
        )

    def init_hardware(self):
        pass

    def update(self, canvas: Image.Image, dirty_rects: list = None):
        """Saves active canvas buffer to disk and keeps mirror reference."""
        self.last_image = canvas.copy()
        canvas.save(self.output_path)
        logger.info(f"[VirtualDisplay] Updated preview image: {self.output_path} ({self.width}x{self.height})")

    def display_image(self, image: Image.Image):
        self.update(image)

    def sleep(self):
        pass

