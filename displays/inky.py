"""
rndrSBC - Native Pimoroni Inky Display Driver
Driver for Pimoroni Inky Impression (4.0", 5.7", 7.3") and Inky pHAT/wHAT panels.
"""

import logging
import os
import time
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

        # Logical resolution (what the scheduler renders to)
        # For impression_7_3, default logical is 800x480 (landscape)
        base_dims = self._PRESETS.get(model, (800, 480))
        # If preset is portrait (e.g. 480x800) but user wants landscape, or vice versa:
        self.width, self.height = base_dims
        self.init_hardware()

    def get_resolution(self) -> tuple[int, int]:
        # Logical resolution returned to scheduler
        # If model is impression_7_3, logical is 800x480 (landscape)
        if self.model == "impression_7_3":
            # Logical is landscape 800x480 unless orientation specifies otherwise
            if self.orientation in [90, 270]:
                return (480, 800)
            return (800, 480)
        
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
        # 1. Try I2C EEPROM auto-detection first
        try:
            from inky.auto import auto
            self._inky = auto()
            if hasattr(self._inky, "resolution"):
                self.width, self.height = self._inky.resolution
            logger.info(f"[Inky] Connected to Inky hardware via auto-detect: {self.width}x{self.height}")
            return
        except Exception as e:
            logger.debug(f"[Inky] inky.auto() did not initialize ({e}), falling back to model '{self.model}'")

        # 2. Fall back to direct hardware instantiation based on model
        try:
            if self.model in ("impression_7_3", "epd7in3f", "7colour", "impressions73"):
                # Try AC073TC1A (newer Inky Impression 7.3"), E673 (Spectra 7.3"), then UC8159 (Impression 7.3")
                try:
                    from inky.inky_ac073tc1a import Inky as InkyAC073TC1A
                    self._inky = InkyAC073TC1A(resolution=(800, 480))
                except Exception:
                    try:
                        from inky.inky_e673 import Inky as InkyE673
                        self._inky = InkyE673(resolution=(800, 480))
                    except Exception:
                        from inky.inky_uc8159 import Inky as InkyUC8159
                        self._inky = InkyUC8159(resolution=(800, 480))
            elif self.model in ("impression_5_7", "impressions"):
                from inky.inky_uc8159 import Inky as InkyUC8159
                self._inky = InkyUC8159(resolution=(600, 448))
            elif self.model in ("impression_4_0", "spectra40"):
                try:
                    from inky.inky_e640 import Inky as InkyE640
                    self._inky = InkyE640(resolution=(600, 400))
                except Exception:
                    from inky.inky_uc8159 import Inky as InkyUC8159
                    self._inky = InkyUC8159(resolution=(640, 400))
            elif self.model in ("what", "whatssd1683"):
                from inky.what import InkyWHAT
                self._inky = InkyWHAT("red")
            elif self.model == "phat":
                from inky.phat import InkyPHAT
                self._inky = InkyPHAT("red")
            
            if self._inky is not None:
                if hasattr(self._inky, "resolution"):
                    self.width, self.height = self._inky.resolution
                logger.info(f"[Inky] Connected to Inky hardware ({self.model}): {self.width}x{self.height}")
        except Exception as e2:
            logger.warning(f"[Inky] Hardware initialization failed ({e2}). Running in simulation mode.")
            self._inky = None

    def update(self, canvas: Image.Image, dirty_rects: list = None):
        """Quantizes (if needed) and flushes the buffer to Inky hardware."""
        image = canvas

        # ---- DEBUG: capture & report exactly what we're about to drive ----
        logger.info(
            "[Inky-debug] update() called: canvas size=%s mode=%s orientation=%d",
            canvas.size, canvas.mode, self.orientation,
        )
        if os.environ.get("RNDRSBC_INKY_DUMP"):
            try:
                dump = os.environ["RNDRSBC_INKY_DUMP"]
                os.makedirs(dump, exist_ok=True)
                p = os.path.join(dump, "inky-input-%d.png" % int(time.time()))
                canvas.convert("RGB").save(p)
                logger.info("[Inky-debug] dumped pre-driver canvas to %s", p)
            except Exception as e:  # pragma: no cover - debug only
                logger.warning("[Inky-debug] dump failed: %s", e)

        # Ensure image matches logical resolution
        logical_res = self.get_resolution()
        if image.size != logical_res:
            logger.info(
                "[Inky-debug] resizing buffer %s -> %s (logical res)",
                image.size, logical_res,
            )
            image = image.resize(logical_res, Image.Resampling.LANCZOS)

        if self._inky is not None:
            phys_w, phys_h = getattr(self._inky, "resolution", (self.width, self.height))
            logger.info("[Inky-debug] physical resolution = %s", (phys_w, phys_h))

            # If physical panel is portrait (e.g. 480x800) and logical image is landscape (800x480),
            # Pimoroni inky set_image expects physical orientation. Rotate 90 deg to fit.
            if phys_w < phys_h and image.width > image.height:
                logger.info("[Inky-debug] auto-rotating 90deg (portrait panel, landscape buffer)")
                image = image.rotate(90, expand=True)
            elif phys_w > phys_h and image.width < image.height:
                logger.info("[Inky-debug] auto-rotating 90deg (landscape panel, portrait buffer)")
                image = image.rotate(90, expand=True)

            # Apply any additional user rotation/orientation
            if self.orientation != 0:
                logger.info("[Inky-debug] applying orientation rotation=%s", self.orientation)
                image = image.rotate(-self.orientation, expand=True)

            if image.size != (phys_w, phys_h):
                logger.info("[Inky-debug] resizing buffer %s -> %s (physical res)",
                            image.size, (phys_w, phys_h))
                image = image.resize((phys_w, phys_h), Image.Resampling.LANCZOS)

            logger.info(
                "[Inky-debug] final buffer -> set_image: size=%s mode=%s",
                image.size, image.mode,
            )

            self._inky.set_image(image)
            self._inky.show()
            logger.info(f"Successfully refreshed Inky Impression screen (physical {phys_w}x{phys_h}).")
        else:
            logger.info(f"[Inky Mock] Flashed image {image.size} in {self.model} mode.")

    def display_image(self, image: Image.Image):
        self.update(image)

    def sleep(self):
        pass
