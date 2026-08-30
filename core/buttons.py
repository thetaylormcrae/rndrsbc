"""
rndrSBC - Physical Button Controller
Wires up to 3 GPIO push-buttons to cycles through playlist actions.

Buttons (BCM pin numbering by default, configurable in config):
  BUTTON_NEXT  -> advance to next widget in active playlist
  BUTTON_PREV  -> go back to previous widget
  BUTTON_TOGGLE-> toggle between quiet-hours suspend and active refresh

The controller degrades gracefully: on platforms without RPi.GPIO
(a laptop, CI, the VirtualDisplay), it logs and no-ops so the rest
of the system runs normally.
"""

import logging
import threading
import time

logger = logging.getLogger("rndrSBC.buttons")

try:
    import RPi.GPIO as GPIO
    _HAS_GPIO = True
except Exception:
    GPIO = None
    _HAS_GPIO = False


class ButtonController:
    """Debounced physical button listener mapped to scheduler actions."""

    # NOTE: these must NOT overlap the e-paper driver's own lines. The Pimoroni
    # Inky HAT / recent Inky boards drive BUSY=17, RESET=27 and DC=22, and the
    # SPI bus uses 8/9/10/11 — so the old defaults (17/27/22) collided with the
    # display every boot ("channel already in use") and left some buttons dead.
    # 5/6/12 are free general-purpose header pins shared with neither the
    # display, the SPI bus, nor I2C (0/1/2/3). Override via ``buttons: pins``.
    DEFAULT_PINS = {
        "next": 5,
        "prev": 6,
        "toggle": 12,
    }

    def __init__(self, scheduler, config: dict, pins: dict = None):
        self.scheduler = scheduler
        self.config = config
        self.pins = dict(self.DEFAULT_PINS)
        if pins:
            self.pins.update({k: v for k, v in pins.items() if v is not None})
        self._thread = None
        self._running = False
        self._last_state = {}
        self._last_trigger = {}
        self._debounce_ms = int(config.get("buttons", {}).get("debounce_ms", 200))
        self._enabled = bool(config.get("buttons", {}).get("enabled", True))

    def _on_next(self):
        items = self.scheduler.active_playlist_items
        if items:
            nxt = (self.scheduler.current_index + 1) % len(items)
            self.scheduler.trigger_render_now(nxt, force_hardware=True)
            logger.info("[BUTTON] Next widget -> index %d", nxt)

    def _on_prev(self):
        items = self.scheduler.active_playlist_items
        if items:
            prv = (self.scheduler.current_index - 1) % len(items)
            self.scheduler.trigger_render_now(prv, force_hardware=True)
            logger.info("[BUTTON] Previous widget -> index %d", prv)

    def _on_toggle(self):
        current = bool(self.config.get("quiet_hours", {}).get("enabled", False))
        self.config.setdefault("quiet_hours", {})["enabled"] = not current
        state = "enabled" if not current else "disabled"
        logger.info("[BUTTON] Quiet-hours %s", state)
        if not current:
            self.scheduler.trigger_render_now(self.scheduler.current_index, force_hardware=True)

    def _dispatch(self, action):
        try:
            getattr(self, f"_on_{action}")()
        except Exception as e:
            logger.error(f"Button action '{action}' failed: {e}")

    def _poll(self):
        """Non-blocking polling loop with software debounce (works on any GPIO lib)."""
        while self._running:
            for action, pin in self.pins.items():
                try:
                    state = GPIO.input(pin)
                except Exception:
                    continue
                now = time.time()
                prev = self._last_state.get(action)
                if prev == 1 and state == 0:  # falling edge (pressed to GND)
                    if now - self._last_trigger.get(action, 0) >= self._debounce_ms / 1000.0:
                        self._last_trigger[action] = now
                        self._dispatch(action)
                self._last_state[action] = state
            time.sleep(0.02)

    def start(self):
        if not self._enabled:
            logger.info("Buttons disabled in config.")
            return
        if not _HAS_GPIO:
            logger.info("RPi.GPIO not available - physical buttons skipped (virtual/simulator mode).")
            return
        try:
            GPIO.setmode(GPIO.BCM)
        except Exception as e:
            logger.error(f"Failed to set GPIO mode: {e}")
            return

        # Track skipped pins so _poll never reads a pin we don't own. A pin that
        # an earlier subsystem (the e-paper driver) has already configured as an
        # OUTPUT is not ours: claiming it would trip RPi.GPIO's noisy
        # "channel already in use" warning and alias the display's line.
        skipped = {}
        for action, pin in self.pins.items():
            try:
                # Probe current state without disturbing a foreign setup.
                inp = GPIO.input(pin)
            except Exception:
                inp = None
            try:
                GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            except Exception as e:
                skipped[action] = (pin, str(e))
                logger.warning(
                    "Button '%s' on GPIO %d could not be claimed by RPi.GPIO "
                    "(likely owned by the display driver): skipping. %s",
                    action, pin, e,
                )
                continue
            self._last_state[action] = GPIO.input(pin)
            logger.info("Configuring %s button on GPIO %d", action, pin)

        if skipped:
            logger.warning(
                "Physical buttons active on pins %s; unsupported pins: %s",
                list(self.pins.keys()), skipped,
            )

        # Drop skipped pins so _poll only ever touches pins we own.
        self.pins = {a: p for a, p in self.pins.items() if a not in skipped}

        if not self.pins or not self._last_state:
            logger.warning("No usable button pins - physical buttons disabled.")
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll, daemon=True, name="rndrSBC-Buttons")
        self._thread.start()
        logger.info("Physical button controller started.")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
        if _HAS_GPIO and self._enabled:
            try:
                GPIO.cleanup()
            except Exception:
                pass
        logger.info("Button controller stopped.")
