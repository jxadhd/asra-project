#!/usr/bin/env bash
# Pulls the latest main branch and reinstalls. Run as root inside the container.
set -euo pipefail
git -C /opt/asra pull --ff-only --quiet origin main
exec /opt/asra/deploy/install.sh
