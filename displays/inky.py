"""
rndrSBC - Native Pimoroni Inky Display Driver
Driver for Pimoroni Inky Impression (4.0", 5.7", 7.3") and Inky pHAT/wHAT panels.
"""

import logging
from PIL import Image
from displays.base import BaseDisplay
from core.color import quantize_image

logger = logging.getLogger("rndrSBC.inky")

class InkyDisplay(BaseDisplay):
    """Driver wrapper for Pimoroni Inky Impression & Inky Frame e-Paper displays."""

    # Inky Impression 4/5.7/7.3 (7-color) do NOT expose a safe partial refresh
    # for color states — full refresh only, matching Pimoroni's guidance.
    # Legacy B/W Inky phat/what support a single-row partial window but we keep
    # them full-refresh by default for ghosting safety.
    SUPPORTS_PARTIAL_REFRESH = False
    PARTIAL_PRESERVES_GRAYSCALE = False
    PARTIAL_RECHARGE_LIMIT = 10

    # Inky panel presets: model -> (width, height)
    _PRESETS = {
        "impression_7_3": (800, 480),
        "impression_5_7": (600, 448),
        "impression_4_0": (640, 400),
        "what": (400, 300),
        "phat": (250, 122),
    }

    def __init__(self, model: str = "impression_7_3", orientation: int = 0):
        super().__init__()
        self.model = model
        self.orientation = orientation
        self._inky = None

        self.width, self.height = self._PRESETS.get(model, (800, 480))

    def get_resolution(self) -> tuple[int, int]:
        if self.orientation in [90, 270]:
            return (self.height, self.width)
        return (self.width, self.height)

    @classmethod
    def detect(cls):
        """Return the Inky panel model actually attached, or ``None`` if none.

        Uses Pimoroni's ``inky.auto()`` to identify the connected panel; raises no
        error if hardware is absent (no SPI panel, CI, laptop), returning ``None``
        so callers can fall back to a virtual display.
        """
        try:
            from inky.auto import auto
        except Exception:
            return None
        try:
            inky = auto()
        except Exception:
            return None
        # inky exposes the detected model via ``type`` metadata or resolution maps;
        # map a detected panel back to a config ``model`` string when derivable.
        res = getattr(inky, "resolution", None)
        if res is None:
            return None
        for model, dims in cls._PRESETS.items():
            if tuple(dims) == tuple(res):
                return model
        # Fall back to the most common 7-color panel if the driver object resolves
        # but has no recognized resolution descriptor.
        return "impression_7_3"

    def init_hardware(self):
        try:
            from inky.auto import auto
            self._inky = auto()
            logger.info(f"Connected to Inky hardware: {self._inky.resolution}")
        except Exception as e:
            logger.warning(f"[Inky] Hardware not detected ({e}). Running in simulation mode.")
            self._inky = None

    def update(self, canvas: Image.Image, dirty_rects: list = None):
        """Quantizes (if needed) and flushes the buffer to Inky hardware."""
        image = canvas
        if self.orientation != 0:
            image = image.rotate(-self.orientation, expand=True)

        if image.size != (self.width, self.height):
            image = image.resize((self.width, self.height), Image.Resampling.LANCZOS)

        if self._inky is not None:
            self._inky.set_image(image)
            self._inky.show()
            logger.info("Successfully refreshed Inky Impression screen.")
        else:
            logger.info(f"[Inky Mock] Flashed image {image.size} in {self.model} mode.")

    def display_image(self, image: Image.Image):
        self.update(image)

    def sleep(self):
        pass
