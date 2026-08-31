"""
rndrSBC - Multi-Playlist Background Scheduler
Rotates through widgets in the active playlist, enforces quiet hours, and updates display hardware.
"""

import time
import threading
import logging
from datetime import datetime
import zoneinfo
import gc
from PIL import Image

from core.pipeline import RENDER_CACHE, compute_dirty_rects, quantize_and_dither
from core.telemetry import TELEMETRY
from core.transitions import apply_transition
from core.config_schema import validate_config, ConfigError

logger = logging.getLogger("rndrSBC.scheduler")

class Scheduler:
    def __init__(self, display, config_data: dict, widget_registry: dict):
        # Fail-fast with self-healing: never boot with a config that is provably invalid.
        # Missing required keys are safely self-healed so an unattended appliance
        # never crash-loops. Unknowns/repairs are logged as warnings and boot proceeds.
        try:
            _, self.config_warnings = validate_config(config_data, self_heal=True)
        except ConfigError as e:
            logger.critical("config rejected at boot: %s", e)
            raise
        for w_ in self.config_warnings:
            logger.warning("config: %s", w_)
        self.display = display
        self.config = config_data
        self.widgets = widget_registry
        
        self.current_index = 0
        self.is_running = False
        self._thread = None
        self.last_rendered_image = None
        self.last_rendered_widget = None
        self.last_preview_image = None
        self.last_render_timestamp = 0
        self._in_quiet_mode = False
        self._consecutive_partials = 0
        self._apply_panel_health_override(config_data)

    def _apply_panel_health_override(self, config_data: dict):
        """Seed the panel-health governor with a used panel's prior wear.

        Reads the optional ``display.panel_health`` block:

        .. code-block:: jsonc

            "display": {
              "refresh_mode": "auto",
              "panel_health": {
                "known_wear_units": 400000,   // exact prior life consumed
                "panel_age_years": 3.0,        // …or estimate from age
                "full_refresh_interval_min": 15 // duty cycle: full every 15 min
              }
            }

        This lets an operator register a refurbished / second-hand panel so
        the cadence governor starts from the *true* remaining budget rather
        than assuming a brand-new panel.  Idempotent: only applies when at
        least one key is present, and safe to re-apply on config hot-reload.
        """
        try:
            d = config_data.get("display", {})
            ph = d.get("panel_health", {}) if isinstance(d, dict) else {}
        except AttributeError:
            ph = {}
        if not isinstance(ph, dict):
            return
        known = ph.get("known_wear_units")
        age = ph.get("panel_age_years")
        interval = ph.get("full_refresh_interval_min")
        if known is None and age is None and interval is None:
            return
        from core.panel_health import get_health
        get_health().apply_override(
            known_wear_units=known, panel_age_years=age,
            full_refresh_interval_min=interval)

    @property
    def active_playlist_items(self) -> list:
        """Resolves the current list of widget items from the active playlist."""
        playlists = self.config.get("playlists", {})
        active_key = self.config.get("active_playlist", "main")
        
        if active_key in playlists and "items" in playlists[active_key]:
            return playlists[active_key]["items"]
        
        # Legacy fallback if config has top-level 'playlist' list
        if "playlist" in self.config and isinstance(self.config["playlist"], list):
            return self.config["playlist"]

        # Default fallback
        return [{"widget": "weather", "duration_minutes": 15, "settings": {}}]

    def is_quiet_hours(self) -> bool:
        """Checks if current time falls within configured quiet hours window."""
        qh = self.config.get("quiet_hours", {})
        if not qh.get("enabled", False):
            return False

        start_str = qh.get("start", "23:00")
        end_str = qh.get("end", "06:00")
        
        # Timezone resolution
        tz_name = self.config.get("device", {}).get("timezone", "UTC")
        try:
            now = datetime.now(zoneinfo.ZoneInfo(tz_name))
        except Exception:
            now = datetime.now()

        try:
            sh, sm = map(int, start_str.split(":"))
            eh, em = map(int, end_str.split(":"))
            cur_min = now.hour * 60 + now.minute
            start_min = sh * 60 + sm
            end_min = eh * 60 + em

            if start_min <= end_min:
                return start_min <= cur_min < end_min
            else: # overnight window (e.g., 23:00 to 06:00)
                return cur_min >= start_min or cur_min < end_min
        except Exception as e:
            logger.warning(f"Failed to parse quiet hours schedule: {e}")
            return False

    def update_config(self, new_config: dict):
        """Live updates configuration and resets rotation if active playlist changed."""
        self.config = new_config
        self.current_index = 0
        self._apply_panel_health_override(new_config)
        # Hot-apply a display resolution / panel change so the live preview
        # (and widget layout) reflect a new screen size without a restart.
        self._apply_display_resolution(new_config)

    def _apply_display_resolution(self, new_config: dict):
        """If the target width/height changed, resize the running display and
        invalidate the render cache so the next frame uses the new size."""
        d = new_config.get("display", {}) if isinstance(new_config.get("display"), dict) else {}
        width = d.get("width")
        height = d.get("height")
        if not width or not height:
            return
        try:
            width, height = int(width), int(height)
        except (TypeError, ValueError):
            return
        cur = self.display.get_resolution()
        if cur == (width, height):
            return  # no change; avoid a wasteful re-render
        if hasattr(self.display, "set_resolution"):
            self.display.set_resolution(width, height)
        # Invalidate every cached render so widgets redraw at the new size.
        try:
            from core.pipeline import RENDER_CACHE
            RENDER_CACHE.clear()
        except Exception:
            pass
        logger.info(
            f"Screen size changed from {cur} -> ({width}x{height}); "
            f"cleared render cache and will re-render at new resolution."
        )
        # Force an immediate re-render so the live preview reflects the new size
        # without waiting for the next rotation tick or quiet hours.
        try:
            self.trigger_render_now(index=self.current_index or 0, force_hardware=True)
        except Exception as e:
            logger.warning(f"Could not force re-render after resize: {e}")

    def start(self):
        if self.is_running:
            return
        self.is_running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="rndrSBC-Scheduler")
        self._thread.start()
        logger.info("Multi-playlist scheduler started.")

    def stop(self):
        self.is_running = False
        if self._thread:
            self._thread.join(timeout=2)
        logger.info("Scheduler stopped.")

    def render_preview(self, index: int = 0) -> Image.Image:
        """Render the current playlist item to a full-RGB preview WITHOUT
        touching the panel. This is the ``snapshot``/QA path: it reproduces
        exactly what the daemon would draw (same widget, settings, resolution)
        and returns the pre-dither colour canvas, saving nothing to hardware.
        Returns None when no item is available.
        """
        items = self.active_playlist_items
        if not items:
            return None
        item = items[index % len(items)]
        widget_id = item.get("widget")
        settings = item.get("settings", {})
        widget = self.widgets.get(widget_id)
        if not widget:
            return None
        widget.config = self.config
        try:
            dims = self.display.get_resolution()
            image = widget.safe_render(dims, settings)
            self.last_preview_image = image.copy()
            return self.last_preview_image
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"render_preview failed for {widget_id}: {type(exc).__name__}: {exc}")
            return None

    def trigger_render_now(self, index: int = None, force_hardware: bool = False):
        """Forces an immediate render through the cache + dirty-rect + transition pipeline."""
        items = self.active_playlist_items
        if not items:
            return

        if index is not None:
            self.current_index = index % len(items)

        item = items[self.current_index % len(items)]
        widget_id = item.get("widget")
        settings = item.get("settings", {})

        # Dedupe: if we already rendered the SAME widget moments ago and this
        # isn't an explicit forced refresh, skip it. Prevents startup bursts
        # (main's initial render + _run_loop's first tick) and button/API
        # double-taps from stacking renders on a slow e-Paper panel.
        if (not force_hardware and self.last_rendered_widget == widget_id
                and self.last_render_timestamp is not None
                and time.time() - self.last_render_timestamp < 10):
            logger.debug("Skipping redundant render of %s (%.1fs ago)",
                         widget_id, time.time() - self.last_render_timestamp)
            return

        widget = self.widgets.get(widget_id)
        if not widget:
            logger.error(f"Widget '{widget_id}' not found in registry.")
            return
        # Stamp global config on the widget so it can read language/units/etc.
        widget.config = self.config

        in_quiet = self.is_quiet_hours()
        if in_quiet and not force_hardware:
            if not self._in_quiet_mode:
                logger.info("Quiet hours active: suspending e-Paper refreshes.")
                self.display.sleep()
                self._in_quiet_mode = True
            return

        self._in_quiet_mode = False
        dims = self.display.get_resolution()

        # For Inky displays, preserve full RGB color canvas so Inky library's
        # native multi-color palette engine handles 7-color / 6-color dithering (like InkyPi).
        disp_color_mode = self.config.get("display", {}).get("color_mode")
        if not disp_color_mode:
            from displays.inky import InkyDisplay
            if isinstance(self.display, InkyDisplay):
                color_mode = "rgb"
            else:
                color_mode = getattr(self.display, "color_mode", "mono")
        else:
            color_mode = disp_color_mode.lower()

        dither_enabled = bool(self.config.get("display", {}).get("dither", True))
        transition_type = self.config.get("transition", "cut")
        t0 = time.time()

        # Refresh mode: 'auto' (default) = partial refresh when the panel
        # supports it; 'full' = explicit user opt-out, always full refresh.
        refresh_mode = str(self.config.get("refresh_mode", "auto")).lower()
        panel_partial = getattr(self.display, "supports_partial", lambda: False)()
        partial_allowed = (refresh_mode == "auto") and panel_partial
        # Adaptive recharge limit comes from the panel-health governor so an
        # ageing panel gets a tighter partial streak before a charge wash.
        from core.panel_health import get_health
        health = get_health()
        recharge_limit = health.partial_budget(
            getattr(self.display, "PARTIAL_RECHARGE_LIMIT", 20)
        )
        if not partial_allowed or self._consecutive_partials >= recharge_limit:
            # Full refresh required: reset the partial counter,
            # and force the full frame to the panel (no dirty-rect masking).
            self._consecutive_partials = 0
            force_full = True
        else:
            force_full = False

        try:
            # Stage 1: Layout & Draw (cache keyed by widget+settings+resolution)
            cached = RENDER_CACHE.get(widget_id, settings, dims)
            if cached is not None:
                image = cached
            else:
                image = widget.safe_render(dims, settings)
                RENDER_CACHE.put(widget_id, settings, dims, image, ttl=60)

            # Hold a COLOR copy (pre-dither) as the live web-preview frame.
            # The hardware "last_rendered_image" holds the final 1-bit panel
            # frame, so the preview would otherwise be black & white.
            self.last_preview_image = image.copy()

            # Stage 2: Dirty-rect detection vs previous frame
            dirty_rects = compute_dirty_rects(self.last_rendered_image, image)

            # Stage 3: Quantize + dither for the target panel palette
            dithered = quantize_and_dither(image, color_mode=color_mode, dither=dither_enabled)
            if image is not dithered:
                image.close()
            del image

            # Stage 4: Optional transition effect on state change
            if transition_type != "cut" and self.last_rendered_image is not None:
                dithered = apply_transition(self.last_rendered_image, dithered, transition_type, progress=1.0)

            # Stage 5: Hardware push — partial refresh automatically when:
            #   refresh_mode='auto' AND panel declares partial-waveform support.
            # Explicit 'full' opt-out or recharge-limit forces full refresh.
            if force_hardware or force_full or not partial_allowed:
                # Full refresh: partial counter stays at 0 (already reset).
                self._consecutive_partials = 0
                health.record("full")
                try:
                    self.display.update(dithered)
                except TypeError:
                    self.display.update(dithered)
                self.last_rendered_image = dithered
                self.last_render_timestamp = time.time()
            elif dirty_rects:
                # Partial-capable panel: push only changed regions.
                self._consecutive_partials += 1
                health.record("partial")
                try:
                    self.display.update(dithered, dirty_rects=dirty_rects)
                except TypeError:
                    self.display.update(dithered)
                self.last_rendered_image = dithered
                self.last_render_timestamp = time.time()
            else:
                logger.debug("No visual changes detected; skipping display update")

            # Cleanup previous frame buffer
            if self.last_rendered_image is not None and self.last_rendered_image is not dithered:
                try:
                    self.last_rendered_image.close()
                except Exception:
                    pass

            self.last_rendered_image = dithered
            self.last_rendered_widget = widget_id
            self.last_render_timestamp = time.time()
            elapsed_ms = (time.time() - t0) * 1000
            TELEMETRY.record_render_success(elapsed_ms, widget.name)
            logger.info(f"Rendered {widget.name} in {elapsed_ms:.1f}ms")
        except Exception as e:
            TELEMETRY.record_render_error(widget_id, str(e))
            logger.error(f"Failed to render widget '{widget_id}': {e}", exc_info=True)
        finally:
            # Explicit garbage collection pass on low-RAM targets
            gc.collect()

    def _run_loop(self):
        # Stagger the first tick: main.py already fires an explicit initial
        # render at boot, so wait a short beat before the loop's own first
        # render to avoid a double-refresh burst on the panel.
        time.sleep(3)
        while self.is_running:
            items = self.active_playlist_items
            if not items:
                time.sleep(5)
                continue

            current_item = items[self.current_index % len(items)]
            duration_secs = max(10, current_item.get("duration_minutes", 15) * 60)

            self.trigger_render_now(self.current_index)

            # Sleep until next rotation
            sleep_start = time.time()
            while time.time() - sleep_start < duration_secs and self.is_running:
                time.sleep(1)

            # Advance to next widget in active playlist
            self.current_index = (self.current_index + 1) % len(items)

