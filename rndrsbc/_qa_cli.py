"""``rndrsbc calibrate`` / ``rndrsbc snapshot`` — hardware QA entrypoints.

``calibrate``     render the reference 7-colour pattern, verify primaries,
                  push to the panel, and save a PNG under /rool-drive.
``snapshot``      render the *current* configured frame (active playlist first
                  item) to a /rool-drive PNG WITHOUT touching the panel — a
                  remote-verifiable "what the software intends" artifact.

Both reuse core.qa.build_display() so they exercise the exact same config →
display wiring as the live daemon.
"""

from __future__ import annotations

import json
import os
import sys

from core import qa
from core.calibrate import make_colour_pattern, run_calibration


def _drive_path(name: str) -> str:
    if os.path.isdir("/rool-drive"):
        return os.path.join("/rool-drive", name)
    base = os.environ.get("RNDRSBC_HOME", os.path.expanduser("~"))
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, name)


def _snapshot_path(arg) -> str:
    if arg:
        return arg
    return _drive_path("screen.png")


def cmd_calibrate(argv):
    """rndrsbc calibrate [--json] [--out /path/to.png]"""
    as_json = "--json" in argv
    argv = [a for a in argv if a != "--json"]
    out = None
    if "--out" in argv:
        i = argv.index("--out")
        if i + 1 < len(argv):
            out = argv.pop(i + 1)
        argv.pop(i)

    cfg = qa.load_qa_config()
    disp = qa.build_display(cfg)  # ValueError/LookupError propagate to user
    brief = qa.display_brief(disp)

    out = out or _drive_path("calibration.png")
    report = run_calibration(disp, out_path=out)
    report["brief"] = brief

    if as_json:
        print(json.dumps(report, indent=2))
    else:
        print(f"rndrSBC calibrate → {brief}")
        print(f"  panel update: {report['verdict']}")
        blocks = report["pre_verify"]
        for name in ("black", "white", "yellow", "red", "blue", "green"):
            b = blocks.get(name)
            if not b:
                continue
            tag = "OK " if b["ok"] else "FAIL"
            print(f"  {name:8} {tag} dom={b['dominant']} cov={b.get('coverage')}")
        print(f"  snapshot: {report.get('snapshot')}")
    return 0 if report["verdict"] == "OK" else 2


def cmd_snapshot(argv):
    """rndrsbc snapshot [--json] [--out /path/to.png]"""
    as_json = "--json" in argv
    argv = [a for a in argv if a != "--json"]
    out = None
    if "--out" in argv:
        i = argv.index("--out")
        if i + 1 < len(argv):
            out = argv.pop(i + 1)
        argv.pop(i)

    cfg = qa.load_qa_config()
    disp = qa.build_display(cfg)
    brief = qa.display_brief(disp)

    # Render the current configured frame: active playlist first item.
    # Reuse the scheduler if present, else render the colour pattern as a
    # deterministic capture. Prefer the scheduler's real frame when run on a Pi.
    frame = None
    try:
        from server.scheduler import Scheduler
        from widgets.base import WIDGET_REGISTRY
        sched = Scheduler(disp, cfg, WIDGET_REGISTRY)
        frame = sched.render_preview(0)
    except Exception as exc:  # noqa: BLE001
        print(f"  (scheduler frame unavailable: {type(exc).__name__}; using colour pattern)", file=sys.stderr)

    if frame is None:
        w, h = disp.get_resolution()
        frame = make_colour_pattern(w, h)

    out = out or _drive_path("screen.png")
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    frame.save(out)

    report = {"driver": brief, "snapshot": out, "mode": frame.mode,
              "size": list(frame.size)}
    if as_json:
        print(json.dumps(report, indent=2))
    else:
        print(f"rndrSBC snapshot → {brief}")
        print(f"  saved: {out}  ({report['mode']} {report['size'][0]}x{report['size'][1]})")
        print("  (does not touch the panel; remotely verifiable artifact)")
    return 0


def main(argv):
    """Dispatch for the QA subcommands."""
    if not argv:
        print("usage: rndrsbc calibrate|snapshot|panel-spec [--json] [--out PATH]")
        return 1
    cmd = argv[0]
    rest = argv[1:]
    if cmd == "calibrate":
        return cmd_calibrate(rest)
    if cmd == "snapshot":
        return cmd_snapshot(rest)
    if cmd in ("panel-spec", "panelspec"):
        from rndrsbc import _panel_spec
        return _panel_spec.main(rest)
    if cmd in ("--help", "-h", "help"):
        print("rndrSBC QA commands:\n  calibrate      push reference colour pattern + verify primaries\n"
              "  snapshot       render current frame to /rool-drive/screen.png\n"
              "  panel-spec     identify attached Inky panel + native palette\n")
        return 0
    print(f"unknown QA command: {cmd}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
