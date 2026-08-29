"""
rndrSBC - Native Pimoroni Inky Display Driver
Driver for Pimoroni Inky Impression (4.0", 5.7", 7.3") and Inky pHAT/wHAT panels.
Matches InkyPi architecture for reliable 7-color / 6-color e-paper rendering.
"""

import logging
from PIL import Image
from displays.base import BaseDisplay

logger = logging.getLogger("rndrSBC.inky")


class InkyDisplay(BaseDisplay):
    """Driver wrapper for Pimoroni Inky Impression & Inky Frame e-Paper displays."""

    SUPPORTS_PARTIAL_REFRESH: bool = False
    color_mode: str = "7color"

    # Supported hardware presets (width, height) in landscape
    _PRESETS = {
        "impression_7_3": (800, 480),
        "spectra73": (800, 480),
        "spectra6": (800, 480),
        "impression_5_7": (600, 448),
        "impression_4_0": (640, 400),
        "what": (400, 300),
        "phat": (250, 122),
    }

    def __init__(self, model: str = "impression_7_3", orientation: int = 0, saturation: float = 0.5, **kwargs):
        super().__init__()
        self.model = model
        self.orientation = orientation
        self.saturation = saturation
        self._inky = None

        base_dims = self._PRESETS.get(model, (800, 480))
        self.width, self.height = base_dims
        self.init_hardware()

    def get_resolution(self) -> tuple[int, int]:
        if self._inky is not None and hasattr(self._inky, "resolution"):
            w, h = self._inky.resolution
        else:
            w, h = self._PRESETS.get(self.model, (800, 480))
        if self.orientation in [90, 270]:
            return (h, w)
        return (w, h)

    @classmethod
    def detect(cls):
        """Return the Inky panel model actually attached, or None if none."""
        try:
            from inky.auto import auto
            inky = auto()
            res = getattr(inky, "resolution", None)
            if res is None:
                return None
            w, h = res
            if (w, h) in [(800, 480), (480, 800)]:
                return "impression_7_3"
            elif (w, h) in [(600, 448), (448, 600)]:
                return "impression_5_7"
            elif (w, h) in [(640, 400), (400, 640), (600, 400)]:
                return "impression_4_0"
            elif (w, h) in [(400, 300), (300, 400)]:
                return "what"
            elif (w, h) in [(250, 122), (122, 250)]:
                return "phat"
            return "impression_7_3"
        except Exception:
            return None

    def init_hardware(self):
        # 1. Try I2C EEPROM auto-detection first (matches InkyPi)
        try:
            from inky.auto import auto
            self._inky = auto()
            if hasattr(self._inky, "resolution"):
                self.width, self.height = self._inky.resolution
            logger.info(f"[Inky] Connected to Inky hardware via auto-detect: {self.width}x{self.height}")
            return
        except Exception as e:
            logger.debug(f"[Inky] inky.auto() did not initialize ({e}), falling back to model '{self.model}'")

        # 2. Direct hardware initialization fallback using explicit model
        try:
            if self.model in ["impression_7_3", "7colour", "spectra73", "spectra6"]:
                try:
                    from inky.inky_e673 import Inky as InkyE673
                    self._inky = InkyE673(resolution=(800, 480))
                except Exception:
                    from inky.inky_ac073tc1a import Inky as InkyAC073TC1A
                    self._inky = InkyAC073TC1A(resolution=(800, 480))
            elif self.model == "impression_5_7":
                from inky.inky_uc8159 import Inky as InkyUC8159
                self._inky = InkyUC8159(resolution=(600, 448))
            elif self.model == "impression_4_0":
                try:
                    from inky.inky_e640 import Inky as InkyE640
                    self._inky = InkyE640(resolution=(600, 400))
                except Exception:
                    from inky.inky_uc8159 import Inky as InkyUC8159
                    self._inky = InkyUC8159(resolution=(640, 400))
            elif self.model == "what":
                from inky.what import InkyWHAT
                self._inky = InkyWHAT("red")
            elif self.model == "phat":
                from inky.phat import InkyPHAT
                self._inky = InkyPHAT("red")

            if self._inky is not None:
                if hasattr(self._inky, "resolution"):
                    self.width, self.height = self._inky.resolution
                logger.info(f"[Inky] Connected to Inky hardware via direct driver '{self.model}': {self.width}x{self.height}")
        except Exception as e:
            logger.warning(f"[Inky] Direct hardware initialization failed: {e}")
            self._inky = None

    def update(self, canvas: Image.Image, dirty_rects: list = None):
        """Displays the provided RGB image on the Inky display (matching InkyPi)."""
        image = canvas.convert("RGB")

        # Apply orientation rotation
        if self.orientation == 90:
            image = image.rotate(90, expand=True)
        elif self.orientation == 180:
            image = image.rotate(180, expand=True)
        elif self.orientation == 270:
            image = image.rotate(270, expand=True)

        target_res = (self.width, self.height)
        if image.size != target_res:
            image = image.resize(target_res, Image.Resampling.LANCZOS)

        if self._inky is not None:
            self._inky.set_image(image, saturation=self.saturation)
            self._inky.show()
            logger.info(f"Successfully refreshed Inky Impression screen ({self.width}x{self.height}).")
        else:
            logger.info(f"[Inky Mock] Flashed image {image.size} in {self.model} mode.")

    def display_image(self, image: Image.Image):
        """Backwards compatibility alias for update()."""
        self.update(image)

    def sleep(self):
        """No-op sleep for Inky (sleeps automatically)."""
        pass
