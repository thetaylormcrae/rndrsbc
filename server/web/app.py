"""rndrSBC - Flask application factory for the web dashboard."""
from __future__ import annotations

import logging
import os
import time

from flask import Flask, request, jsonify, send_from_directory
from flask import session as _flask_session

from . import security
from .routes import bp

logger = logging.getLogger("rndrSBC.web.app")

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TEMPLATES = os.path.join(_ROOT, "server", "templates")
ASSETS = os.path.join(_ROOT, "assets")


def create_app(scheduler=None, secret_key: str | None = None) -> Flask:
    app = Flask("rndrsbc",
                template_folder=TEMPLATES,
                static_folder=None)  # assets served explicitly, with traversal guard

    # Secret key: env-provided or a per-boot ephemeral key (sessions are
    # cookie-signed only; restarting invalidates sessions, which is safe and
    # expected for a dashboard of this size).
    app.config.update(
        SECRET_KEY=secret_key or os.environ.get("RNDRSBC_SECRET_KEY") or os.urandom(32),
        SESSION_COOKIE_NAME="rndrsbc_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=security.SESSION_IDLE_SECS,
        MAX_CONTENT_LENGTH=50 * 1024 * 1024,
        SCHEDULER=scheduler,
    )

    app.register_blueprint(bp)

    # ---- static assets with path-traversal guard ----
    @app.route("/static/<path:rel>")
    @app.route("/assets/<path:rel>")
    def static_assets(rel):
        return send_from_directory(ASSETS, rel, conditional=True)

    # ---- sliding session touch + security headers ----
    @app.after_request
    def _touch_and_headers(resp):
        security.touch_session()
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        return resp

    # ---- 401 JSON for API paths (so fetch() handles it uniformly) ----
    @app.errorhandler(401)
    def _unauthorized(e):
        if request.path.startswith("/api/"):
            return jsonify(error="Authentication required"), 401
        return e

    @app.errorhandler(404)
    def _not_found(e):
        if request.path.startswith("/api/"):
            return jsonify(error="Not found"), 404
        return e

    return app
