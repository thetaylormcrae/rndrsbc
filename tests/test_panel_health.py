"""Panel-health governor tests: wear model, persistence, prior-wear overrides."""
import json
import os

import pytest

from core.panel_health import (
    PanelHealth, get_health,
    ESTIMATED_FULL_CYCLES, PARTIAL_TO_FULL_RATIO,
    annual_full_cycles,
)


def _tmp_state(tmp_path, **initial):
    f = tmp_path / "health.json"
    if initial:
        f.write_text(json.dumps(initial))
    return str(f)


# --- annual_full_cycles math ---

def test_annual_full_cycles_15min():
    assert abs(annual_full_cycles(15) - 35_040) < 1  # 365*24*60/15


def test_annual_full_cycles_default_4min():
    assert abs(annual_full_cycles(4) - 131_400) < 1


# --- wear accumulation & persistence ---

def test_records_full_refresh_and_persists(tmp_path):
    f = _tmp_state(tmp_path)
    h = PanelHealth(f)
    h.record("full")
    h2 = PanelHealth(f)  # fresh instance reads the SAME file (reboot)
    snap = h2.snapshot()
    assert snap["wear_units"] == 1
    assert snap["cumulative_full_refreshes"] == 1
    assert snap["cumulative_partial_refreshes"] == 0


def test_partial_ratio_accounting(tmp_path):
    f = _tmp_state(tmp_path)
    h = PanelHealth(f)
    for _ in range(PARTIAL_TO_FULL_RATIO):
        h.record("partial")
    snap = h.snapshot()
    assert snap["cumulative_partial_refreshes"] == PARTIAL_TO_FULL_RATIO
    assert snap["wear_units"] == pytest.approx(1.0)


def test_fresh_panel_health_is_full(tmp_path):
    h = PanelHealth(_tmp_state(tmp_path))
    assert h.snapshot()["panel_health_percent"] == pytest.approx(100.0)


def test_unknown_type_ignored(tmp_path):
    h = PanelHealth(_tmp_state(tmp_path))
    h.record("bogus")
    assert h.snapshot()["wear_units"] == 0


# --- cadence transitions ---

def test_pristine_cadence_is_standard(tmp_path):
    h = PanelHealth(_tmp_state(tmp_path))
    assert h.snapshot()["refresh_cadence"] == "standard"


def test_cadence_steps_down_as_budget_depletes(tmp_path):
    h = PanelHealth(_tmp_state(tmp_path))
    h.apply_override(known_wear_units=ESTIMATED_FULL_CYCLES * 0.7)
    snap = h.snapshot()
    assert snap["panel_health_percent"] < 50
    assert snap["refresh_cadence"] in ("conservative", "preserve")


def test_near_exhausted_cadence_is_preserve(tmp_path):
    h = PanelHealth(_tmp_state(tmp_path))
    h.apply_override(known_wear_units=ESTIMATED_FULL_CYCLES * 0.92)  # 8% left
    assert h.snapshot()["refresh_cadence"] == "preserve"


def test_partial_budget_throttles_by_cadence(tmp_path):
    h = PanelHealth(_tmp_state(tmp_path))
    full_budget = h.partial_budget(20)
    h.apply_override(known_wear_units=ESTIMATED_FULL_CYCLES * 0.8)
    conserve_budget = h.partial_budget(20)
    assert conserve_budget < full_budget


# --- prior-wear overrides ---

def test_known_wear_override_applied(tmp_path):
    h = PanelHealth(_tmp_state(tmp_path))
    h.apply_override(known_wear_units=250_000)
    snap = h.snapshot()
    assert snap["recognized_wear_units"] == 250_000
    assert snap["panel_health_percent"] == pytest.approx(50.0)


def test_age_override_implies_wear(tmp_path):
    h = PanelHealth(_tmp_state(tmp_path))
    h.apply_override(full_refresh_interval_min=15, panel_age_years=14.27)
    # 14.27 yr * 35,040/yr ~= 500,000 units -> nearly exhausted
    assert h.snapshot()["panel_health_percent"] < 1.0


def test_explicit_wear_beats_age(tmp_path):
    h = PanelHealth(_tmp_state(tmp_path))
    h.apply_override(known_wear_units=1000, panel_age_years=10)
    snap = h.snapshot()
    assert snap["recognized_wear_units"] == 1000
    assert snap["panel_health_percent"] > 99


def test_interval_persists_across_reload(tmp_path):
    f = _tmp_state(tmp_path)
    h = PanelHealth(f)
    h.apply_override(panel_age_years=1, full_refresh_interval_min=30)
    h2 = PanelHealth(f)
    assert h2.snapshot()["full_refresh_interval_min"] == 30


# --- helper / singleton ---

def test_get_health_returns_governor(tmp_path):
    h = get_health(str(tmp_path / "h.json"))
    assert isinstance(h, PanelHealth)
