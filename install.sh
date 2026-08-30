#!/bin/bash
#
# rndrSBC installation — superseded by deploy/bootstrap.sh (the single
# canonical Pi setup: apt deps + venv + deterministic pypi.org install +
# hardware config + systemd auto-start daemon). All logic now lives there.
#
#   bash deploy/bootstrap.sh
#
# This file remains only as a stable entrypoint for anything that still
# calls the old name install.sh.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
exec bash deploy/bootstrap.sh "$@"
