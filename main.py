"""
rndrSBC - Native E-Paper Operating Platform
Main entry point for daemon, background multi-playlist scheduler, secure web dashboard, and developer studio.

Packaging notes:
  - No install.sh required. Copy the folder onto the Pi and run
    ``python3 main.py``. The entrypoint self-bootstraps vendored/venv deps
    and anchors every on-disk path to the package location.
  - ``python3 main.py [port]``          run web dashboard + scheduler
  - ``python3 main.py --daemon [port]`` detach and run in the background
  - ``python3 main.py --once``          single render pass, then exit
  - ``python3 main.py dev [port]``      developer studio (live preview)
"""

import sys
import os
import json
import time
import gc
import logging

# Make deps importable regardless of how the entrypoint is invoked (must run
# before any third-party import below). Anchors the package to its own root.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.paths import bootstrap_deps, resolve, CONFIG_PATH, CLAIM_TOKEN_PATH, ensure_data_dir, create_venv, ROOT
bootstrap_deps()

# Ensure the relocatable data dir exists so photo uploads / config writes work
# no matter where the process was started from.
ensure_data_dir()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("rndrSBC")

from displays.virtual import VirtualDisplay
from widgets.base import discover_widgets, WIDGET_REGISTRY
from server.scheduler import Scheduler


def load_config(path=None):
    path = path or CONFIG_PATH
    if os.path.exists(path):
        with open(path, "r") as f:
            raw = json.load(f)
        # Zero-downtime schema upgrades: step forward, persist, return.
        from core.migrations import migrate
        migrated = migrate(raw)
        if migrated is not raw and migrated.get("schema_version") != raw.get("schema_version"):
            try:
                with open(path, "w") as f:
                    json.dump(migrated, f, indent=2)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"could not persist migrated config: {e}")
        return migrated
    return {
        "device": {"name": "Raspberry Pi Zero 2W", "timezone": "America/New_York"},
        "display": {"driver": "auto", "model": "impression_7_3", "orientation": 0},
        "quiet_hours": {"enabled": False, "start": "23:00", "end": "06:00", "mode": "suspend"},
        "active_playlist": "main",
        "playlists": {
            "main": {
                "name": "Main Rotation",
                "items": [
                    {"widget": "onboarding", "duration_minutes": 5, "settings": {"title": "Let's set up your display"}},
                    {"widget": "weather", "duration_minutes": 15, "settings": {"location": "New York City", "latitude": 40.7128, "longitude": -74.0060, "units": "imperial", "frame": "Corner"}},
                    {"widget": "calendar", "duration_minutes": 30, "settings": {"title": "My Schedule", "first_day_sunday": True, "frame": "Corner"}}
                ]
            }
        }
    }

def print_banner():
    print("""
  ╔═════════════════════════════════════════════════════════╗
  ║                      rndrSBC                            ║
  ║  High-Performance Native E-Paper Rendering Platform     ║
  ║       Fast • Ultra-Low Memory • Chromium-Free           ║
  ╚═════════════════════════════════════════════════════════╝
    """)

def _daemonize():
    """Double-fork to detach from the controlling terminal (POSIX).

    On Windows this is a no-op; run in the foreground instead. Pidfile is
    written to <root>/rndrSBC.pid so the process can be managed without sudo.
    """
    if os.name != "posix":
        logger.info("Daemon mode unavailable on this platform; running in foreground.")
        return
    pidfile = resolve("rndrSBC.pid")
    try:
        pid = os.fork()
        if pid > 0:
            os._exit(0)              # parent exits
        os.setsid()                 # new session
        pid = os.fork()
        if pid > 0:
            os._exit(0)             # session leader exits
        os.chdir(ROOT)              # stay anchored to the package root
        for f in (sys.stdin, sys.stdout, sys.stderr):
            try:
                f.flush()
            except Exception:
                pass
        si = open(os.devnull, "r")
        so = open(os.devnull, "a+")
        se = open(os.devnull, "a+")
        os.dup2(si.fileno(), sys.stdin.fileno())
        os.dup2(so.fileno(), sys.stdout.fileno())
        os.dup2(se.fileno(), sys.stderr.fileno())
        with open(pidfile, "w") as f:
            f.write(str(os.getpid()))
        logger.info("Detached to background (pid %s), pidfile: %s", os.getpid(), pidfile)
    except Exception as e:
        logger.warning("Daemonize failed (%s); running in foreground.", e)


def main():
    print_banner()

    # 0. Auto-discover all installed widget plugins
    discover_widgets()
    logger.info(f"Loaded {len(WIDGET_REGISTRY)} active widgets: {list(WIDGET_REGISTRY.keys())}")

    # 1. Dev Studio Command: python main.py dev [port]
    if len(sys.argv) > 1 and sys.argv[1] == "dev":
        from server.dev_studio import run_dev_studio
        port = int(sys.argv[2]) if len(sys.argv) > 2 else 8080
        run_dev_studio(port=port)
        return

    # 2. Production Daemon + Web Dashboard
    config = load_config()
    disp_cfg = config.get("display", {})
    driver_name = disp_cfg.get("driver", "auto")
    orientation = disp_cfg.get("orientation", disp_cfg.get("rotation", 0))
    saturation = float(disp_cfg.get("saturation", 0.5))
    inky_kw = dict(
        orientation=orientation,
        saturation=saturation,
    )

    if driver_name == "auto":
        # Auto-detect a physical panel; fall back to a virtual display otherwise.
        from displays.inky import InkyDisplay
        detected = InkyDisplay.detect()
        if detected is not None:
            logger.info(f"[display] Auto-detected Inky panel: {detected}")
            display = InkyDisplay(model=detected, **inky_kw)
        else:
            # Also try configured Inky model fallback in case I2C EEPROM auto-detection was unavailable
            fallback_model = disp_cfg.get("model", "impression_7_3")
            try_display = InkyDisplay(model=fallback_model, **inky_kw)
            if try_display._inky is not None:
                logger.info(f"[display] Connected to Inky panel via model fallback: {fallback_model}")
                display = try_display
            else:
                logger.warning("[display] No physical e-paper detected; falling back to virtual display.")
                from displays.virtual import VirtualDisplay
                display = VirtualDisplay(
                    width=disp_cfg.get("width", 800),
                    height=disp_cfg.get("height", 480),
                    output_path=resolve("live_screen.png"),
                )
    elif driver_name == "waveshare":
        from displays.waveshare import WaveshareDisplay
        display = WaveshareDisplay(model=disp_cfg.get("model", "epd7in3f"), orientation=orientation)
    elif driver_name == "inky":
        from displays.inky import InkyDisplay
        display = InkyDisplay(model=disp_cfg.get("model", "impression_7_3"), **inky_kw)
    elif driver_name == "framebuffer":
        from displays.framebuffer import FramebufferDisplay
        display = FramebufferDisplay(orientation=orientation)
    else:
        display = VirtualDisplay(
            width=disp_cfg.get("width", 800),
            height=disp_cfg.get("height", 480),
            output_path=resolve("live_screen.png")  # relocatable preview
        )

    # Propagate runtime refresh mode ('auto' default, 'full' opt-out) to driver.
    if hasattr(display, "set_refresh_mode"):
        display.set_refresh_mode(config.get("refresh_mode", "auto"))

    scheduler = Scheduler(display, config, WIDGET_REGISTRY)

    # Self-bootstrap a private venv on first run if no vendored wheels are
    # present and the third-party deps aren't already importable.
    try:
        import requests, PIL, werkzeug  # noqa: F401
    except ImportError:
        create_venv()
        bootstrap_deps()
        import requests, PIL, werkzeug  # noqa: F401

    # Physical GPIO buttons (next/prev/toggle) for playlist cycling.
    buttons = None
    try:
        from core.buttons import ButtonController
        buttons = ButtonController(scheduler, config)
        buttons.start()
    except Exception as e:
        logger.warning(f"Physical button controller not started: {e}")

    # Single pass CLI test
    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        logger.info("Executing single render pass...")
        scheduler.trigger_render_now(0, force_hardware=True)
        gc.collect()
        logger.info("Single-pass complete.")
        return

    # Daemon mode: detach from the controlling terminal and keep running.
    if len(sys.argv) > 1 and sys.argv[1] == "--daemon":
        _daemonize()
        port = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 8080
    else:
        port = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 8080

    # Execute initial render pass
    scheduler.trigger_render_now(0)
    gc.collect()

    # AP fallback / recovery-mode watchdog: if Wi-Fi is missing or drops,
    # the device brings up its own hotspot so it can be re-provisioned.
    try:
        from server.onboarding import ap_manager
        ap_manager._config = config
        if not ap_manager._wifi_creds_present():
            ap_manager.start_ap()  # First boot with no Wi-Fi -> immediate setup AP
        ap_manager.start_network_watchdog(check_interval=30)
    except Exception as e:
        logger.warning(f"AP fallback watchdog not started: {e}")

    # Start Background Refresh Scheduler
    logger.info("Starting background display scheduler...")
    scheduler.start()

    # Start Production Web Control Panel
    try:
        from server.app import run_production_server  # lazy: not needed for --once
        run_production_server(scheduler, port=port)
    except KeyboardInterrupt:
        logger.info("Shutting down rndrSBC...")
        scheduler.stop()
        if buttons:
            buttons.stop()

if __name__ == "__main__":
    main()
