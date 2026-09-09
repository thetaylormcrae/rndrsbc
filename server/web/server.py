"""rndrSBC - Production server entry: waitress + Flask app.

Replaces run_production_server() in server/app.py. Called from main.py; the
legacy module is kept temporarily for compatibility and will be removed once
the templates are verified against the new backend.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("rndrSBC.web.server")


def run_flask_server(scheduler=None, port: int = 80):
    from .app import create_app
    app = create_app(scheduler=scheduler)
    logger.info("Production Web Dashboard (Flask/waitress) active at: http://localhost:%s", port)
    actual_port = port
    try:
        from waitress import serve
        serve(app, host="0.0.0.0", port=actual_port, threads=8, clear_untrusted_proxy_headers=True)
    except PermissionError:
        actual_port = 8080
        from waitress import serve
        serve(app, host="0.0.0.0", port=actual_port, threads=8, clear_untrusted_proxy_headers=True)
    except Exception:
        logger.exception("Flask server failed on port %s; retrying on 8080", port)
        from waitress import serve
        serve(app, host="0.0.0.0", port=8080, threads=8, clear_untrusted_proxy_headers=True)
