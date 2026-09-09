"""rndrSBC - Session, auth, CSRF, and rate-limiting primitives.

Replaces the hand-rolled cookie/token session store with Flask's signed
session cookie (itsdangerous under the hood) plus:

  * Sliding 1-hour inactivity expiry (PERMANENT_SESSION_LIFETIME refreshed
    on each request via an after_request touch).
  * CSRF token issued on login/setup and verified on every mutating request.
  * Login rate limiting: 5 failures then a 15-minute lockout.
  * Bearer-token support preserved for programmatic/CLI callers.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import secrets
import threading
import time
from functools import wraps

from flask import current_app, request, session, jsonify
from werkzeug.security import check_password_hash, generate_password_hash

from core.paths import CONFIG_PATH

logger = logging.getLogger("rndrSBC.web.security")

SESSION_COOKIE = "rndrsbc_session"
SESSION_IDLE_SECS = 3600          # sliding inactivity window (was requested)
CSRF_KEY = "_csrf_token"
BEARER_TOKENS_FILE = ".bearer_tokens"  # json map: token -> {"last_seen": float}

# In-process login throttling: {ip: {"fails": int, "locked_until": float}}
_LOGIN_STATE: dict[str, dict] = {}
_LOGIN_LOCK = threading.Lock()
LOGIN_MAX_FAILS = 5
LOGIN_LOCKOUT_SECS = 900

# ---------------------------------------------------------------------------
# Config helpers (single writer; mirrors the old hand-rolled load/save)
# ---------------------------------------------------------------------------

def load_config(path: str | None = None) -> dict:
    p = path or CONFIG_PATH
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            logger.exception("config.json unreadable")
    return {}


def save_config(cfg: dict, path: str | None = None) -> None:
    p = path or CONFIG_PATH
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, p)


def has_admin_setup() -> bool:
    return bool(load_config().get("admin_password_hash"))


# ---------------------------------------------------------------------------
# Bearer tokens (CLI / programmatic access, no cookie jar available)
# ---------------------------------------------------------------------------

def _bearer_file() -> str:
    from core.paths import DATA_DIR
    return os.path.join(DATA_DIR, BEARER_TOKENS_FILE)


def bearer_tokens() -> dict:
    try:
        with open(_bearer_file()) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def issue_bearer_token() -> str:
    """Create a long-lived bearer token for headless callers."""
    token = "rndr_" + secrets.token_hex(24)
    toks = bearer_tokens()
    toks[token] = {"last_seen": time.time()}
    with open(_bearer_file(), "w") as f:
        json.dump(toks, f, indent=2)
    return token


def check_bearer_token(token: str) -> bool:
    toks = bearer_tokens()
    meta = toks.get(token)
    if not meta:
        return False
    # Bearer tokens do NOT expire on inactivity (they are long-lived CLI creds).
    toks[token]["last_seen"] = time.time()
    try:
        with open(_bearer_file(), "w") as f:
            json.dump(toks, f, indent=2)
    except OSError:
        pass
    return True


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def is_authenticated() -> bool:
    """Cookie session OR valid bearer token."""
    auth_h = request.headers.get("Authorization", "")
    if auth_h.startswith("Bearer "):
        return check_bearer_token(auth_h[7:].strip())
    sess = session.get("user")
    last = session.get("last_seen", 0)
    return bool(sess) and (time.time() - last) < SESSION_IDLE_SECS


def login_session(password: str, config_path: str | None = None) -> bool:
    """Verify password and, on success, populate the Flask signed session."""
    cfg = load_config(config_path)
    stored = cfg.get("admin_password_hash", "")
    if not stored or not check_password_hash(stored, password):
        return False
    session.clear()
    session["user"] = "admin"
    session["last_seen"] = time.time()
    session[CSRF_KEY] = secrets.token_urlsafe(32)
    session.permanent = True
    return True


def setup_admin(password: str, config_path: str | None = None) -> None:
    """First-run setup: store the password hash, then log the user in."""
    cfg = load_config(config_path)
    cfg["admin_password_hash"] = generate_password_hash(password, method="pbkdf2:sha256")
    save_config(cfg, config_path)
    login_session(password, config_path)


def change_password(current_pwd: str, new_pwd: str, config_path: str | None = None) -> tuple[bool, str]:
    cfg = load_config(config_path)
    stored = cfg.get("admin_password_hash", "")
    if not stored or not check_password_hash(stored, current_pwd):
        return False, "Current password is incorrect"
    if len(new_pwd) < 8:
        return False, "New password must be at least 8 characters long"
    cfg["admin_password_hash"] = generate_password_hash(new_pwd, method="pbkdf2:sha256")
    save_config(cfg, config_path)
    return True, "ok"


def logout_session() -> None:
    session.clear()


def touch_session() -> None:
    """Sliding expiry: refresh last_seen on every authenticated request."""
    if session.get("user"):
        now = time.time()
        if now - float(session.get("last_seen", 0)) > 30:
            session["last_seen"] = now
            session.permanent = True


def csrf_token() -> str:
    """Get or create this session's CSRF token (issue lazily)."""
    tok = session.get(CSRF_KEY)
    if not tok:
        tok = secrets.token_urlsafe(32)
        session[CSRF_KEY] = tok
    return tok


def csrf_exempt(route):
    route._csrf_exempt = True
    return route


def verify_csrf() -> bool:
    sent = request.headers.get("X-CSRF-Token", "")
    tok = session.get(CSRF_KEY, "")
    return bool(tok) and hmac.compare_digest(sent, tok)


# ---------------------------------------------------------------------------
# Decorators
# ---------------------------------------------------------------------------

def login_required(fn):
    """401 unless the session/bearer is valid (after setup). Pre-setup routes
    bypass this so the local onboarding flow can still configure the device."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if has_admin_setup() and not is_authenticated():
            return jsonify(error="Authentication required"), 401
        return fn(*args, **kwargs)
    return wrapper


def csrf_protect(fn):
    """Verify CSRF on mutating requests; exempted routes skip the check."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if request.method in ("POST", "PUT", "DELETE", "PATCH"):
            if getattr(fn, "_csrf_exempt", False):
                return fn(*args, **kwargs)
            if not verify_csrf():
                return jsonify(error="CSRF token missing or invalid"), 403
        return fn(*args, **kwargs)
    return wrapper


def rate_limit_login(fn):
    """Track failures per-IP and lock out after LOGIN_MAX_FAILS."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        ip = request.remote_addr or "unknown"
        with _LOGIN_LOCK:
            st = _LOGIN_STATE.setdefault(ip, {"fails": 0, "locked_until": 0.0})
            now = time.time()
            if now < st["locked_until"]:
                remaining = int(st["locked_until"] - now)
                return jsonify(error=f"Too many failed attempts. Locked for {remaining}s."), 429
        resp = fn(*args, **kwargs)
        status = resp[1] if isinstance(resp, tuple) else resp.status_code
        if status == 401:  # failed attempt
            with _LOGIN_LOCK:
                st["fails"] += 1
                if st["fails"] >= LOGIN_MAX_FAILS:
                    st["locked_until"] = time.time() + LOGIN_LOCKOUT_SECS
                    st["fails"] = 0
                    logger.warning("Login lockout for %s", ip)
        else:
            with _LOGIN_LOCK:
                _LOGIN_STATE.pop(ip, None)
        return resp
    return wrapper
