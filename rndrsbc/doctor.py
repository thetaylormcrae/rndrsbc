"""``rndrsbc doctor`` — self-diagnostic for a frame.

Runs a battery of environment, dependency, config, and widget checks and
prints a verdict table. Safe to run on any host (a non-Pi returns useful
"simulation mode" info rather than failing). Non-zero exit if anything is
fatal; ``--json`` emits machine-readable results for scripting.
"""
from __future__ import annotations

import importlib
import json
import os
import platform
import shutil
import sys
import time

# Delay imports of the package so `doctor` can report *why* a module is missing
# instead of crashing before the report is printed.
SEVERITIES = ("ok", "warn", "fail", "skip")


def _pid_ok(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _import_obj(name: str):
    try:
        mod = importlib.import_module(name)
        return mod, None
    except Exception as exc:  # noqa: BLE001 - report the underlying cause
        return None, exc


def check_system():
    uname = platform.uname()
    is_pi = "arm" in uname.machine or uname.machine in ("aarch64",)
    checks = [
        ("os-name", os.uname().sysname, "ok"),
        ("python", platform.python_version(), "ok" if sys.version_info >= (3, 10) else "fail"),
        ("arch", uname.machine, "ok"),
        ("platform", uname.system, "ok"),
    ]
    if is_pi:
        checks.append(("hardware", f"{uname.system}/{uname.machine} (likely Raspberry Pi)", "ok"))
    else:
        checks.append(("hardware", f"{uname.system}/{uname.machine} — no Pi GPIO, will use simulator", "warn"))
    return checks


def check_dependencies():
    """Import the hard deps from the runtime environment (not the wheel dir)."""
    required = {
        "PIL": "pillow",
        "requests": "requests",
        "werkzeug": "werkzeug",
        "qrcode": "qrcode",
    }
    out = []
    for module, dist in required.items():
        mod, err = _import_obj(module)
        if mod is not None:
            out.append((f"dep:{dist}", f"import ok", "ok"))
        else:
            out.append((f"dep:{dist}", f"missing/invalid ({type(err).__name__}) — pip install {dist}", "fail"))
    return out


def check_config():
    """Validate the canonical CONFIG_PATH resolves and parses."""
    from core import paths
    import json
    cfg_path = paths.CONFIG_PATH
    checks = [("config.exists", "found" if os.path.exists(cfg_path) else "missing",
               "ok" if os.path.exists(cfg_path) else "fail")]
    cfg = {}
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path) as fh:
                cfg = json.load(fh)
            checks.append(("config.valid-json", "parses ok", "ok"))
        except json.JSONDecodeError as exc:
            checks.append(("config.valid-json", f"parse error: {exc}", "fail"))
    else:
        checks.append(("config.valid-json", "n/a (no file)", "skip"))
    # Fail-fast schema validation (provable defects only; unknowns are warnings)
    if isinstance(cfg, dict) and cfg:
        from core.config_schema import validate_config, ConfigError
        try:
            _cfg, warns = validate_config(cfg)
            if warns:
                checks.append(("config.schema", f"ok with {len(warns)} non-fatal warnings; "
                               f"e.g. {warns[0]}", "warn"))
            else:
                checks.append(("config.schema", "valid", "ok"))
        except ConfigError as exc:
            checks.append(("config.schema", f"REJECTED: {exc}", "fail"))
    # display / device presence
    display = cfg.get("display") or {}
    checks.append(("display.driver", str(display.get("driver", "<unset>")) or "<unset>",
                   "ok" if display.get("driver") else "warn"))
    # check the chosen driver module resolves
    driver = (display.get("driver") or "").replace("driver_", "")
    mod, err = _import_obj(f"displays.{driver}") if driver else (None, None)
    if driver:
        checks.append((f"display.{driver}", "import ok" if mod else f"no driver module ({type(err).__name__}): {err}",
                       "ok" if mod else "fail"))
    # active playlist sanity
    playlists = cfg.get("playlists") or {}
    active = cfg.get("active_playlist")
    checks.append(("config.playlist", f"active='{active}'",
                   "ok" if (not playlists or active in playlists) else "warn"))
    return checks


def check_widgets():
    from widgets.base import discover_widgets, WIDGET_REGISTRY
    try:
        reg = discover_widgets()
        names = sorted(reg.keys())
        checks = [("widget-tree", f"{len(names)} discovered: {', '.join(names)}", "ok" if names else "warn")]
        # each active widget has a render boundary?
        for name in list(names)[:12]:
            checks.append((f"widget:{name}", "registered", "ok"))
        return checks
    except Exception as exc:  # noqa: BLE001
        return [("widget-load", f"discover_widgets failed: {type(exc).__name__}", "fail")]


def check_display_paths():
    import os
    from core import paths
    rows = [
        ("deploy_home", str(paths.DEPLOY_ROOT), "ok"),
    ]
    # writable check
    home_missing = not os.path.isdir(paths.DEPLOY_ROOT)
    probe = os.path.join(paths.DEPLOY_ROOT, "doctor.probe")
    try:
        with open(probe, "w") as fh:
            fh.write("x")
        os.remove(probe)
        rows.append(("deploy_writable", "yes", "ok"))
    except OSError:
        hint = " — run `bootstrap.sh --home <dir>` (deploy home not writable)" if home_missing else ""
        rows.append(("deploy_writable", f"{hint.strip() or 'read-only!'}", "fail"))
    return rows


def check_pixel_smoke():
    """Optional: instantiate the configured driver and push a test frame.

    Only runs when ``--render`` is passed. Proves the screen actually renders.
    """
    import json
    from core import paths
    if not os.path.exists(paths.CONFIG_PATH):
        return [("render.smoke", "no config.json; skipping", "skip")]
    try:
        with open(paths.CONFIG_PATH) as fh:
            cfg = json.load(fh)
        driver = (cfg.get("display") or {}).get("driver", "virtual")
        mod, err = _import_obj(f"displays.{driver}")
        if not mod:
            return [("render.smoke", f"driver {driver} not importable ({type(err).__name__})", "fail")]
        # find a display class
        cls = getattr(mod, "VirtualDisplay", None) or getattr(mod, driver.title() + "Display", None) \
              or getattr(mod, "Display", None)
        if not cls:
            return [("render.smoke", f"no display class in {driver}", "fail")]
        # construct with only constructor-accepted config keys
        import inspect
        sig = inspect.signature(cls.__init__)
        var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD
                     for p in sig.parameters.values())
        cfg_kw = {k: v for k, v in cfg.get("display", {}).items()
                  if k != "driver" and (k in sig.parameters or var_kw)}
        disp = cls(**cfg_kw)
        disp.init_hardware()
        w, h = disp.get_resolution()
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (w, h), "white")
        ImageDraw.Draw(img).text((20, 20), "rndrsbc doctor", fill="black")
        disp.display_image(img)
        disp.sleep()
        return [("render.smoke", f"{driver} {w}x{h} rendered + sent OK", "ok")]
    except Exception as exc:  # noqa: BLE001
        return [("render.smoke", f"{type(exc).__name__}: {exc}", "fail")]


def run_checks(do_render=False):
    """Run all check groups; return (rows, fatal_count, exit_code)."""
    groups = [
        ("platform", check_system()),
        ("dependencies", check_dependencies()),
        ("config", check_config()),
        ("widgets", check_widgets()),
        ("paths", check_display_paths()),
    ]
    if do_render:
        groups.append(("render", check_pixel_smoke()))
    fatal = sum(1 for _name, rows in groups for _k, _v, s in rows if s == "fail")
    rows = []
    for gname, gchecks in groups:
        for key, val, sev in gchecks:
            rows.append({"group": gname, "check": key, "value": val, "status": sev})
    return rows, fatal


# --- Health-action recommendations -------------------------------------------
# map check-key -> concrete remediation command(s). Matched on the failing
# check key so `doctor --recommend` stays reliable as checks evolve.
RECOMMENDATIONS = {
    "dep:pillow":       "rndrsbc update self\n     (or) python3 -m pip install --upgrade pillow",
    "dep:requests":     "rndrsbc update self\n     (or) python3 -m pip install --upgrade requests",
    "dep:werkzeug":     "rndrsbc update self\n     (or) python3 -m pip install werkzeug",
    "dep:qrcode":       "rndrsbc update self\n     (or) python3 -m pip install qrcode[pil]",
    "config.exists":   "run the deployment bootstrap:\n     bash bootstrap.sh or `cp config.template.json config.json`",
    "config.valid-json": "fix config.json syntax: `python3 -m json.tool config.json`",
    "display.virtual":  "install the configured display driver (wheel extra or apt) then `rndrsbc doctor`",
    "display.driver":   "set display.driver to a supported name in config.json (virtual / inky_impression …)",
    "deploy_writable":  "make the deploy home writable:\n     mkdir -p $RNDRSBC_HOME && chmod -R u+w $RNDRSBC_HOME\n     or set RNDRSBC_HOME to a writable dir",
    "render.smoke":     "display driver rejected a config key or failed to render:\n     check display.* keys in config.json / run `rndrsbc doctor --render` after fixing",
    "widget-load":      "a widget failed to import — run `rndrsbc list` and `rndrsbc remove <id>`",
}


def recommend(rows) -> list[str]:
    """Return actionable remediation strings for each failing/pending check."""
    out = []
    for r in rows:
        if r["status"] not in ("fail", "warn"):
            continue
        key = r["check"]
        if key in RECOMMENDATIONS:
            out.append(f"[{r['group']}/{key}]\n{RECOMMENDATIONS[key]}")
        elif r["status"] == "fail":
            out.append(f"[{r['group']}/{key}] no canned remedy — inspect config.json / logs.")
    return out


def _print_human(rows):
    sev_mark = {"ok": "  OK ", "warn": " WARN", "fail": " FAIL", "skip": " SKIP"}
    print(f"{'GROUP':<12} {'CHECK':<22} {'STATUS':<6} VALUE")
    print("-" * 70)
    for r in rows:
        print(f"{r['group']:<12} {r['check']:<22} {sev_mark.get(r['status'],' ?   '):<6} {r['value']}")
    fatal = sum(1 for r in rows if r["status"] == "fail")
    print("-" * 70)
    print(f"verdict: {fatal} fatal, {sum(1 for r in rows if r['status']=='warn')} warning(s)")
    return fatal


def main(argv=None):
    argv = list(argv) if argv is not None else sys.argv[1:]
    want_json = "--json" in argv
    want_render = "--render" in argv
    want_rec = "--recommend" in argv
    rows, fatal = run_checks(do_render=want_render)
    if want_json:
        print(json.dumps({"checks": rows, "fatal": fatal}, indent=2))
    else:
        fatal = _print_human(rows)
        if want_rec:
            recs = recommend(rows)
            if recs:
                print("\n— actionable fixes —")
                for r in recs:
                    print(r)
            else:
                print("\n— no remediation needed —")
    return 1 if fatal else 0


if __name__ == "__main__":
    sys.exit(main())
