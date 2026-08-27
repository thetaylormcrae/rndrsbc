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

    DEFAULT_PINS = {
        "next": 17,
        "prev": 27,
        "toggle": 22,
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
            for action, pin in self.pins.items():
                logger.info("Configuring %s button on GPIO %d", action, pin)
                GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
                self._last_state[action] = GPIO.input(pin)
        except Exception as e:
            logger.error(f"Failed to configure GPIO buttons: {e}")
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
