#!/usr/bin/env bash
# Updates the site from main. Run as root inside the container. Kept for older
# instructions; it is the same as "asra deploy".
set -euo pipefail
exec /opt/asra/deploy/asra deploy main
