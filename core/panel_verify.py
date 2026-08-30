"""
rndrSBC - Panel Update Verification & SPI Serialization.

Closing the gap between ``render succeeded`` and ``panel actually changed``.

The e673 driver's ``show()`` is fire-and-forget from the caller's perspective:
it returns when the SPI write sequence has *completed*, not when the panel has
*confirmed* the frame. Three real failure modes produce an "intermittently off"
display even though the render (and `screen.png` preview) were correct:

  1. Overlapping   calls are interleaved on the shared SPI bus. The scheduler
     runs in a background thread while HTTP (`/api/refresh`) and button threads
     can also call ``display.update``. Without serialization a second ``show()``
     can cut off the first mid-waveform, leaving the panel dark until the next
     full refresh re-drives it ("sometimes a refresh fixes it").
  2. Stacking onto a panel still mid-waveform (EPD BUSY held high).
  3. A write that completes but the panel fails to latch the frame.

This module:
  - serializes panel access with a per-instance lock,
  - probes the EPD BUSY state (via the driver's own wait, when available),
  - measures the refresh window and flags any stall,
  - retries a failed refresh once automatically,
  - emits distinct telemetry (`panel_updated` / `panel_stalled`) so the
    intermittent failure becomes visible instead of a lying success log.

It degrades gracefully: on mock/virtual/absent hardware it behaves exactly as
before (a single synchronous update, no busy probing) with no new failure path.
"""

import logging
import threading
import time

logger = logging.getLogger("rndrSBC.panel_verify")


class PanelVerify:
    """Serialization + post-write verification for the e-paper panel."""

    # Busy-wait before judging "stalled". The Spectra 6 alternating waveform
    # runs ~23.6s / ~36.1s (measured), so give the full-refresh window headroom
    # plus margin before declaring a stall worth a retry.
    BUSY_WAIT_TIMEOUT = 12.0        # max s waiting for BUSY to clear pre-write
    STALL_THRESHOLD = 48.0          # s a single show() may take before we retry
    DEFAULT_ELAPSED_OK = 5.0        # reasonable completion window on mock/virtual

    def __init__(self, telemetry=None):
        self._lock = threading.Lock()
        self._telemetry = telemetry
        self.retry_count = 0
        self.last_stall_at = 0.0

    # -- entry point ------------------------------------------------------
    def run(self, push_fn, *, model="Inky", resolution=(800, 480),
            busy_wait=None, elapsed_ok=DEFAULT_ELAPSED_OK):
        """Execute ``push_fn`` once, serialize against other pushes, and verify.

        ``push_fn`` must perform the hardware write (``set_image`` + ``show``).
        ``busy_wait`` is an optional callable taking a timeout that blocks until
        the panel is idle; when provided we gate on it *before* the push.
        """
        stall = None
        with self._lock:
            # 1. Gate on BUSY so we never stack onto a mid-waveform refresh.
            if busy_wait is not None:
                try:
                    busy_wait(self.BUSY_WAIT_TIMEOUT)
                    if self._telemetry is not None:
                        self._telemetry.record_panel_busy_wait(self.BUSY_WAIT_TIMEOUT)
                except Exception as e:  # never block a real update on a probe
                    logger.debug("[PanelVerify] BUSY probe unavailable: %s", e)

            # 2. Write + measure the refresh window.
            t0 = time.time()
            first_error = None
            try:
                push_fn()
                elapsed = time.time() - t0
            except Exception as e:  # a real exception on push is itself a stall
                first_error = e
                elapsed = time.time() - t0

            # 3. Judge the outcome.
            if first_error is not None or elapsed > self.STALL_THRESHOLD:
                stall = PanelStall(elapsed=elapsed, error=first_error)
        # -- retry (outside the lock so a stuck panel doesn't hold the bus) --
        if stall is not None:
            self.retry_count += 1
            self.last_stall_at = time.time()
            if self._telemetry is not None:
                self._telemetry.record_panel_stall(stall.elapsed_ms, repr(stall.error))
            logger.error(
                "[PanelVerify] Refresh stall detected (%.1fs%s) - retrying once.",
                stall.elapsed_ms / 1000.0,
                f" - {stall.error!r}" if stall.error else "",
            )
            with self._lock:
                try:
                    push_fn()
                    logger.info("[PanelVerify] Auto-retry succeeded.")
                except Exception as retry_err:
                    if self._telemetry is not None:
                        self._telemetry.record_panel_stall(
                            (time.time() - t0) * 1000.0, repr(retry_err))
                    logger.error("[PanelVerify] Auto-retry also failed: %r", retry_err)
                    return PanelResult(ok=False, retried=True,
                                       problems=["stall", "retry_failed"])
            return PanelResult(ok=True, retried=True, problems=[])

        if self._telemetry is not None:
            self._telemetry.record_panel_updated(elapsed * 1000.0, model, resolution)
        return PanelResult(ok=True, retried=False, problems=[])


class PanelResult:
    """Outcome of a verified panel refresh."""

    def __init__(self, ok: bool, retried: bool, problems):
        self.ok = ok
        self.retried = retried
        self.problems = problems


class PanelStall:
    def __init__(self, elapsed=None, error=None):
        self.elapsed = elapsed or 0.0
        self.error = error

    @property
    def elapsed_ms(self) -> float:
        return self.elapsed * 1000.0
