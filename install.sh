#!/bin/bash
#
# rndrSBC installation — DEPLOYMENT LIVES IN THE rndrsbc-deploy REPO.
#
# This engine repo ships the code (the `rndrsbc` PyPI package). Installing that
# code onto a Raspberry Pi is the deploy repo's job, so this file is a thin
# delegating stub, not a duplicate installer:
#
#   git clone https://github.com/thetaylormcrae/rndrsbc-deploy
#   cd rndrsbc-deploy
#   sudo ./install.sh --with-service
#
# (Equivalent: run this file with a rndrsbc-deploy checkout next to this one.)
exec bash deploy/deploy-to-my-frame.sh "$@"
