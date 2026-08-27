"""Config schema migration tests."""
import copy
import pytest

from core.migrations import migrate, get_version, CURRENT_SCHEMA_VERSION


def test_current_version_flag_preserved():
    cfg = {"schema_version": CURRENT_SCHEMA_VERSION}
    assert get_version(cfg) == CURRENT_SCHEMA_VERSION


def test_missing_version_treated_as_v1():
    assert get_version({}) == 1


def test_non_numeric_version_treated_as_v1():
    assert get_version({"schema_version": "abc"}) == 1


def test_v1_config_migrates_to_current():
    old = {
        "display": {"orientation": 90},
        "playlists": {"main": {"layout": {"zones": {"zone1": {}}}}},
    }
    out = migrate(old)
    assert get_version(out) == CURRENT_SCHEMA_VERSION
    assert out["display"]["rotate"] is True
    assert out["display"]["rotation"] == 90


def test_migration_backfills_responsive_grid():
    old = {"playlists": {"main": {"layout": {"zones": {"z1": {}}}}}}
    out = migrate(old)
    z = out["playlists"]["main"]["layout"]["zones"]
    assert z["z1"].get("responsive") == "shrink"


def test_migration_is_idempotent_and_non_destructive():
    old = {"display": {"orientation": 0}, "custom": "keep-me"}
    once = migrate(copy.deepcopy(old))
    twice = migrate(once)
    assert twice == once  # no double-mutate
    assert twice["custom"] == "keep-me"  # user data untouched


def test_shape_migrations_do_not_throw_on_minimal_config():
    assert migrate({})["schema_version"] == CURRENT_SCHEMA_VERSION
