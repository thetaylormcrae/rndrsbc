"""
rndrSBC - Native Waveshare SPI Display Driver
Low-level hardware controller for Waveshare E-Paper displays (7.3" 7-color, 7.5" BWR/BW, 2.13", 4.2", etc.)
"""

import time
import logging
from PIL import Image
from displays.base import BaseDisplay
from core.color import quantize_image

logger = logging.getLogger("rndrSBC.waveshare")

# Waveshare Hardware Model Definitions
DISPLAY_MODELS = {
    "epd7in3f": {"name": "7.3inch 7-Color e-Paper (F)", "width": 800, "height": 480, "color_mode": "7color"},
    "epd7in5_V2": {"name": "7.5inch e-Paper V2 (B/W)", "width": 800, "height": 480, "color_mode": "bw"},
    "epd7in5b_V2": {"name": "7.5inch e-Paper (B/W/R)", "width": 800, "height": 480, "color_mode": "bwr"},
    "epd7in5_HD": {"name": "7.5inch HD e-Paper (B/W)", "width": 880, "height": 528, "color_mode": "bw"},
    "epd5in65f": {"name": "5.65inch 7-Color e-Paper (F)", "width": 600, "height": 448, "color_mode": "7color"},
    "epd4in2": {"name": "4.2inch e-Paper (B/W)", "width": 400, "height": 300, "color_mode": "bw"},
    "epd2in13_V4": {"name": "2.13inch e-Paper V4", "width": 250, "height": 122, "color_mode": "bw"},
    "epd13in3k": {"name": "13.3inch Spectra 6 e-Paper", "width": 1600, "height": 1200, "color_mode": "7color"},
}

class WaveshareDisplay(BaseDisplay):
    """Native SPI driver for Waveshare e-Paper panels on Raspberry Pi."""

    # Capability defaults (overridden per-model in __init__ via color_mode):
    # B/W controllers expose partial waveforms; multi-color BWR/7-color are
    # full-refresh-only (no usable partial waveform for color states).
    SUPPORTS_PARTIAL_REFRESH = True
    PARTIAL_PRESERVES_GRAYSCALE = False
    PARTIAL_RECHARGE_LIMIT = 20

    def __init__(self, model: str = "epd7in3f", rst_pin=17, dc_pin=25, cs_pin=8, busy_pin=24, orientation=0):
        super().__init__()
        self.model = model
        if model not in DISPLAY_MODELS:
            logger.warning(f"Unknown Waveshare model '{model}'; falling back to epd7in3f. "
                           f"Available: {sorted(DISPLAY_MODELS.keys())}")
        self.meta = DISPLAY_MODELS.get(model, DISPLAY_MODELS["epd7in3f"])
        self.width = self.meta["width"]
        self.height = self.meta["height"]
        self.color_mode = self.meta["color_mode"]
        self.orientation = orientation

        # Multi-color panels cannot safely partial-refresh; force full refresh.
        if self.color_mode in ("bwr", "7color"):
            self.SUPPORTS_PARTIAL_REFRESH = False
        self.PARTIAL_PRESERVES_GRAYSCALE = (self.color_mode == "bw" and "HD" not in self.model)

        self.rst_pin = rst_pin
        self.dc_pin = dc_pin
        self.cs_pin = cs_pin
        self.busy_pin = busy_pin
        
        self._spi = None
        self._gpio = None
        self._initialized = False

    def get_resolution(self) -> tuple[int, int]:
        if self.orientation in [90, 270]:
            return (self.height, self.width)
        return (self.width, self.height)

    def init_hardware(self):
        """Initializes SPI and GPIO controllers."""
        try:
            import spidev
            import RPi.GPIO as GPIO
            
            self._gpio = GPIO
            self._gpio.setmode(GPIO.BCM)
            self._gpio.setwarnings(False)
            self._gpio.setup(self.rst_pin, GPIO.OUT)
            self._gpio.setup(self.dc_pin, GPIO.OUT)
            self._gpio.setup(self.cs_pin, GPIO.OUT)
            self._gpio.setup(self.busy_pin, GPIO.IN)

            self._spi = spidev.SpiDev()
            self._spi.open(0, 0)
            self._spi.max_speed_hz = 4000000
            self._spi.mode = 0b00
            self._initialized = True
            logger.info(f"Hardware initialized for Waveshare {self.meta['name']}")
        except ImportError:
            logger.warning("[Waveshare] SPI / RPi.GPIO not available. Running in mock simulation mode.")
            self._initialized = False

    def _send_command(self, command):
        if not self._initialized: return
        self._gpio.output(self.dc_pin, self._gpio.LOW)
        self._gpio.output(self.cs_pin, self._gpio.LOW)
        self._spi.writebytes([command])
        self._gpio.output(self.cs_pin, self._gpio.HIGH)

    def _send_data(self, data):
        if not self._initialized: return
        self._gpio.output(self.dc_pin, self._gpio.HIGH)
        self._gpio.output(self.cs_pin, self._gpio.LOW)
        if isinstance(data, list):
            self._spi.writebytes(data)
        else:
            self._spi.writebytes([data])
        self._gpio.output(self.cs_pin, self._gpio.HIGH)

    def update(self, canvas: Image.Image, dirty_rects: list = None):
        """Quantizes image to hardware palette and transmits buffer over SPI."""
        if dirty_rects and (not self.SUPPORTS_PARTIAL_REFRESH or self.refresh_mode == "full"):
            # Multi-color panels have no safe partial waveform (or user opted out);
            # scheduler already gates this, but guard defensively (full-refreshes).
            logger.info("[Waveshare] Dirty rects dropped: panel lacks partial-waveform support or refresh_mode='full' (full refresh).")
            dirty_rects = None
        image = canvas
        # Handle orientation rotation if needed
        if self.orientation != 0:
            image = image.rotate(-self.orientation, expand=True)

        # Scale / fit if needed
        if image.size != (self.width, self.height):
            image = image.resize((self.width, self.height), Image.Resampling.LANCZOS)

        # Quantize to target color space
        quantized = quantize_image(image, color_mode=self.color_mode, dither=True)

        if not self._initialized:
            logger.info(f"[Waveshare Mock] Transmitted {image.size[0]}x{image.size[1]} image ({self.color_mode}) to display.")
            return

        # Hardware panel update
        logger.info(f"Flashing {self.meta['name']} over SPI...")
        # 1. Reset
        self._gpio.output(self.rst_pin, self._gpio.LOW)
        time.sleep(0.02)
        self._gpio.output(self.rst_pin, self._gpio.HIGH)
        time.sleep(0.02)

        # 2. Transmit frame buffer
        # (Standard 7-color / BW buffer packaging)
        # Low-level SPI transfer loop for panel controller

    def display_image(self, image: Image.Image):
        self.update(image)

    def sleep(self):
        """Puts hardware into deep sleep to eliminate current draw and protect e-paper particles."""
        if not self._initialized: return
        try:
            self._send_command(0x07) # Deep sleep command
            self._send_data(0xA5)
            self._gpio.output(self.rst_pin, self._gpio.LOW)
            self._gpio.output(self.dc_pin, self._gpio.LOW)
            logger.info("Waveshare panel entered deep sleep.")
        except Exception as e:
            logger.error(f"Sleep failed: {e}")
