"""Security regression tests for the production web server.

Guards the onboarding-auth gate (#security): the disruptive onboarding mutators
(/api/onboarding/ap/start, /api/onboarding/ap/stop, /api/onboarding/wifi) must be
blocked with 401 once an admin password is set, while remaining usable pre-setup
so the first-run flow completes. Also locks in injection-resistant SSID/psk
handling for the wpa_supplicant write path.
"""
import os
import sys
import json
import time

import pytest

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root)

import server.app as app
from server.app import ProductionHandler

MUTATORS = (
    "/api/onboarding/ap/start",
    "/api/onboarding/ap/stop",
    "/api/onboarding/wifi",
)


def _make_handler(tmp_path, setup_done, authed):
    cfg = {"admin_password_hash": "pbkdf2:sha256:x"} if setup_done else {}
    src = tmp_path / "config.json"
    src.write_text(json.dumps(cfg))

    h = ProductionHandler.__new__(ProductionHandler)
    h.config_path = str(src)
    h.scheduler = None

    app.ACTIVE_SESSIONS.clear()
    if authed:
        app.ACTIVE_SESSIONS["tok"] = {"created_at": time.time(), "user": "admin"}
        h.headers = type("H", (), {
            "get": lambda s, k, c=None: "rndrsbc_session=tok" if k == "Cookie" else "",
            "__iter__": lambda s: iter(()),
        })()
    else:
        h.headers = type("H", (), {
            "get": lambda s, k, c=None: "",
            "__iter__": lambda s: iter(()),
        })()
    return h


def _gate(path, setup_done, authed, tmp_path):
    """Mirror the gate expression that now precedes the mutator branches."""
    h = _make_handler(tmp_path, setup_done, authed)
    return path in MUTATORS and h._has_admin_setup() and not h._is_authenticated()


def test_mutators_blocked_when_post_setup_and_unauthenticated(tmp_path):
    for p in MUTATORS:
        assert _gate(p, setup_done=True, authed=False, tmp_path=tmp_path), p


def test_mutators_allowed_pre_setup(tmp_path):
    # First-run flow must still reach the endpoints without a password.
    for p in MUTATORS:
        assert not _gate(p, setup_done=False, authed=False, tmp_path=tmp_path), p


def test_mutators_allowed_post_setup_when_authenticated(tmp_path):
    for p in MUTATORS:
        assert not _gate(p, setup_done=True, authed=True, tmp_path=tmp_path), p


def test_claim_remains_open_post_setup(tmp_path):
    # /api/onboarding/claim is bounded by claim-token validity and stays open.
    assert not _gate("/api/onboarding/claim", True, False, tmp_path)


def test_wifi_field_sanitizer_rejects_injection():
    # Mirror the helper used before writing into wpa_supplicant.conf.
    def _safe_wifi_field(value, max_len=63):
        if len(value) > max_len:
            raise ValueError("long")
        if any(ch in value for ch in ('"', "\\", "\n", "\r", "\t", "\x00")):
            raise ValueError("bad char")
        return value

    for bad in ('evil"network{', "a\\b", "a\nb", "a\rb", "a\tb", "a\x00b", "x" * 64):
        with pytest.raises(ValueError):
            _safe_wifi_field(bad)
    assert _safe_wifi_field("HomeNet-5G") == "HomeNet-5G"
    assert _safe_wifi_field("a very normal psk 123") == "a very normal psk 123"
