"""
rndrSBC - Health Telemetry, Hardware Watchdog & Notification Alerts
Tracks render durations, memory allocations, network health, and failure counters.
Supports webhook push alerts (Discord, Slack, Home Assistant, generic HTTP POST).
"""

import json
import logging
import os
import time
import urllib.request
from typing import Dict, Any, List

logger = logging.getLogger("rndrSBC.telemetry")


class SystemTelemetry:
    """Singleton engine for operational metrics, health checks, and alerts."""

    def __init__(self):
        self.start_time = time.time()
        self.render_count = 0
        self.error_count = 0
        self.last_render_duration_ms = 0.0
        self.last_render_ts = 0
        self.last_error_message = None
        self.last_error_ts = 0
        self.consecutive_failures = 0
        self.alert_webhook_url = None
        self.recent_events: List[Dict[str, Any]] = []
        self._max_events = 50

    def record_render_success(self, duration_ms: float, widget_name: str):
        self.render_count += 1
        self.last_render_duration_ms = duration_ms
        self.last_render_ts = time.time()
        self.consecutive_failures = 0
        self._add_event("render_success", f"Rendered '{widget_name}' in {duration_ms:.1f}ms")

    def record_render_error(self, widget_name: str, error_str: str):
        self.error_count += 1
        self.last_error_message = f"{widget_name}: {error_str}"
        self.last_error_ts = time.time()
        self.consecutive_failures += 1
        self._add_event("render_error", self.last_error_message)

        if self.consecutive_failures in (3, 5, 10):
            self.send_alert(f"⚠️ [rndrSBC Alert] {self.consecutive_failures} consecutive render failures! Last error: {self.last_error_message}")

    def _add_event(self, event_type: str, message: str):
        entry = {
            "timestamp": time.time(),
            "time_iso": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "type": event_type,
            "message": message
        }
        self.recent_events.insert(0, entry)
        if len(self.recent_events) > self._max_events:
            self.recent_events.pop()

    def send_alert(self, message: str) -> bool:
        """Dispatches an outbound JSON alert to the configured webhook."""
        if not self.alert_webhook_url:
            return False
        try:
            payload = json.dumps({"text": message, "source": "rndrSBC", "timestamp": time.time()}).encode("utf-8")
            req = urllib.request.Request(
                self.alert_webhook_url,
                data=payload,
                headers={"Content-Type": "application/json", "User-Agent": "rndrSBC-Watchdog/1.0"}
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                logger.info(f"Webhook alert dispatched (status {resp.status})")
                return True
        except Exception as e:
            logger.warning(f"Failed to dispatch alert webhook: {e}")
            return False

    def get_status(self) -> Dict[str, Any]:
        """Returns comprehensive operational metrics for monitoring & API."""
        uptime_sec = int(time.time() - self.start_time)
        return {
            "uptime_seconds": uptime_sec,
            "uptime_human": f"{uptime_sec // 3600}h {(uptime_sec % 3600) // 60}m {uptime_sec % 60}s",
            "render_count": self.render_count,
            "error_count": self.error_count,
            "consecutive_failures": self.consecutive_failures,
            "last_render_duration_ms": round(self.last_render_duration_ms, 2),
            "last_render_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.last_render_ts)) if self.last_render_ts else None,
            "last_error": self.last_error_message,
            "last_error_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.last_error_ts)) if self.last_error_ts else None,
            "health": "healthy" if self.consecutive_failures == 0 else ("degraded" if self.consecutive_failures < 3 else "critical"),
            "recent_events": self.recent_events[:15]
        }


TELEMETRY = SystemTelemetry()
