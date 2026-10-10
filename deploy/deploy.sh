#!/usr/bin/env bash
# Updates the Asra site from the Proxmox host: pulls main into the web container
# and re-runs its installer. It never touches the database or its logins.
# Run as root:
#
#   curl -fsSL https://raw.githubusercontent.com/jxadhd/asra-project/main/deploy/deploy.sh | bash
#
# Settings: CTID, the web container (default: the one named "asra").
set -euo pipefail

command -v pct >/dev/null || { echo "Run this on the Proxmox host." >&2; exit 1; }
CTID=${CTID:-$(pct list | awk 'NR>1 && $NF=="asra" {print $1}' | head -n 1)}
[ -n "$CTID" ] || { echo "No container named \"asra\". Set CTID." >&2; exit 1; }

echo "Updating container $CTID from main"
pct exec "$CTID" -- env LC_ALL=C.UTF-8 LANG=C.UTF-8 /opt/asra/deploy/update.sh
