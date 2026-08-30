#!/usr/bin/env bash
#
# rndrSBC — the canonical Pi installer lives in the rndrsbc-deploy repo.
#
# This engine repo ships the code (the `rndrsbc` PyPI package). Getting it onto
# a Raspberry Pi is the deploy repo's single, one-shot installer (`install.sh`),
# which this stub delegates to so the engine never carries a competing full
# installer again. The deploy installer covers: apt (python + SPI/GPIO hardware
# libs + fonts), a --system-site-packages venv, a pypi.org-only install of
# rndrsbc[pi], a hardware-first config (driver=auto, model=spectra6), and the
# systemd auto-start daemon (with rollback-aware config migration).
#
# Usage:
#   git clone https://github.com/thetaylormcrae/rndrsbc-deploy
#   cd rndrsbc-deploy
#   sudo ./install.sh --with-service
#
# This stub also works if a sibling rndrsbc-deploy checkout is present:
#   bash deploy/bootstrap.sh
set -euo pipefail

# Locate a rndrsbc-deploy checkout: sibling dir if present, else instruct clone.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENGINE_ROOT="$(cd "$HERE/.." && pwd)"
PARENT="$(dirname "$ENGINE_ROOT")"

DEPLOY_DIR=""
for candidate in "$PARENT/rndrsbc-deploy" "$PARENT/rndrsbc-deploy_fresh"; do
  if [ -d "$candidate" ] && [ -f "$candidate/install.sh" ]; then
    DEPLOY_DIR="$candidate"
    break
  fi
done

if [ -n "$DEPLOY_DIR" ]; then
  echo "==> delegating to rndrsbc-deploy installer at $DEPLOY_DIR"
  cd "$DEPLOY_DIR"
  exec bash install.sh "$@"
fi

cat <<'MSG'
The rndrsbc Pi installer lives in the rndrsbc-deploy repo, not this engine repo.

  git clone https://github.com/thetaylormcrae/rndrsbc-deploy
  cd rndrsbc-deploy
  sudo ./install.sh --with-service

See https://github.com/thetaylormcrae/rndrsbc-deploy for options.
MSG
exit 1
