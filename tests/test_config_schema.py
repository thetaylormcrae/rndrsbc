"""Tests for core.config_schema fail-fast validation (prod-readiness #2)."""
import pytest

from core.config_schema import validate_config, ConfigError, SUPPORTED_DRIVERS


def basic():
    return {
        "display": {"driver": "virtual", "model": "epd7in3f", "orientation": 0},
        "active_playlist": "main",
        "playlists": {"main": ["weather", "clock"]},
    }


def test_valid_config_passes_cleanly():
    cfg, warns = validate_config(basic())
    assert warns == []


def test_wrong_type_on_known_key_fails():
    cfg = basic()
    cfg["active_playlist"] = 42
    with pytest.raises(ConfigError):
        validate_config(cfg)


def test_bad_driver_enum_fails():
    cfg = basic()
    cfg["display"]["driver"] = "not-a-driver"
    with pytest.raises(ConfigError) as e:
        validate_config(cfg)
    assert "not-a-driver" in str(e.value) and "unsupported" in str(e.value)


def test_bad_orientation_fails():
    cfg = basic()
    cfg["display"]["orientation"] = 45
    with pytest.raises(ConfigError):
        validate_config(cfg)


def test_bad_refresh_mode_fails():
    cfg = basic()
    cfg["display"]["refresh_mode"] = "sometimes"
    with pytest.raises(ConfigError):
        validate_config(cfg)


def test_unknown_top_key_warns_not_fails():
    cfg = basic()
    cfg["brand_new_plugin_area"] = 1  # not in _known -> should warn, not brick
    cfg, warns = validate_config(cfg)
    assert any("brand_new_plugin_area" in w for w in warns)


def test_missing_required_key_fails():
    cfg = basic()
    del cfg["active_playlist"]
    with pytest.raises(ConfigError) as e:
        validate_config(cfg)
    assert "active_playlist" in str(e.value)


def test_panel_health_type_checked():
    cfg = basic()
    cfg["display"]["panel_health"] = {"known_wear_units": "lots"}  # wrong type
    with pytest.raises(ConfigError):
        validate_config(cfg)
    cfg["display"]["panel_health"] = {"known_wear_units": 400000}
    _cfg, warns = validate_config(cfg)
    assert warns == []


def test_real_default_config_shape_is_accepted():
    """The platform's own default config ships keys the schema doesn't declare
    (quiet_hours, object-form playlists). It must warn, never brick boot."""
    cfg = {
        "device": {"name": "RPi Zero 2W", "timezone": "America/New_York"},
        "display": {"driver": "virtual", "model": "epd7in3f", "orientation": 0},
        "quiet_hours": {"enabled": False, "start": "23:00", "end": "06:00", "mode": "suspend"},
        "active_playlist": "main",
        "playlists": {"main": {"name": "Main Rotation", "items": [
            {"widget": "weather", "duration_minutes": 5,
             "settings": {"city": "Oslo"}},
        ]}},
    }
    _cfg, warns = validate_config(cfg)  # must NOT raise; no spurious warnings
    assert warns == []


def test_playlist_item_widget_type_fails():
    cfg = basic()
    cfg["playlists"] = {"main": [{"widget": 7, "duration_minutes": 5}]}
    with pytest.raises(ConfigError) as e:
        validate_config(cfg)
    assert "widget" in str(e.value)


def test_configerror_lists_all_problems_in_one_pass():
    cfg = {
        "display": {"driver": "nope", "orientation": 45, "refresh_mode": "banana"},
        "playlists": {},
        # missing active_playlist
    }
    with pytest.raises(ConfigError) as e:
        validate_config(cfg)
    msg = str(e.value)
    assert all(term in msg for term in ("nope", "45", "banana", "active_playlist"))


def test_json_string_input_accepted():
    import json
    cfg, warns = validate_config(json.dumps(basic()))
    assert warns == []
