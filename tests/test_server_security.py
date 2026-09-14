"""Security-gate regression tests for the Flask web app.

Legacy (server/app.py) tested a hand-rolled cookie store; the Flask port uses a
signed session cookie. Same intent: once an admin password exists, disruptive
onboarding mutators require an authenticated session; before setup they stay
open; the claim endpoint remains token-bounded; wifi fields are sanitized.
"""
import json
import os
import sys

import pytest

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root)

from server.web.app import create_app  # noqa: E402
from server.web import security  # noqa: E402

MUTATORS = (
    "/api/onboarding/ap/start",
    "/api/onboarding/ap/stop",
    "/api/onboarding/wifi",
)


@pytest.fixture()
def app_ctx(tmp_path, monkeypatch):
    monkeypatch.setattr(security, "CONFIG_PATH", str(tmp_path / "config.json"))
    app = create_app()
    app.testing = True
    return app


def _cfg_setup(tmp_path, setup_done, password="pw"):
    if not setup_done:
        (tmp_path / "config.json").write_text("{}")
        return
    from werkzeug.security import generate_password_hash
    (tmp_path / "config.json").write_text(json.dumps(
        {"admin_password_hash": generate_password_hash(password, method="pbkdf2:sha256")}))


def test_mutators_blocked_when_post_setup_and_unauthenticated(app_ctx, tmp_path):
    _cfg_setup(tmp_path, True)
    c = app_ctx.test_client()
    for p in MUTATORS:
        r = c.post(p, json={})
        assert r.status_code == 401, (p, r.status_code)


def test_mutators_allowed_pre_setup(app_ctx, tmp_path):
    _cfg_setup(tmp_path, False)
    c = app_ctx.test_client()
    for p in MUTATORS:
        r = c.post(p, json={})
        assert r.status_code != 401, (p, r.status_code)


def test_mutators_allowed_post_setup_when_authenticated(app_ctx, tmp_path):
    _cfg_setup(tmp_path, True)  # real pbkdf2 hash for "pw"
    c = app_ctx.test_client()
    r = c.post("/api/auth/login", json={"password": "pw"})
    assert r.status_code == 200, r.status_code
    for p in MUTATORS:
        r2 = c.post(p, json={})
        assert r2.status_code != 401, (p, r2.status_code)


def test_claim_remains_open_post_setup(app_ctx, tmp_path):
    _cfg_setup(tmp_path, True)
    c = app_ctx.test_client()
    r = c.post("/api/onboarding/claim", json={})
    assert r.status_code != 401, r.status_code


def test_wifi_field_sanitizer_rejects_injection():
    from server.web.routes import _safe_wifi_field

    for bad in ('evil"network{', "a\\b", "a\nb", "a\rb", "a\tb", "a\x00b", "x" * 64):
        with pytest.raises(ValueError):
            _safe_wifi_field(bad)
    assert _safe_wifi_field("HomeNet-5G") == "HomeNet-5G"
    assert _safe_wifi_field("a very normal psk 123") == "a very normal psk 123"
