"""Tests for the Flask web server rewrite.

Locks in the security architecture: auth gating, CSRF enforcement,
config-write protection, and rate limiting.
"""
import importlib
import json
import os
import sys
import tempfile
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


@pytest.fixture()
def client(monkeypatch):
    tmp = tempfile.mkdtemp()
    import core.paths
    monkeypatch.setattr(core.paths, "CONFIG_PATH", os.path.join(tmp, "config.json"), raising=False)
    import server.web.security as security
    importlib.reload(security)
    import server.web.model as model
    importlib.reload(model)
    import server.web.routes as routes
    importlib.reload(routes)
    from server.web.app import create_app
    app = create_app(scheduler=None)
    app.testing = True
    def make_client():
        return app.test_client()
    return app.test_client(), security, model, make_client


def _csrf(client):
    html = client.get("/").data.decode()
    return html.split("window.__CSRF__=")[1].split(";")[0].strip('"')


class TestAuth:
    def test_setup_then_apis_lock(self, client):
        c, _, _, factory = client
        r = c.post("/api/setup", json={"password": "correcthorse"})
        assert r.status_code == 200
        fresh = factory()
        assert fresh.get("/api/config").status_code == 401
        assert fresh.get("/api/telemetry").status_code == 401
        # the setup client itself is authenticated (setup logs you in)

    def test_setup_rejects_short_password(self, client):
        c, _, _, factory = client
        r = c.post("/api/setup", json={"password": "short"})
        assert r.status_code == 400

    def test_setup_rejects_second_setup(self, client):
        c, _, _, factory = client
        c.post("/api/setup", json={"password": "correcthorse"})
        r = c.post("/api/setup", json={"password": "anotherpass1"})
        assert r.status_code == 400

    def test_login_success_sets_session(self, client):
        c, _, _, factory = client
        c.post("/api/setup", json={"password": "correcthorse"})
        c.get("/logout-fix")  # clear state
        r = c.post("/api/auth/login", json={"password": "correcthorse"})
        assert r.status_code == 200
        assert r.get_json()["csrf_token"]

    def test_wrong_password_401(self, client):
        c, _, _, factory = client
        c.post("/api/setup", json={"password": "correcthorse"})
        r = c.post("/api/auth/login", json={"password": "nope"})
        assert r.status_code == 401

    def test_rate_limit_lockout(self, client):
        c, _, _, factory = client
        c.post("/api/setup", json={"password": "correcthorse"})
        for _ in range(5):
            r = c.post("/api/auth/login", json={"password": "wrong"})
            assert r.status_code == 401
        r = c.post("/api/auth/login", json={"password": "correcthorse"})
        assert r.status_code == 429

    def test_logout_clears_session(self, client):
        c, _, _, factory = client
        c.post("/api/setup", json={"password": "correcthorse"})
        c.post("/api/auth/login", json={"password": "correcthorse"})
        assert c.get("/api/config").status_code == 200
        c.post("/api/auth/logout")
        assert c.get("/api/config").status_code == 401

    def test_bearer_token_grants_access(self, client):
        c, _, _, factory = client
        c.post("/api/setup", json={"password": "correcthorse"})
        c.post("/api/auth/login", json={"password": "correcthorse"})
        r = c.post("/api/auth/bearer", headers={"X-CSRF-Token": _csrf(c)})
        tok = r.get_json()["token"]
        c.post("/api/auth/logout")
        r = c.get("/api/config", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 200


class TestCSRF:
    def test_config_write_blocked_without_token(self, client):
        c, _, _, factory = client
        c.post("/api/setup", json={"password": "correcthorse"})
        c.post("/api/auth/login", json={"password": "correcthorse"})
        r = c.post("/api/config", json={"device": {"name": "x"}})
        assert r.status_code == 403

    def test_config_write_allowed_with_token(self, client):
        c, _, _, factory = client
        c.post("/api/setup", json={"password": "correcthorse"})
        c.post("/api/auth/login", json={"password": "correcthorse"})
        r = c.post("/api/config", json={"device": {"name": "x"}},
                   headers={"X-CSRF-Token": _csrf(c)})
        assert r.status_code == 200

    def test_csrf_token_issued_per_session(self, client):
        c, _, _, factory = client
        r1 = c.post("/api/setup", json={"password": "correcthorse"})
        t1 = r1.get_json()["csrf_token"]
        assert t1  # a token was issued for this session
        c2 = factory()
        r2 = c2.post("/api/setup", json={"password": "anotherpass1"})
        assert r2.status_code == 400  # second setup is rejected


class TestConfigModel:
    def test_write_preserves_playlists(self, client):
        c, _, model, factory = client
        c.post("/api/setup", json={"password": "correcthorse"})
        c.post("/api/auth/login", json={"password": "correcthorse"})
        csrf = _csrf(c)
        c.post("/api/config", json={"device": {"name": "x"}}, headers={"X-CSRF-Token": csrf})
        # Now save with a partial payload missing playlists - must NOT wipe them
        r = c.post("/api/config", json={"quiet_hours": {"enabled": True}},
                   headers={"X-CSRF-Token": csrf})
        assert r.status_code == 200
        cfg = r.get_json()["config"]
        assert "playlists" in cfg, "playlists must survive partial save"
        assert cfg["playlists"], "playlists must not be emptied"

    def test_protected_keys_never_written(self, client):
        c, _, model, factory = client
        c.post("/api/setup", json={"password": "correcthorse"})
        c.post("/api/auth/login", json={"password": "correcthorse"})
        csrf = _csrf(c)
        r = c.post("/api/config",
                   json={"admin_password_hash": "attacker-value"},
                   headers={"X-CSRF-Token": csrf})
        assert r.status_code == 200
        import core.paths
        raw = json.load(open(core.paths.CONFIG_PATH))
        assert raw["admin_password_hash"] != "attacker-value"

    def test_password_hash_never_leaks_in_get(self, client):
        c, _, _, factory = client
        c.post("/api/setup", json={"password": "correcthorse"})
        c.post("/api/auth/login", json={"password": "correcthorse"})
        r = c.get("/api/config")
        assert "admin_password_hash" not in json.dumps(r.get_json())


class TestPages:
    PAGES = ["/", "/playlists", "/widgets", "/settings", "/photos", "/system"]

    def test_all_pages_serve_with_csrf_bootstrap(self, client):
        c, _, _, factory = client
        for p in self.PAGES:
            r = c.get(p)
            assert r.status_code == 200, p
            assert "window.__CSRF__" in r.data.decode(), p

    def test_unknown_page_404(self, client):
        c, _, _, factory = client
        assert c.get("/does-not-exist").status_code == 404


class TestStaticAssets:
    def test_font_served(self, client):
        c, _, _, factory = client
        assert c.get("/assets/fonts/Roboto-Regular.ttf").status_code == 200

    def test_traversal_blocked(self, client):
        c, _, _, factory = client
        assert c.get("/assets/../../../etc/passwd").status_code == 404

    def test_unknown_asset_404(self, client):
        c, _, _, factory = client
        assert c.get("/assets/nonexistent.png").status_code == 404


def test_bearer_issue_without_data_dir(monkeypatch, tmp_path):
    """Regression (CI): a fresh checkout has no data/ (gitignored), so
    issue_bearer_token() must create the directory, not crash."""
    from core import paths
    import server.web.security as sec

    missing = tmp_path / "data"          # does NOT exist
    monkeypatch.setattr(paths, "DATA_DIR", str(missing))
    token = sec.issue_bearer_token()
    assert token.startswith("rndr_")
    assert missing.exists()              # dir auto-created
    assert token in sec.bearer_tokens()
