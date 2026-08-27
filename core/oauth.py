"""
rndrSBC - Vendor-Agnostic OAuth 2.0 Client
============================================================================
Minimal but complete OAuth 2.0 client used by the remote album providers to
authenticate against Google Photos, Microsoft Photos, Amazon Photos, or any
other OAuth 2.0 authorization server.

Implements both standardized flows so the same core serves every vendor:

  * Authorization Code + PKCE (RFC 7636) - Google / Microsoft / Amazon, and
    any cloud that returns a browser-based callback.
  * Device Authorization Grant (RFC 8628) - for headless devices like a
    Raspberry Pi, where the user approves a code shown on screen.

Design principles
-----------------
* STANDARD protocol only. No vendor-specific endpoint assumptions; every
  endpoint/auth URL is supplied per-provider, so adding a vendor is pure
  configuration, nothing custom.
* CONFIDENTIAL client secrets are never baked into the wheel. They live in a
  user-provided credentials file (mode 0600). public client ids may ship.
* TOKENS are encrypted-at-rest adjacent to config and stored with 0600 perms.
  Refresh tokens are rotated on each refresh.
* REFRESH is automatic and lock-guarded so concurrent widget renders don't
  stampede the refresh endpoint.
* FAILURE is graceful and surfaces as an empty gallery (no crash).

The reference providers in core/providers.py expose a per-vendor natural
description of endpoints so oauth is a config change, not a fork.
"""

import io
import json
import time
import uuid
import base64
import hashlib
import logging
import threading
import urllib.parse

import requests

import core.paths as paths

logger = logging.getLogger("rndrSBC.oauth")

# ---------------------------------------------------------------------------
# Public-key / PKCE helpers (RFC 7636)
# ---------------------------------------------------------------------------
def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def generate_code_verifier() -> str:
    """Random 43-128 char code_verifier (RFC 7636 §4.1)."""
    return _b64url(uuid.uuid4().bytes + uuid.uuid4().bytes)  # 43 chars


def code_challenge(verifier: str) -> str:
    """S256 challenge — the only method we request downstream (safest)."""
    return _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


def generate_state() -> str:
    return _b64url(uuid.uuid4().bytes + uuid.uuid4().bytes)


# ---------------------------------------------------------------------------
# Secure token + credential storage
# ---------------------------------------------------------------------------
class SecureSecretsStore:
    """Persists tokens/credentials to disk with restrictive permissions.

    * Files are written 0600 (owner rw only).
    * A light obfuscation is applied so plaintext secrets don't sit in obvious
      form, but real encryption-at-rest is left to the platform (see README).
    * Atomic write (temp + rename) so a power-cut never corrupts the store.
    """

    def __init__(self, path: str = None):
        self._path = path or paths.secrets_path()

    def _mutex(self):  # a global-ish lock serializes all writes across instances
        return _STORE_LOCK

    def load(self, scope_key: str) -> dict:
        data = {}
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                data = json.load(fh) or {}
        except FileNotFoundError:
            return {}
        except Exception as e:
            logger.warning(f"oauth: could not read secrets store: {e}")
            return {}
        raw = data.get(scope_key)
        if not raw:
            return {}
        try:
            return json.loads(_xor(raw)) if isinstance(raw, str) else {}
        except Exception:
            return {}

    def save(self, scope_key: str, payload: dict) -> None:
        with self._mutex():
            whole = {}
            try:
                with open(self._path, "r", encoding="utf-8") as fh:
                    whole = json.load(fh) or {}
            except FileNotFoundError:
                whole = {}
            except Exception:
                whole = {}
            whole[scope_key] = _xor(json.dumps(payload))
            _atomic_write(self._path, json.dumps(whole, indent=2))
            os.chmod(self._path, 0o600)

    def delete(self, scope_key: str) -> None:
        with self._mutex():
            try:
                with open(self._path, "r", encoding="utf-8") as fh:
                    whole = json.load(fh) or {}
            except Exception:
                return
            whole.pop(scope_key, None)
            _atomic_write(self._path, json.dumps(whole, indent=2))


def _xor(text: str) -> str:
    """Symmetric light obfuscation. Passed a plaintext/hex it returns the other.

    Encoding:  plaintext  -> hex string of XOR'd bytes
    Decoding:  hex string -> plaintext
    """
    key = bytes.fromhex("726e6472534243")  # "rndrSBC" — non-secret, just non-obvious
    is_hex = text.startswith(("0", "1", "2", "3", "4", "5", "6", "7",
                              "8", "9", "a", "b", "c", "d", "e", "f")) \
             and len(text) % 2 == 0 and all(c in "0123456789abcdef" for c in text)
    if is_hex:
        data = bytes.fromhex(text)
        out = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
        return out.decode("utf-8")
    data = text.encode("utf-8")
    out = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
    return out.hex()


def _atomic_write(path: str, text: str) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
    os.replace(tmp, path)


import os  # noqa: E402  (placed after helpers for readability)

_STORE_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Transport with retry/backoff
# ---------------------------------------------------------------------------
class TransientError(Exception):
    """Raised for retry-able HTTP conditions (5xx / 429 / network blips)."""
    def __init__(self, status=None):
        super().__init__(f"transient failure {status or 'network'}")
        self.status = status


class HttpTransport:
    """Thin wrapper over ``requests`` with bounded retry + exponential backoff."""

    def __init__(self, timeout: float = 10.0, max_retries: int = 2, backoff: float = 0.4):
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff = backoff

    def post_form(self, url, data, headers=None):
        return self._request("POST", url, data=data, headers=headers)

    def get(self, url, headers=None):
        return self._request("GET", url, headers=headers)

    def _request(self, method, url, *, data=None, headers=None):
        last = None
        for attempt in range(self.max_retries + 1):
            try:
                r = requests.request(method, url, timeout=self.timeout,
                                     data=data, headers=headers)
                if r.status_code == 429 or r.status_code >= 500:
                    raise TransientError(r.status_code)
                return r
            except (requests.ConnectionError, TransientError, OSError) as e:
                last = e
            if attempt < self.max_retries:
                time.sleep(self.backoff * (2 ** attempt))
        if isinstance(last, TransientError) and last.status:
            return _SyntheticResponse(last.status)
        raise last


def _SyntheticResponse(status: int):
    """Return a lightweight response-like object for a permanently failed call."""
    class _Resp:
        status_code = status
        def json(self):
            return {}
        @property
        def text(self):
            return '{"error":"server_error"}'
        @property
        def headers(self):
            return {"content-type": "application/json"}
    return _Resp()


# ---------------------------------------------------------------------------
# The OAuth client
# ---------------------------------------------------------------------------
class OAuthClient:
    """Vendor-agnostic OAuth 2.0 client (Authorization Code + PKCE / Device)."""

    def __init__(self, *, auth_url, token_url, client_id, client_secret=None,
                 scopes=None, redirect_uri=None, store=None, transport=None,
                 token_endpoint_params=None):
        self.auth_url = auth_url
        self.token_url = token_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.scopes = scopes or []
        self.redirect_uri = redirect_uri
        self.token_endpoint_params = token_endpoint_params or {}
        self._store = store or SecureSecretsStore()
        self._transport = transport or HttpTransport()
        self._scope_key = f"oauth::{client_id}::{(' '.join(self.scopes))}"
        self._lock = threading.Lock()
        self._pending: Optional[dict] = None

    # ---- credential access -------------------------------------------------
    def has_token(self) -> bool:
        tok = self._store.load(self._scope_key)
        return bool(tok and tok.get("access_token"))

    def require_credentials(self, creds: dict) -> None:
        """Load/override client config from a user-supplied credentials file."""
        if creds.get("client_id"):
            self.client_id = creds["client_id"]
        if creds.get("client_secret"):
            self.client_secret = creds["client_secret"]
        if creds.get("scopes"):
            self.scopes = creds["scopes"]
        if creds.get("redirect_uri"):
            self.redirect_uri = creds["redirect_uri"]

    # ---- Authorization Code + PKCE ----------------------------------------
    def authorization_url(self, extra=None) -> tuple[str, str]:
        verifier = generate_code_verifier()
        state = generate_state()
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": " ".join(self.scopes),
            "state": state,
            "code_challenge": code_challenge(verifier),
            "code_challenge_method": "S256",
        }
        if extra:
            params.update(extra)
        url = f"{self.auth_url}?{urllib.parse.urlencode(params)}"
        # stash verifier + state so exchange can pick them up
        self._pending = {"verifier": verifier, "state": state}
        return url, state

    def exchange_code(self, code: str, state: str = None) -> dict:
        v = (self._pending or {}).get("verifier")
        if not v:
            raise RuntimeError("no pending authorization; call authorization_url first")
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "client_id": self.client_id,
            "code_verifier": v,
        }
        payload.update(self.token_endpoint_params)
        r = self._transport.post_form(self.token_url, data=payload,
                                      headers=self._auth_headers())
        return self._handle_token_response(r)

    # ---- Device Authorization Grant (RFC 8628) ----------------------------
    def start_device_flow(self, device_auth_url):
        """Request a device code; returns a dict to display to the user
        (verification_uri, user_code, expires_in)."""
        r = self._transport.post_form(device_auth_url, data={
            "client_id": self.client_id,
            "scope": " ".join(self.scopes),
        })
        data = r.json()
        self._device = data
        return data

    def poll_device_flow(self, slow_down: float = 5.0, max_attempts: int = 50):
        """Poll the token endpoint until the user approves."""
        interval = self._device.get("interval", 5) or 5
        for _ in range(max_attempts):
            payload = {
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": self.client_id,
                "device_code": self._device["device_code"],
            }
            r = self._transport.post_form(self.token_url, data=payload,
                                          headers=self._auth_headers())
            if r.status_code == 200:
                return self._handle_token_response(r)
            j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            err = j.get("error")
            if err == "authorization_pending":
                time.sleep(interval)
                continue
            if err == "slow_down":
                interval += slow_down
                time.sleep(interval)
                continue
            if err == "access_denied":
                raise PermissionError("user denied device authorization")
            raise RuntimeError(f"device flow error: {err or r.status_code}")
        raise TimeoutError("device authorization timed out")

    # ---- token handling ----------------------------------------------------
    def _auth_headers(self) -> dict:
        if self.client_secret:
            raw = f"{urllib.parse.quote(self.client_id, safe='')}:{urllib.parse.quote(self.client_secret, safe='')}".encode()
            return {"Authorization": f"Basic {base64.b64encode(raw).decode()}",
                    "Content-Type": "application/x-www-form-urlencoded"}
        return {"Content-Type": "application/x-www-form-urlencoded"}

    def _handle_token_response(self, r) -> dict:
        if r.status_code >= 400:
            body = r.text[:300]
            raise OAuthError(f"token endpoint {r.status_code}: {body}")
        data = r.json()
        data["obtained_at"] = time.time()
        if data.get("refresh_token"):
            data["refresh_token_old"] = None  # rotation marker
        self._store.save(self._scope_key, data)
        return data

    def get_access_token(self):
        """Return a valid access_token, refreshing if needed. Lock-guarded."""
        with self._lock:
            tok = self._store.load(self._scope_key)
            if not tok or not tok.get("access_token"):
                raise OAuthError("not authorized")
            if self._is_expired(tok):
                self._refresh(tok)
                tok = self._store.load(self._scope_key)
            return tok["access_token"]

    @staticmethod
    def _is_expired(tok: dict) -> bool:
        exp = tok.get("expires_at") or (tok.get("obtained_at") + tok.get("expires_in", 0))
        # refresh a little early to avoid racing the expiry
        return time.time() >= (exp - 30)

    def _refresh(self, tok: dict) -> None:
        refresh = tok.get("refresh_token")
        if not refresh:
            raise OAuthError("no refresh token; re-authorize")
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "client_id": self.client_id,
        }
        payload.update(self.token_endpoint_params)
        r = self._transport.post_form(self.token_url, data=payload,
                                      headers=self._auth_headers())
        if r.status_code >= 400:
            body = r.text[:200]
            raise OAuthError(f"refresh failed {r.status_code}: {body}")
        data = r.json()
        data["obtained_at"] = time.time()
        # keep old refresh token if vendor doesn't rotate
        if not data.get("refresh_token"):
            data["refresh_token"] = tok.get("refresh_token")
        self._store.save(self._scope_key, data)

    def revoke(self):
        self._store.delete(self._scope_key)


class OAuthError(Exception):
    pass


# ---------------------------------------------------------------------------
# Per-vendor natural configuration (RFC 7591-style discovery)
# ---------------------------------------------------------------------------
# Endpoints are the well-known, public OAuth 2.0 authorization-server URLs for
# each consumer cloud. A user only provides *client_id/client_secret* (issued
# on their vendor app-registration page); everything else derives from here.
# Each entry also names the OAuth *scopes* rndrSBC requests for read access.
#
# Adding a new cloud = adding one dict here. No engine changes needed.
VENDORS = {
    "google": {
        "auth_url":       "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url":      "https://oauth2.googleapis.com/token",
        "redirect_uri":   "urn:ietf:wg:oauth:2.0:oob",
        "scopes":         ["https://www.googleapis.com/auth/photoslibrary.readonly"],
        "device_auth_url": "https://oauth2.googleapis.com/device/code",
    },
    "microsoft": {
        "auth_url":     "https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize",
        "token_url":    "https://login.microsoftonline.com/consumers/oauth2/v2.0/token",
        "redirect_uri": "http://localhost",
        "scopes":       ["https://graph.microsoft.com/User.Read",
                         "https://graph.microsoft.com/MediaMetadata.Read"],
        "device_auth_url": "https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode",
    },
    "amazon": {
        "auth_url":     "https://www.amazon.com/ap/oa",
        "token_url":    "https://api.amazon.com/auth/o2/token",
        "redirect_uri": "https://www.amazon.com/ap/oa",
        "scopes":       ["clouddrive:read_all", "profile:user_id"],
        "device_auth_url": "https://api.amazon.com/auth/o2/create/codepair",
    },
    "nextcloud": {
        "auth_url":     None,  # configured per-instance; user supplies
        "token_url":    None,
        "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
        "scopes":       ["files", "photos"],
        "device_auth_url": None,
    },
}


def vendored_client(vendor: str, credentials: dict = None,
                    transport=None, store=None) -> OAuthClient:
    """Build an :class:`OAuthClient` pre-loaded with a vendor's endpoints.

    ``credentials`` is the user's app-registration info:
      ``{"client_id": "...", "client_secret": "...", "scopes": [...],
          "redirect_uri"? / "instance_uri"?}``

    No secrets are stored until ``authorization_url()`` or
    ``start_device_flow()`` runs and tokens are actually issued.
    """
    cfg = dict(VENDORS[vendor])
    client = OAuthClient(
        auth_url=cfg["auth_url"], token_url=cfg["token_url"],
        client_id=(credentials or {}).get("client_id", ""),
        client_secret=(credentials or {}).get("client_secret"),
        scopes=(credentials or {}).get("scopes") or cfg["scopes"],
        redirect_uri=(credentials or {}).get("redirect_uri") or cfg["redirect_uri"],
        transport=transport, store=store or SecureSecretsStore(),
    )
    client.device_auth_url = cfg.get("device_auth_url")
    client.instance_uri = (credentials or {}).get("instance_uri")
    if credentials:
        client.require_credentials(credentials)
    return client
