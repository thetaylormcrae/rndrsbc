#!/usr/bin/env bash
#
# rndrSBC deployment bootstrap (idempotent, ONE canonical Pi setup).
#
# This is the single script that gets the platform running on a Raspberry Pi:
# apt deps, venv + deterministic install from pypi.org, hardware config, and the
# systemd auto-start daemon. It supersedes the older install.sh (which strayed
# by using system python + the git checkout). The daemon and every QA tool
# (doctor/calibrate/snapshot) ship in the same `rndrsbc` wheel, so one install
# covers both.
#
#  1. apt: firmware SPI/GPIO libs + fonts (needed for the physical panel).
#  2. venv + `rndrsbc[pi]`: installed from pypi.org ONLY (never piwheels,
#     whose armv7l mirror can shadow a newer release and stall upgrades).
#  3. writes a valid config.json (hardware-first: driver=auto, model=spectra6)
#     so the panel is probed on I2C rather than defaulting to virtual display.
#  4. installs a systemd service that boots the daemon at startup using the
#     venv binary + $RNDRSBC_HOME (keeps writable state out of site-packages).
#
# Usage (on the Pi):
#   bash deploy/bootstrap.sh                # deploy root = this checkout
#   RNDRSBC_HOME=/srv/rndrsbc bash deploy/bootstrap.sh   # custom deploy root
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${RNDRSBC_VENV:-$REPO_ROOT/.venv}"
RNDRSBC_HOME="${RNDRSBC_HOME:-$REPO_ROOT}"
SVC_NAME="rndrsbc"
SVC="${RNDRSBC_SERVICE:-/etc/systemd/system/${SVC_NAME}.service}"

PY="${VENV_DIR}/bin/python"
PIP="${VENV_DIR}/bin/pip"

step(){ printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

# --- 1. APT: hardware + fonts ---------------------------------------------
need_sudo(){ [ "$(id -u)" -eq 0 ] || command -v sudo >/dev/null 2>&1; }
step "apt: SPI/GPIO libs + fonts"
if need_sudo; then
  (sudo apt-get update && sudo apt-get install -y \
      python3-pip python3-venv python3-pil python3-requests \
      fonts-dejavu) || echo "apt step skipped (no sudo)"
else
  apt-get install -y \
      python3-pip python3-venv python3-pil python3-requests \
      fonts-dejavu
fi

# --- 2. VENV + deterministic install ----------------------------------------
step "venv ($VENV_DIR)"
# --system-site-packages so the venv can see the apt-installed hardware libs
# (RPi.GPIO, spidev, gpiod, smbus2) rather than re-compiling them on-device.
if [ ! -x "$PIP" ]; then
  python3 -m venv --system-site-packages "$VENV_DIR"
fi

step "install/upgrade rndrsbc core (pypi.org only, no piwheels shadow)"
# rndrsbc itself is pulled from pypi.org ONLY so the armv7l piwheels mirror can
# never shadow a newer release and stall upgrades: --index-url (with no
# --extra-index-url) makes pypi.org the ONLY index, and PIP_CONFIG_FILE=/dev/null
# neutralises any ambient /etc/pip.conf on the Pi.
PIP_CONFIG_FILE=/dev/null "$PIP" install --upgrade --no-cache-dir \
  --index-url https://pypi.org/simple \
  "rndrsbc"

step "hardware deps (apt-first; pip default index fallback for arm prebuilt)"
# RPi.GPIO / spidev / gpiod / smbus2 / inky are C libs with armv7l wheels on the
# piwheels fan-in; from pure pypi they'd need a compiler (slow/fragile on a Pi).
# Prefer the system apt copies; use the default pip index (piwheels fan-in is
# safe for these hardware libs) only to top up whatever apt lacks. rndrsbc
# itself stays pinned to pypi.org-only above, so this can't re-introduce the
# stale-rndrsbc shadow.
if command -v apt-get >/dev/null 2>&1; then
  sudo apt-get install -y \
      python3-rpi.gpio python3-spidev \
      python3-gpiod python3-smbus \
  || echo "(some apt hardware libs unavailable - using pip fallback)"
fi
"$PIP" install --upgrade --no-cache-dir \
    "inky" "gpiodevice" \
  || echo "(pip hardware libs skipped - physical drivers only)"


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

# --- 4. SYSTEMD auto-start daemon (venv binary + RNDRSBC_HOME) -------------
systemd_bin=$(command -v systemctl || true)
if [ -n "$systemd_bin" ] && need_sudo; then
  step "systemd service -> $SVC"
  RUSER="${RNDRSBC_USER:-$USER}"
  ESC_HOME="${RNDRSBC_HOME//\//\\\/}"
  ESC_RUSER="${RUSER//\//\\\/}"
  ESC_PY="${PY//\//\\\/}"
  sudo bash -c "cat > \"$SVC\"" <<SVC
[Unit]
Description=rndrSBC E-Paper Display
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$ESC_RUSER
WorkingDirectory=$ESC_HOME
Environment=RNDRSBC_HOME=$ESC_HOME
ExecStart=$ESC_PY -m rndrsbc 8080
Restart=always
RestartSec=5
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
SVC
  sudo systemctl daemon-reload
  sudo systemctl enable "$SVC_NAME"
  echo "service enabled: systemctl start $SVC_NAME"
else
  echo "no systemd/sudo - skipping daemon auto-start; run manually:"
  echo "  export RNDRSBC_HOME=$RNDRSBC_HOME; $PY -m rndrsbc 8080"
fi

step "done — next:"
echo "  export RNDRSBC_HOME=$RNDRSBC_HOME"
echo "  ${VENV_DIR}/bin/rndrsbc doctor       # display.auto should hit the panel, not virtual"
echo "  ${VENV_DIR}/bin/rndrsbc calibrate    # push the 7-colour pattern to the panel"
echo "  ${VENV_DIR}/bin/rndrsbc snapshot     # save the intended frame pre-dither"
echo "  sudo systemctl start $SVC_NAME      # boot the daemon + dashboard :8080"
