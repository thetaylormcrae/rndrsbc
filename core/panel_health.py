#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rndrSBC - Panel Refresh-Budget Governor & Wear Estimator

E-Paper panels wear out: every full ``flash`` refresh and (to a lesser but
non-trivial degree) every ``partial`` refresh draws current through the
electrophoretic ink and degrades contrast over the panel's lifetime.  The
**refresh budget** is the operator-facing accounting of that wear over the
board's *entire life* (persisted across reboots, unlike the per-run
``consecutive_partials`` recharge guard already in the scheduler).

This module:

  * persists cumulative full/partial refresh counts to disk (atomic write),
  * converts those counts into an estimated ``panel_health`` percentage,
  * recommends an adaptive ``refresh_cadence`` ("standard" / "conservative" /
    "preserve") as the budget is consumed, and
  * hands the scheduler a live ``PartialBudget`` snapshot so it can tighten
    the recharge limit as the panel ages.

The wear model is deliberately conservative and transparently documented in
``WEAR_MODEL``: a full refresh is weighted 1.0 ``wear-unit`` and the elected
demotion of partial refreshes is at a public ratio (default 8 partials = 1
full) so operators can tune for their specific panel/driver combination.

Thread-safety: the governor uses a lock around every read-modify-write, so it
is safe to call from the scheduler thread and a telemetry/API thread at once.
"""

import os
import json
import time
import threading

from core import paths

logger = None


def _log():
    global logger
    if logger is None:
        import logging
        logger = logging.getLogger("rndrSBC.panel_health")
    return logger


# --- Public, documentation-bearing wear model constants --------------------
ESTIMATED_FULL_CYCLES = 500_000

PARTIAL_TO_FULL_RATIO = 8  # 8 partials ~= 1 full wear unit

# Minutes between FULL refreshes on a continuous, round-the-clock board.
# This is exactly the operator-facing thing they know about a used panel.
# Prior wear from AGE is computed from it, so a 15-min board gets ~35k
# full-cycles/yr rather than some arbitrary denser figure.
DEFAULT_FULL_REFRESH_INTERVAL_MIN = 4  # extremely conservative fallback
MINUTES_PER_YEAR = 365 * 24 * 60


def annual_full_cycles(full_refresh_interval_min: float) -> float:
    """Full-refresh-equivalent cycles a panel experiences per year.

    ``interval`` is the minutes between full refreshes (the heavy wear driver
    on e-paper).  A panel stroking full every 15 min does ~35k cycles/yr;
    every 4 min ~131k.  Safe-guarded against division by zero / negatives.
    """
    if not full_refresh_interval_min or full_refresh_interval_min <= 0:
        return MINUTES_PER_YEAR / max(1.0, float(DEFAULT_FULL_REFRESH_INTERVAL_MIN))
    return MINUTES_PER_YEAR / float(full_refresh_interval_min)

CADENCE_STANDARD_MAX = 0.50      # >50% remaining -> standard cadence
CADENCE_CONSERVATIVE_MAX = 0.15  # >15% remaining -> conservative cadence

DEFAULT_STATE_FILE = ".panel_health.json"


class PanelHealth:
    """Persistent cumulative refresh accounting + wear estimation."""

    def __init__(self, state_file: str = None):
        self._path = state_file or paths.resolve(DEFAULT_STATE_FILE)
        self._lock = threading.Lock()
        self._full = 0
        self._partial = 0
        self._recognized_wear_units = 0.0  # wear known to have happened BEFORE this install
        self._recognized_age_years = 0.0   # panel age used to *estimate* prior wear
        self._full_refresh_interval_min = DEFAULT_FULL_REFRESH_INTERVAL_MIN
        self._since = None
        self._load()

    # --- persistence ------------------------------------------------------
    def _load(self):
        try:
            with open(self._path, encoding="utf-8") as fh:
                data = json.load(fh)
            self._full = max(0, int(data.get("full_refreshes", 0)))
            self._partial = max(0, int(data.get("partial_refreshes", 0)))
            self._recognized_wear_units = float(data.get("recognized_wear_units", 0.0) or 0.0)
            self._recognized_age_years = float(data.get("recognized_age_years", 0.0) or 0.0)
            self._full_refresh_interval_min = float(
                data.get("full_refresh_interval_min", DEFAULT_FULL_REFRESH_INTERVAL_MIN) or
                DEFAULT_FULL_REFRESH_INTERVAL_MIN)
            self._since = data.get("since")
        except FileNotFoundError:
            self._since = self._since or int(time.time())
        except (ValueError, OSError, TypeError):
            _log().warning("panel_health state unreadable; starting fresh")
            self._since = self._since or int(time.time())

    def _save(self):
        data = {
            "full_refreshes": int(self._full),
            "partial_refreshes": int(self._partial),
            "recognized_wear_units": round(self._recognized_wear_units, 3),
            "recognized_age_years": round(self._recognized_age_years, 3),
            "full_refresh_interval_min": round(self._full_refresh_interval_min, 3),
            "since": int(self._since or time.time()),
        }
        tmp = self._path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            os.replace(tmp, self._path)  # atomic
            try:
                os.chmod(self._path, 0o600)
            except OSError:
                pass
        except OSError as exc:
            _log().warning("panel_health save failed: %s", exc)
    
    # --- accounting -------------------------------------------------------
    def record(self, refresh_type: str, count: int = 1):
        """Record one or more ("full" or "partial") refresh events."""
        with self._lock:
            n = max(0, int(count))
            if refresh_type in ("full", "flash"):
                self._full += n
            elif refresh_type in ("partial", "fast"):
                self._partial += n
            else:
                return
            self._save()

    # --- estimation -------------------------------------------------------
    def observed_wear_units(self) -> float:
        """Wear accumulated since this install (full + partial/RATIO)."""
        return self._full + (self._partial / PARTIAL_TO_FULL_RATIO)

    def prior_wear_units(self) -> float:
        """Wear that happened before this install (known or age-estimated).

        Precedence: an explicit ``recognized_wear_units`` override wins;
        otherwise an age-only override is converted using the operator's
        ``full_refresh_interval_min`` (the panel's actual full-refresh
        cadence) via :func:`annual_full_cycles`.
        """
        if self._recognized_wear_units > 0:
            return self._recognized_wear_units
        return self._recognized_age_years * annual_full_cycles(
            self._full_refresh_interval_min)

    def wear_units(self) -> float:
        return self.observed_wear_units() + self.prior_wear_units()

    def panel_health(self) -> float:
        return max(0.0, 100.0 * (1.0 - self.wear_units() / ESTIMATED_FULL_CYCLES))

    def cadence(self) -> str:
        frac = 1.0 - self.wear_units() / ESTIMATED_FULL_CYCLES
        if frac > CADENCE_STANDARD_MAX:
            return "standard"
        if frac > CADENCE_CONSERVATIVE_MAX:
            return "conservative"
        return "preserve"

    def partial_budget(self, base_recharge_limit: int = 20) -> int:
        cad = self.cadence()
        base = max(2, int(base_recharge_limit))
        if cad == "standard":
            return base
        if cad == "conservative":
            return max(2, int(base * 0.6))
        return 2  # preserve: minimal partial streak before a full wash

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "cumulative_full_refreshes": int(self._full),
                "cumulative_partial_refreshes": int(self._partial),
                "prior_wear_units": round(self.prior_wear_units(), 3),
                "recognized_wear_units": round(self._recognized_wear_units, 3),
                "recognized_age_years": round(self._recognized_age_years, 3),
                "full_refresh_interval_min": round(self._full_refresh_interval_min, 3),
                "wear_units": round(self.wear_units(), 2),
                "estimated_full_endurance": ESTIMATED_FULL_CYCLES,
                "panel_health_percent": round(self.panel_health(), 3),
                "refresh_cadence": self.cadence(),
                "suggested_recharge_limit": self.partial_budget(),
                "tracked_since_unix": int(self._since or 0),
            }

    def apply_override(self, *, known_wear_units: float = None,
                       panel_age_years: float = None,
                       full_refresh_interval_min: float = None) -> dict:
        """Seed the budget with a panel's pre-existing wear.

        Use one of:
          * ``known_wear_units``  - operator knows exactly how much life is gone
            (e.g. from a previous board's tally, an RMA report, or a panel that
            was already driven by this software on another host).
          * ``panel_age_years``   - only the panel's age is known; prior wear is
            estimated from the panel's duty cycle.  Pass
            ``full_refresh_interval_min`` (minutes between full refreshes) so
            the estimate reflects the panel's real cadence; defaults to
            ``DEFAULT_FULL_REFRESH_INTERVAL_MIN`` (a heavy cadence, so the
            estimate errs on the side of being protective).

        Both age/exact values may be provided (explicit wear wins).
        ``full_refresh_interval_min`` always updates the duty-cycle assumption
        used for any *age-based* estimate.  Results persist across reboots.
        """
        if full_refresh_interval_min is not None and full_refresh_interval_min > 0:
            self._full_refresh_interval_min = float(full_refresh_interval_min)
        with self._lock:
            if known_wear_units is not None:
                self._recognized_wear_units = max(0.0, float(known_wear_units))
                self._recognized_age_years = 0.0
            if panel_age_years is not None:
                self._recognized_age_years = max(0.0, float(panel_age_years))
                if known_wear_units is None:
                    self._recognized_wear_units = 0.0
            self._save()
        return self.snapshot()

    def reset(self):
        """Reset observed wear (e.g. after panel replacement).

        Clears nothing else; a recognized prior-wear override (a refurbished
        panel) is preserved so it is not accidentally wiped here. Use
        ``apply_override(known_wear_units=0)`` to also clear an override.
        """
        with self._lock:
            self._full = 0
            self._partial = 0
            self._since = int(time.time())
            self._save()


# Convenience singleton used by the scheduler and telemetry.
HEALTH = None


def get_health(state_file: str = None) -> PanelHealth:
    global HEALTH
    if HEALTH is None:
        HEALTH = PanelHealth(state_file)
    return HEALTH
