#!/usr/bin/env bash
# One command to deploy or update the Asra site from the Proxmox host. Run as root:
#
#   curl -fsSL https://raw.githubusercontent.com/jxadhd/asra-project/main/deploy/deploy.sh | bash
#
# It downloads the latest main branch, sets up the Postgres database the first
# time (deploy/postgres/setup.sh), hands the logins to the web container and
# runs its update. Safe to run again: later runs just update the site.
#
# Settings (environment variables, all optional; put them before "bash"):
#   CTID          web container (default: the one named "asra")
#   REF           branch, tag or commit to deploy (default: main)
#   PG_ADMIN_DB, DB_CT, PG_CONTAINER, DB_HOST, DB_PORT   passed to setup.sh
set -euo pipefail

command -v pct >/dev/null || { echo "Run this on the Proxmox host." >&2; exit 1; }
REF=${REF:-main}
DB_ENV_FILE=/root/asra-db.env
CTID=${CTID:-$(pct list | awk 'NR>1 && $NF=="asra" {print $1}' | head -n 1)}
[ -n "$CTID" ] || { echo "No container named \"asra\". Set CTID, or create one with deploy/create-lxc.sh." >&2; exit 1; }

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
echo "Downloading $REF"
curl -fsSL "https://github.com/jxadhd/asra-project/archive/$REF.tar.gz" | tar xz -C "$WORK" --strip-components 1

if pct exec "$CTID" -- test -s /etc/asra/admin.env; then
  echo "Container $CTID already has its database logins"
else
  if [ ! -s "$DB_ENV_FILE" ]; then
    bash "$WORK/deploy/postgres/setup.sh"
  fi
  echo "Giving the database logins to container $CTID"
  pct push "$CTID" "$DB_ENV_FILE" /root/asra-db.env --perms 0600
fi

echo "Updating container $CTID"
pct exec "$CTID" -- /opt/asra/deploy/update.sh
