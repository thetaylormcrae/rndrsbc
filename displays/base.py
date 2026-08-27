"""
rndrSBC - Abstract Display Interface
Hardware-Adaptive Display Driver API
"""

from abc import ABC, abstractmethod
import logging
from PIL import Image

class BaseDisplay(ABC):
    """Abstract base class for physical and virtual display hardware drivers."""

    # Hardware capability flags — subclasses MUST declare these accurately.
    # Failing to do so can cause visible defects (ghosting, color bleed).
    SUPPORTS_PARTIAL_REFRESH: bool = False
    # If partial is supported, does it preserve grayscale multi-level states?
    PARTIAL_PRESERVES_GRAYSCALE: bool = False
    # Max partial refreshes before a full refresh is required to clear charge.
    PARTIAL_RECHARGE_LIMIT: int = 20

    def __init__(self):
        # Runtime refresh mode: 'auto' (partial when supported) or 'full' (opt-out).
        self.refresh_mode = "auto"

    @abstractmethod
    def get_resolution(self) -> tuple[int, int]:
        """Returns (width, height) tuple in physical pixels."""
        pass

    @abstractmethod
    def init_hardware(self):
        """Initializes SPI, GPIO, I2C, or framebuffer interface."""
        pass

    @abstractmethod
    def update(self, canvas: Image.Image, dirty_rects: list = None):
        """
        Updates the physical display with the given image.
        Optional `dirty_rects` allows partial buffer updating on supported hardware.
        """
        pass

    def display_image(self, image: Image.Image):
        """Backwards compatibility alias for update()."""
        self.update(image)

    @abstractmethod
    def sleep(self):
        """Puts controller into low-power sleep to prevent panel burn-in and save power."""
        pass

    def supports_partial(self) -> bool:
        """True if this panel can safely accept dirty_rect partial updates."""
        return self.SUPPORTS_PARTIAL_REFRESH and self.refresh_mode == "auto"

    def set_refresh_mode(self, mode: str):
        """Runtime panel refresh mode: 'auto' (partial when safe) or 'full' (opt-out)."""
        if mode not in ("auto", "full"):
            raise ValueError(f"Invalid refresh_mode '{mode}' — use 'auto' or 'full'")
        self.refresh_mode = mode

    def set_resolution(self, width: int, height: int):
        """Hot-resize the driver at runtime (used when the panel model / screen
        size is changed via the dashboard without a restart). Subclasses that
        hold explicit width/height should override this."""
        logging.getLogger(self.__class__.__name__).warning(
            f"Driver does not support runtime resize; ignoring "
            f"set_resolution({width}x{height}). A restart is required to apply "
            f"the new screen size."
        )

    def needs_full_refresh(self, consecutive_partials: int = 0) -> bool:
        """
        True when a full refresh must be forced:
        - panel doesn't support partial refresh, or
        - the panel has exceeded its recharge limit (charge accumulation).
        """
        if not self.SUPPORTS_PARTIAL_REFRESH:
            return True
        return consecutive_partials >= self.PARTIAL_RECHARGE_LIMIT

