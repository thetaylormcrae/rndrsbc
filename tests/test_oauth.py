"""OAuth 2.0 core tests: PKCE mechanics, code+device exchange, vendor map."""
import json
import hashlib
import base64

import pytest

from core.oauth import (
    OAuthClient, OAuthError, vendored_client,
    generate_code_verifier, code_challenge, generate_state,
    SecureSecretsStore, VENDORS,
)


# ---- helpers --------------------------------------------------------------

class FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.headers = {"content-type": "application/json"}

    def json(self):
        return self._payload

    @property
    def text(self):
        return json.dumps(self._payload)


class FakeTransport:
    """Records token-url posts and returns canned responses."""
    def __init__(self, token_resp=None, device_resp=None):
        self.token_resp = token_resp or {"access_token": "at1", "expires_in": 3600}
        self.device_resp = device_resp
        self.requests = []

    def post_form(self, url, data, headers=None):
        self.requests.append({"url": url, "data": data, "headers": headers})
        if "device" in url:
            return FakeResp(self.device_resp)
        return FakeResp(self.token_resp)


def _client(transport=None, store=None):
    return OAuthClient(
        auth_url="https://as.example/auth",
        token_url="https://as.example/token",
        client_id="cid",
        client_secret="csecret",
        scopes=["read"],
        redirect_uri="urn:ietf:wg:oauth:2.0:oob",
        store=store or SecureSecretsStore(path="/tmp/_rndr_oauth_test.json"),
        transport=transport or FakeTransport(),
    )


def _wipe(path="/tmp/_rndr_oauth_test.json"):
    import os
    if os.path.exists(path):
        os.remove(path)


# ---- PKCE mechanics -------------------------------------------------------

def test_code_verifier_length_in_rfc_range():
    v = generate_code_verifier()
    assert 43 <= len(v) <= 128


def test_code_challenge_is_sha256_s256():
    v = generate_code_verifier()
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(v.encode("ascii")).digest()
    ).rstrip(b"=").decode()
    assert code_challenge(v) == expected


def test_authorization_url_includes_pkce_and_state():
    c = _client()
    url, state = c.authorization_url()
    assert "code_challenge=" in url
    assert "code_challenge_method=S256" in url
    assert "state=" in url
    assert state and len(state) > 10


def test_exchange_sends_verifier_and_stores_token():
    _wipe()
    c = _client()
    url, state = c.authorization_url()
    tok = c.exchange_code("the-code", state)
    assert tok["access_token"] == "at1"
    assert c.has_token() is True
    # the verifier must be sent in the exchange payload
    sent = c._transport.requests[-1]["data"]
    assert "code_verifier" in sent
    assert sent["code"] == "the-code"


def test_exchange_without_pending_raises():
    _wipe()
    c = _client()
    with pytest.raises(RuntimeError):
        c.exchange_code("code")


def test_token_endpoint_error_raises_oauth_error():
    _wipe()
    t = FakeTransport(token_resp={})
    c = OAuthClient(
        auth_url="https://a", token_url="https://t", client_id="cid",
        store=SecureSecretsStore(path="/tmp/_rndr_oauth_test.json"), transport=t,
    )
    class BadResp:
        status_code = 400
        headers = {"content-type": "application/json"}
        def json(self): return {"error": "invalid_grant"}
        @property
        def text(self): return '{"error":"invalid_grant"}'
    t.post_form = lambda *a, **k: BadResp()
    c.authorization_url()
    with pytest.raises(OAuthError):
        c.exchange_code("bad")


# ---- device flow ----------------------------------------------------------

def test_device_flow_returns_user_code_to_display():
    _wipe()
    t = FakeTransport(
        device_resp={"user_code": "ABCD-EFGH", "verification_uri": "https://x/device",
                     "device_code": "dc1", "interval": 1, "expires_in": 600},
    )
    c = _client(transport=t)
    info = c.start_device_flow("https://as.example/device/code")
    assert info["user_code"] == "ABCD-EFGH"
    assert info["verification_uri"]


def test_device_flow_access_denied_raises_permission_error():
    _wipe()
    c = _client()
    c._device = {"device_code": "dc1", "interval": 1}
    class Denied:
        status_code = 400
        headers = {"content-type": "application/json"}
        def json(self): return {"error": "access_denied"}
    c._transport.post_form = lambda *a, **k: Denied()
    with pytest.raises(PermissionError):
        c.poll_device_flow(slow_down=0)


# ---- vendor map -----------------------------------------------------------

def test_vendors_contain_expected_consumers():
    for v in ("google", "microsoft", "amazon", "nextcloud"):
        assert v in VENDORS


def test_vendored_client_google_loads_endpoints():
    c = vendored_client("google", {"client_id": "gid"})
    assert c.client_id == "gid"
    assert c.token_url == "https://oauth2.googleapis.com/token"
    assert c.device_auth_url


def test_vendored_client_empty_creds_builds_idempotent():
    c = vendored_client("google", {})
    assert c.client_id == ""  # no creds -> empty, no throw
    assert c.token_url == "https://oauth2.googleapis.com/token"
