#!/usr/bin/env bash
#
# rndrSBC deployment bootstrap (idempotent).
#
# Deterministically prepares a deploy home + venv so `rndrsbc doctor` and the
# QA subcommands hit the REAL hardware, not a virtual fallback:
#
#   1. Creates $RNDRSBC_HOME (default: this deploy checkout) if needed.
#   2. Writes a valid config.json (hardware-first: driver=auto, model=spectra6)
#      via python, so the panel is probed on I2C rather than defaulting to the
#      virtual display.
#   3. Installs/upgrades rndrsbc from pypi.org ONLY (never piwheels, whose
#      armv7l mirror can shadow a newer release and stall upgrades).
#   4. Prints the resolved version + a note on what display driver it will use.
#
# Usage:
#   bash deploy/bootstrap.sh                # use this repo's deploy root
#   RNDRSBC_HOME=/srv/rndrsbc bash deploy/bootstrap.sh
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${RNDRSBC_VENV:-$REPO_ROOT/.venv}"
RNDRSBC_HOME="${RNDRSBC_HOME:-$REPO_ROOT}"

PY="${VENV_DIR}/bin/python"
PIP="${VENV_DIR}/bin/pip"

step(){ printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

step "venv ($VENV_DIR)"
if [ ! -x "$PIP" ]; then
  python3 -m venv "$VENV_DIR"
fi

step "install/upgrade rndrsbc (pypi.org only, no piwheels shadow)"
# --index-url (no --extra-index-url) makes pypi.org the ONLY index, and
# PIP_CONFIG_FILE=/dev/null neutralises any ambient /etc/pip.conf on the Pi
# (piwheels extra-index, install.user=true, etc.) for a deterministic install.
PIP_CONFIG_FILE=/dev/null "$PIP" install --upgrade --no-cache-dir \
  --index-url https://pypi.org/simple \
  rndrsbc

step "write hardware config -> $RNDRSBC_HOME/config.json"
"$PY" - "$REPO_ROOT" "$RNDRSBC_HOME" <<'PY'
# The Pi is a physical panel, so always target it, never the template's
# conservative dev default (virtual).
import json, os, sys
tpl, home = sys.argv[1], sys.argv[2]
cfg_path = os.path.join(home, "config.json")
src = os.path.join(tpl, "config.template.json")
with open(src) as f:
    cfg = json.load(f)
cfg.pop("_comment", None)
# hardware-first: the Pi has an I2C Spectra 6, so auto-detect it via
# InkyDisplay.detect() (falls back only if no panel is physically present).
cfg["display"] = {
    "driver": "auto",
    "model": "spectra6",
    "orientation": cfg.get("display", {}).get("orientation", 0),
    "saturation": cfg.get("display", {}).get("saturation", 0.5),
}
cfg["device"]["name"] = "Spectra 6 Frame"
cfg_path_tmp = cfg_path + ".tmp"
os.makedirs(home, exist_ok=True)
with open(cfg_path_tmp, "w") as f:
    json.dump(cfg, f, indent=2)
os.replace(cfg_path_tmp, cfg_path)
print(f"wrote {cfg_path}")
print("display.driver =", cfg["display"]["driver"], " model =", cfg["display"]["model"])
PY

step "verify resolved version"
"$PY" - "$REPO_ROOT" <<'PY'
import importlib.metadata, sys
sys.path.insert(0, sys.argv[1])
try:
    import rndrsbc
    print("rndrsbc.__version__ =", rndrsbc.__version__)
except Exception as e:
    print("rndrsbc (source checkout) __version__ fallback:", e)
try:
    print("dist version =", importlib.metadata.version("rndrsbc"))
except Exception as e:
    print("not an installed dist:", e)
PY

step "done — next:"
echo "  export RNDRSBC_HOME=$RNDRSBC_HOME"
echo "  ${VENV_DIR}/bin/rndrsbc doctor"
echo "  ${VENV_DIR}/bin/rndrsbc calibrate   # push the 7-colour pattern to the panel"
echo "  ${VENV_DIR}/bin/rndrsbc snapshot"
