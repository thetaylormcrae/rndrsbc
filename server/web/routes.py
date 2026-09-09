"""rndrSBC - Flask route table (1:1 port of the legacy http.server endpoints).

Response shapes are preserved exactly so the existing dashboard JS keeps
working. Mutating endpoints are CSRF-protected; auth/setup endpoints are
CSRF-exempt (they establish the session) and login is rate-limited.
"""
from __future__ import annotations

import io
import json
import logging
import os
import subprocess
import threading
import time

import requests as _http
from flask import (Blueprint, Response, current_app, jsonify, redirect,
                   render_template, request, send_file, send_from_directory,
                   session, url_for)

from core import paths
from core.paths import CONFIG_PATH, resolve
from core.telemetry import TELEMETRY
from server.onboarding import (claim_url_for_token, consume_claim_token,
                               issue_claim_token, onboarding_state)
from . import model, security
from .security import (csrf_exempt, csrf_protect, has_admin_setup, is_authenticated,
                       login_required, rate_limit_login)

logger = logging.getLogger("rndrSBC.web.routes")

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

bp = Blueprint("rndrsbc", __name__)

PAGE_MAP = {
    "/": "dashboard.html",
    "/index.html": "dashboard.html",
    "/playlists": "playlists.html",
    "/widgets": "widgets.html",
    "/settings": "settings.html",
    "/photos": "photos.html",
    "/system": "system.html",
}


# ---------------------------------------------------------------------------
# Pages (shell only; data loads via the API)
# ---------------------------------------------------------------------------

@bp.route("/", defaults={"path": ""})
@bp.route("/<path:path>")
def page(path):
    tpl = PAGE_MAP.get("/" + path.rstrip("/")) if path else PAGE_MAP["/"]
    if tpl is None:
        # serve real asset files (icon fonts etc.); never traverse outside assets
        if os.path.splitext(path)[1] and not path.startswith("api/"):
            resp = _static_asset(path)
            if resp is not None:
                return resp
        return jsonify(error="Not found"), 404
    html = render_template(tpl)
    return Response(_inject_csrf_bootstrap(html), mimetype="text/html")


@bp.route("/favicon.ico")
def favicon():
    # No static icon ships with the package yet; a 204 keeps this route
    # functional without inventing an asset that doesn't exist.
    return "", 204


def _static_asset(rel: str):
    base = os.path.abspath(os.path.join(os.path.dirname(paths.__file__) if hasattr(paths, "__file__") else ".", ".."))
    root = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), )
    assets = os.path.join(root, "assets")
    target = os.path.realpath(os.path.join(assets, rel))
    if not target.startswith(assets + os.sep) or not os.path.isfile(target):
        return None
    return send_file(target)


def _inject_csrf_bootstrap(html: str) -> str:
    """Inject a CSRF bootstrap <script> before </body>.

    Sets window.__CSRF__ and monkey-patches window.fetch so every subsequent
    POST/PUT/DELETE (including the templates' own inline fetches) carries the
    X-CSRF-Token header without editing each template.
    """
    token = security.csrf_token()
    script = (
        '<script>'
        'window.__CSRF__=' + json.dumps(token) + ';'
        '(function(){var f=window.fetch;if(!f)return;'
        'window.fetch=function(u,o){o=o||{};var m=(o.method||"GET").toUpperCase();'
        'if(m!=="GET"){o.headers=o.headers||{};'
        'if(o.headers instanceof Headers){if(!o.headers.has("X-CSRF-Token"))o.headers.set("X-CSRF-Token",window.__CSRF__);}'
        'else if(!o.headers["X-CSRF-Token"])o.headers["X-CSRF-Token"]=window.__CSRF__;}'
        'return f(u,o);};})();'
        '</script>'
    )
    idx = html.rfind("</body>")
    if idx == -1:
        return html
    return html[:idx] + script + html[idx:]


# ---------------------------------------------------------------------------
# Auth (CSRF-exempt: these establish the session and issue the token)
# ---------------------------------------------------------------------------

@bp.route("/api/auth/status")
def auth_status():
    setup_req = not has_admin_setup()
    return jsonify(setup_required=setup_req,
                   authenticated=is_authenticated(),
                   user="admin" if is_authenticated() else None)


@bp.route("/api/setup", methods=["POST"])
@csrf_exempt
def setup():
    if has_admin_setup():
        return jsonify(error="Admin setup has already been completed. Use Settings or CLI 'rndrsbc set-password' to change password."), 400
    pwd = (request.get_json(silent=True) or {}).get("password", "").strip()
    if len(pwd) < 8:
        return jsonify(error="Password must be at least 8 characters long."), 400
    try:
        security.setup_admin(pwd)
    except Exception:
        logger.exception("setup failed")
        return jsonify(error="Setup failed"), 500
    return jsonify(status="ok", csrf_token=security.csrf_token())


@bp.route("/api/auth/login", methods=["POST"])
@csrf_exempt
@rate_limit_login
def login():
    pwd = (request.get_json(silent=True) or {}).get("password", "")
    ok = security.login_session(pwd)
    if not ok:
        return jsonify(error="Invalid credentials"), 401
    return jsonify(status="ok", csrf_token=security.csrf_token())


@bp.route("/api/auth/password", methods=["POST"])
@csrf_protect
def change_password():
    body = request.get_json(silent=True) or {}
    ok, msg = security.change_password(body.get("current_password", ""),
                                       (body.get("new_password") or "").strip())
    if not ok:
        return jsonify(error=msg), 400
    # rotate session + CSRF after a credential change
    session["last_seen"] = time.time()
    return jsonify(status="ok")


@bp.route("/api/auth/logout", methods=["POST"])
@csrf_exempt
def logout():
    security.logout_session()
    return jsonify(status="ok")


@bp.route("/api/auth/bearer", methods=["POST"])
@csrf_protect
@login_required
def new_bearer():
    return jsonify(token=security.issue_bearer_token())


# ---------------------------------------------------------------------------
# Onboarding (pre-auth by design, bounded by claim tokens)
# ---------------------------------------------------------------------------

@bp.route("/api/onboarding/status")
def onboarding_status():
    return jsonify(onboarding_state())


@bp.route("/api/onboarding/claim-url")
def onboarding_claim_url():
    state = onboarding_state()
    token = state.get("token") or issue_claim_token().get("token")
    return jsonify(url=claim_url_for_token(token), token=token)


@bp.route("/api/onboarding/qr.png")
def onboarding_qr():
    try:
        import qrcode
        state = onboarding_state()
        token = state.get("token") or issue_claim_token().get("token")
        img = qrcode.QRCode(box_size=10, border=2)
        img.add_data(claim_url_for_token(token))
        img.make(fit=True)
        buf = io.BytesIO()
        img.make_image(fill_color="black", back_color="white").save(buf, format="PNG")
        return Response(buf.getvalue(), mimetype="image/png",
                        headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
    except Exception:
        logger.exception("QR generation failed")
        return "", 500


@bp.route("/api/onboarding/claim", methods=["POST"])
@csrf_exempt
def onboarding_claim():
    token = (request.get_json(silent=True) or {}).get("token", "")
    if consume_claim_token(token):
        return jsonify(status="claimed")
    return jsonify(error="Invalid, expired, or already-claimed token"), 400


@bp.route("/api/onboarding/ap/start", methods=["POST"])
@csrf_exempt
def ap_start():
    from server.onboarding import AccessPointManager
    mgr = current_app.config.get("AP_MANAGER") or AccessPointManager()
    ok = mgr.start_ap()
    return jsonify(status="started" if ok else "failed"), (200 if ok else 500)


@bp.route("/api/onboarding/ap/stop", methods=["POST"])
@csrf_exempt
def ap_stop():
    from server.onboarding import AccessPointManager
    mgr = current_app.config.get("AP_MANAGER") or AccessPointManager()
    mgr.stop_ap()
    return jsonify(status="stopped")


def _safe_wifi_field(value: str, max_len: int = 63) -> str:
    if len(value) > max_len:
        raise ValueError("field too long")
    if any(ch in value for ch in ('"', "\\", "\n", "\r", "\t", "\x00")):
        raise ValueError("field contains invalid characters")
    return value


@bp.route("/api/onboarding/wifi", methods=["POST"])
@csrf_exempt
def onboarding_wifi():
    body = request.get_json(silent=True) or {}
    ssid = (body.get("ssid") or "").strip()
    password = body.get("password", "") or ""
    try:
        ssid = _safe_wifi_field(ssid)
        password = _safe_wifi_field(password)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    if not ssid:
        return jsonify(error="SSID is required"), 400

    cfg = security.load_config()
    cfg["wifi"] = {"ssid": ssid, "password": password}
    cfg.setdefault("device", {})["name"] = cfg.get("device", {}).get("name", "rndrSBC Node")
    security.save_config(cfg)

    wpa_dir = "/etc/wpa_supplicant"
    if os.path.isdir(wpa_dir):
        try:
            wpa = (f'ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev\n'
                   f'update_config=1\ncountry=US\n'
                   f'network={{\n    ssid="{ssid}"\n    psk="{password}"\n    key_mgmt=WPA-PSK\n}}\n')
            with open(os.path.join(wpa_dir, "wpa_supplicant.conf"), "w") as f:
                f.write(wpa)
        except Exception as e:
            logger.warning("Could not write wpa_supplicant.conf: %s", e)
    return jsonify(status="ok")


# ---------------------------------------------------------------------------
# System / telemetry / updates / photos
# ---------------------------------------------------------------------------

@bp.route("/api/telemetry")
@login_required
def telemetry():
    return jsonify(TELEMETRY.get_status())


@bp.route("/api/update/check")
@login_required
def update_check():
    from rndrsbc import _update
    return jsonify(_update.status(quiet=True))


@bp.route("/api/update/apply", methods=["POST"])
@csrf_protect
@login_required
def update_apply():
    cls = type(current_app)
    if getattr(cls, "_apply_thread_active", False):
        return jsonify(status="in-progress", error="An update is already applying"), 409

    def _do_apply():
        try:
            from rndrsbc import _update
            rc = _update.apply()
            result = {"success": rc == 0, "error": None if rc == 0 else "pip upgrade exited %d" % rc}
        except Exception as exc:
            result = {"success": False, "error": str(exc)}
        cls._apply_result = result
        cls._apply_thread_active = False
        cls._apply_finished_ts = time.time()

    cls._apply_result = None
    cls._apply_thread_active = True
    threading.Thread(target=_do_apply, daemon=True).start()
    return jsonify(status="started"), 202


@bp.route("/api/update/apply-status")
@login_required
def update_apply_status():
    cls = type(current_app)
    res = getattr(cls, "_apply_result", None)
    active = getattr(cls, "_apply_thread_active", False)
    if res is not None:
        return jsonify(status="finished", success=res.get("success"), error=res.get("error"))
    if active:
        return jsonify(status="in-progress")
    return jsonify(status="idle")


@bp.route("/api/photos")
@login_required
def photos_list():
    try:
        from widgets.photo_frame.widget import list_photos
        return jsonify(photos=list_photos())
    except Exception as e:
        logger.exception("list_photos failed")
        return jsonify(photos=[], error=str(e))


@bp.route("/api/photos/file")
@login_required
def photos_file():
    from widgets.photo_frame.widget import PHOTO_DIR
    target = (request.args.get("path") or "").strip()
    base = os.path.realpath(PHOTO_DIR)
    cand = os.path.realpath(target)
    if not cand.startswith(base + os.sep) or not os.path.isfile(cand):
        return jsonify(error="Photo not found"), 404
    ext = cand.lower().rsplit(".", 1)[-1]
    ctype = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}.get(ext, "application/octet-stream")
    return send_file(cand, mimetype=ctype)


@bp.route("/api/photos/delete", methods=["POST"])
@csrf_protect
@login_required
def photos_delete():
    from widgets.photo_frame.widget import PHOTO_DIR
    target = ((request.get_json(silent=True) or {}).get("path") or "").strip()
    base = os.path.realpath(PHOTO_DIR)
    cand = os.path.realpath(target)
    if not cand.startswith(base + os.sep):
        return jsonify(error="Refusing to delete outside photo library"), 400
    if os.path.isfile(cand):
        os.remove(cand)
        return jsonify(message=f"Deleted {os.path.basename(cand)}")
    return jsonify(message=f"Not found: {target}")


@bp.route("/api/photos/upload", methods=["POST"])
@csrf_protect
@login_required
def photos_upload():
    f = request.files.get("file")
    if f is None:
        return jsonify(error="No file uploaded"), 400
    from widgets.photo_frame.widget import save_photo
    try:
        path = save_photo(f.read(), f.filename or "photo.jpg")
    except Exception as e:
        return jsonify(error=f"Upload failed: {e}"), 400
    sched = current_app.config.get("SCHEDULER")
    if sched is not None:
        try:
            sched.refresh_display()
        except Exception:
            logger.exception("post-upload repaint failed")
    return jsonify(status="uploaded", path=path)


# ---------------------------------------------------------------------------
# Config, geocode, dev studio, live mirror
# ---------------------------------------------------------------------------

@bp.route("/api/config")
@login_required
def config_get():
    return jsonify(model.sanitize_outbound(security.load_config()))


@bp.route("/api/config", methods=["POST"])
@csrf_protect
@login_required
def config_post():
    partial = request.get_json(silent=True)
    if partial is None:
        return jsonify(error="Invalid JSON"), 400
    try:
        merged, warnings = model.update_config(partial, scheduler=current_app.config.get("SCHEDULER"))
    except model.ConfigUpdateError as e:
        return jsonify(error=str(e)), 400
    return jsonify(status="updated", warnings=warnings, config=model.sanitize_outbound(merged))


@bp.route("/api/geocode")
@login_required
def geocode():
    query = (request.args.get("q") or "").strip()
    if not query:
        return jsonify(error="query required"), 400
    try:
        r = _http.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": query, "count": 8, "language": "en", "format": "json"},
            timeout=8)
        r.raise_for_status()
        results = r.json().get("results", [])
    except Exception:
        results = []
    return jsonify([{"label": f"{x.get('name','')} · {x.get('admin1','')}, {x.get('country','')}".strip(" .,"),
                     "name": x.get("name", ""),
                     "latitude": x.get("latitude"),
                     "longitude": x.get("longitude")} for x in results])


@bp.route("/api/dev-studio/render")
@login_required
def devstudio_render():
    from server.dev_studio import render_widget_image

    def _clamp(name, default):
        try:
            return max(16, min(1600, int(request.args.get(name, default))))
        except (TypeError, ValueError):
            return default

    w, h = _clamp("w", 800), _clamp("h", 480)
    settings = {}
    for k, v in request.args.items():
        if k not in ("w", "h", "widget", "color", "dither", "t", "settings"):
            settings[k] = v
    blob = request.args.get("settings")
    if blob:
        try:
            b = json.loads(blob)
            if isinstance(b, dict):
                settings.update(b)
        except (ValueError, TypeError):
            pass
    data, err = render_widget_image(request.args.get("widget", ""), w, h,
                                    color_mode=request.args.get("color", "7color"),
                                    use_dither=request.args.get("dither", "0") == "1",
                                    settings=settings)
    if data is not None:
        return Response(data, mimetype="image/png", headers={"Cache-Control": "no-cache"})
    return jsonify(error=err), (400 if err and "Unknown widget" in err else 500)


@bp.route("/api/dev-studio/widgets")
@login_required
def devstudio_widgets():
    from server.dev_studio import WIDGETS
    out = [{"name": name, "schema": (getattr(w, "get_config_schema", lambda: [])() or [])}
           for name, w in sorted(WIDGETS.items())]
    return jsonify(widgets=out)


@bp.route("/api/screen.png")
@login_required
def screen_png():
    sched = current_app.config.get("SCHEDULER")
    img = None
    if sched and getattr(sched, "last_preview_image", None):
        img = sched.last_preview_image
    elif sched and getattr(sched, "last_rendered_image", None):
        img = sched.last_rendered_image
    elif os.path.exists(resolve("live_screen.png")):
        from PIL import Image
        img = Image.open(resolve("live_screen.png"))
    elif os.path.exists(resolve("live_weather_full.png")):
        from PIL import Image
        img = Image.open(resolve("live_weather_full.png"))
    else:
        from PIL import Image
        img = Image.new("RGB", (800, 480), "white")
    if img.mode != "RGB":
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(buf.getvalue(), mimetype="image/png",
                    headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


@bp.route("/api/panel/refresh", methods=["POST"])
@csrf_protect
@login_required
def panel_refresh():
    sched = current_app.config.get("SCHEDULER")
    if sched:
        try:
            sched.trigger_render_now(0, force_hardware=True)
        except Exception as e:
            return jsonify(error=str(e)), 500
    return jsonify(status="ok")


@bp.route("/api/panel/clean", methods=["POST"])
@csrf_protect
@login_required
def panel_clean():
    try:
        from core.panel_health import get_health
        get_health().record_full_refresh()
        sched = current_app.config.get("SCHEDULER")
        if sched:
            sched.trigger_render_now(0, force_hardware=True)
        return jsonify(status="clean_cycle_scheduled")
    except Exception as e:
        return jsonify(error=str(e)), 500


@bp.route("/api/system/restart", methods=["POST"])
@csrf_protect
@login_required
def system_restart():
    subprocess.run(["systemctl", "restart", "rndrsbc"], check=False)
    return jsonify(status="restarting")


@bp.route("/api/system/reboot", methods=["POST"])
@csrf_protect
@login_required
def system_reboot():
    subprocess.run(["systemctl", "reboot"], check=False)
    return jsonify(status="rebooting")


@bp.route("/api/system/shutdown", methods=["POST"])
@csrf_protect
@login_required
def system_shutdown():
    subprocess.run(["systemctl", "poweroff"], check=False)
    return jsonify(status="powering_off")
