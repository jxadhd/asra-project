#!/usr/bin/env bash
# Creates the asra database and its two logins on an existing Postgres that runs
# in Docker inside a Proxmox container. Run as root on the Proxmox host:
#
#   bash deploy/postgres/setup.sh
#
# It writes the connection details to /root/asra-db.env (readable by root only),
# which create-lxc.sh, or "pct push", hands to the web container. Running it
# again keeps the same passwords.
#
# Settings (environment variables, all optional):
#   DB_CT         Proxmox container running Postgres (default: the one named "database")
#   PG_CONTAINER  Docker container name inside it (default: pgsql)
#   PG_ADMIN_DB   existing database to log in to first (default: the container's
#                 POSTGRES_DB, else its POSTGRES_USER, else postgres)
#   DB_HOST       address the web container connects to (default: DB_CT's eth0 address)
#   DB_PORT       port the web container connects to (default: the port Docker publishes)
#   DB_NAME       default: asra
#   OUT           default: /root/asra-db.env
set -euo pipefail

command -v pct >/dev/null || { echo "Run this on the Proxmox host." >&2; exit 1; }
HERE=$(cd "$(dirname "$0")" && pwd)
PG_CONTAINER=${PG_CONTAINER:-pgsql}
PG_ADMIN_DB=${PG_ADMIN_DB:-}
DB_NAME=${DB_NAME:-asra}
OUT=${OUT:-/root/asra-db.env}
case "$DB_NAME" in *[!a-z0-9_]*|"") echo "DB_NAME may only use a-z, 0-9 and _." >&2; exit 1 ;; esac

DB_CT=${DB_CT:-$(pct list | awk 'NR>1 && $NF=="database" {print $1}' | head -n 1)}
[ -n "$DB_CT" ] || { echo "No container named \"database\". Set DB_CT to the Postgres container's id." >&2; exit 1; }

in_ct() { pct exec "$DB_CT" -- "$@"; }
in_ct docker inspect "$PG_CONTAINER" >/dev/null 2>&1 || {
  echo "No Docker container \"$PG_CONTAINER\" in CT $DB_CT. Set PG_CONTAINER." >&2; exit 1; }

DB_HOST=${DB_HOST:-$(in_ct ip -4 -o addr show dev eth0 | awk '{split($4, a, "/"); print a[1]; exit}')}
DB_PORT=${DB_PORT:-$(in_ct docker port "$PG_CONTAINER" 5432/tcp 2>/dev/null | head -n 1 | sed 's/.*://')}
DB_PORT=${DB_PORT:-5432}
[ -n "$DB_HOST" ] || { echo "Couldn't find CT $DB_CT's address. Set DB_HOST." >&2; exit 1; }

old_password() { [ -r "$OUT" ] && sed -n "s|^$1=postgresql://$2:\([0-9a-f]*\)@.*|\1|p" "$OUT" | head -n 1; }
APP_PASSWORD=$(old_password ASRA_DATABASE_URL asra_app || true)
ADMIN_PASSWORD=$(old_password ASRA_ADMIN_DATABASE_URL asra_admin || true)
APP_PASSWORD=${APP_PASSWORD:-$(openssl rand -hex 24)}
ADMIN_PASSWORD=${ADMIN_PASSWORD:-$(openssl rand -hex 24)}

echo "Setting up database \"$DB_NAME\" in $PG_CONTAINER (CT $DB_CT), reached at $DB_HOST:$DB_PORT"
# Passwords go in on stdin, so they never show in a process list. setup.sql's
# "\ir schema.sql" is replaced by the file itself, since stdin has no directory.
# $POSTGRES_USER and $POSTGRES_DB (the image's own settings) expand inside the
# Docker container, so psql connects to the database the container was made with.
# shellcheck disable=SC2016
{
  printf '\\set dbname %s\n\\set app_password %s\n\\set admin_password %s\n' "$DB_NAME" "$APP_PASSWORD" "$ADMIN_PASSWORD"
  sed "/^\\\\ir schema.sql$/{
r $HERE/schema.sql
d
}" "$HERE/setup.sql"
} | in_ct docker exec -i -e ASRA_ADMIN_DB="$PG_ADMIN_DB" "$PG_CONTAINER" sh -c 'exec psql -q -v ON_ERROR_STOP=1 -U "${POSTGRES_USER:-postgres}" -d "${ASRA_ADMIN_DB:-${POSTGRES_DB:-${POSTGRES_USER:-postgres}}}"' >/dev/null

umask 077
cat > "$OUT" <<ENV
# Asra database logins, made by deploy/postgres/setup.sh. Keep this private.
ASRA_DATABASE_URL=postgresql://asra_app:$APP_PASSWORD@$DB_HOST:$DB_PORT/$DB_NAME
ASRA_ADMIN_DATABASE_URL=postgresql://asra_admin:$ADMIN_PASSWORD@$DB_HOST:$DB_PORT/$DB_NAME
ENV
echo "Done. Logins saved to $OUT (root only)."
