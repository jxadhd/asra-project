#!/usr/bin/env bash
# Installs or updates the Asra site inside its container, as two Docker
# containers (see compose.yaml). Run as root from the repo checkout at /opt/asra. Safe to run again; settings from the first run are
# kept in /etc/asra/install.conf.
#
#   SITE_ADDRESS   ":80" (default) serves plain HTTP for a reverse proxy in front.
#                  A domain such as "asra.joshcross.co.nz" makes Caddy fetch an
#                  HTTPS certificate itself (ports 80 and 443 must reach the container).
#   TRUSTED_PROXY  IP or CIDR of a reverse proxy in front of this container, so
#                  rate limits see real visitor addresses. Leave empty if none.
#   CLIENT_IP_HEADER  header a CDN sets to the visitor's address, for rate
#                  limits (Cloudflare: CF-Connecting-IP). Leave empty if none.
#   DB_ENV_FILE    database logins made by deploy/postgres/setup.sh (default:
#                  /root/asra-db.env). Moved into /etc/asra when found.
set -euo pipefail

APP_DIR=/opt/asra
CONF=/etc/asra/install.conf

[ "$(id -u)" -eq 0 ] || { echo "Run as root." >&2; exit 1; }
[ -d "$APP_DIR/site" ] || { echo "Expected the repo at $APP_DIR." >&2; exit 1; }

if [ -r "$CONF" ]; then
  # shellcheck disable=SC1090
  . "$CONF"
fi
SITE_ADDRESS=${SITE_ADDRESS_OVERRIDE:-${SITE_ADDRESS:-:80}}
TRUSTED_PROXY=${TRUSTED_PROXY_OVERRIDE:-${TRUSTED_PROXY:-}}
CLIENT_IP_HEADER=${CLIENT_IP_HEADER_OVERRIDE:-${CLIENT_IP_HEADER:-}}
case "$CLIENT_IP_HEADER" in
  *[!A-Za-z0-9-]*) echo "CLIENT_IP_HEADER must be a header name, such as CF-Connecting-IP." >&2; exit 1 ;;
esac

export DEBIAN_FRONTEND=noninteractive
# SSH from a New Zealand machine passes LANG=en_NZ.UTF-8 through "pct exec".
# Generate that locale (and en_US for other visitors) so perl and apt stop
# warning, and make C.UTF-8 the container's default.
export LC_ALL=C.UTF-8 LANG=C.UTF-8
if ! locale -a 2>/dev/null | grep -qix 'en_NZ.utf8'; then
  echo "Setting up locales"
  apt-get update -qq
  apt-get install -y -qq locales >/dev/null
  sed -i -E 's/^# *(en_NZ\.UTF-8 UTF-8|en_US\.UTF-8 UTF-8)/\1/' /etc/locale.gen
  locale-gen >/dev/null
  update-locale LANG=C.UTF-8
fi

echo "Installing packages"
apt-get update -qq
apt-get install -y -qq docker.io docker-compose python3 git ca-certificates curl >/dev/null
docker compose version >/dev/null 2>&1 || {
  echo "Docker Compose v2 ('docker compose') is not available after installing docker-compose." >&2; exit 1; }
systemctl enable --now docker >/dev/null

id asra >/dev/null 2>&1 || useradd --system --home-dir /nonexistent --no-create-home --shell /usr/sbin/nologin asra

install -d -m 0750 -g asra /etc/asra
printf 'SITE_ADDRESS=%q\nTRUSTED_PROXY=%q\nCLIENT_IP_HEADER=%q\n' "$SITE_ADDRESS" "$TRUSTED_PROXY" "$CLIENT_IP_HEADER" > "$CONF"
chmod 0640 "$CONF"
if [ ! -f /etc/asra/asra.env ]; then
  install -m 0640 -g asra "$APP_DIR/deploy/asra.env.example" /etc/asra/asra.env
fi
set_env() {  # set_env KEY VALUE: replace or add one line in /etc/asra/asra.env
  local tmp
  tmp=$(mktemp /etc/asra/.asra.env.XXXXXX)
  grep -v -e "^$1=" -e '^ASRA_DB=' /etc/asra/asra.env > "$tmp" || true
  printf '%s=%s\n' "$1" "$2" >> "$tmp"
  chmod 0640 "$tmp"
  chgrp asra "$tmp"
  mv "$tmp" /etc/asra/asra.env
}
set_env ASRA_CLIENT_IP_HEADER "$CLIENT_IP_HEADER"
if [ -n "$TRUSTED_PROXY" ]; then set_env ASRA_PROXY_HOPS 1; fi

DB_ENV_FILE=${DB_ENV_FILE:-/root/asra-db.env}
if [ -f "$DB_ENV_FILE" ]; then
  echo "Loading database logins from $DB_ENV_FILE"
  APP_URL=$(sed -n 's/^ASRA_DATABASE_URL=//p' "$DB_ENV_FILE")
  ADMIN_URL=$(sed -n 's/^ASRA_ADMIN_DATABASE_URL=//p' "$DB_ENV_FILE")
  case "$APP_URL $ADMIN_URL" in
    postgresql://*" postgresql://"*) ;;
    *) echo "$DB_ENV_FILE needs ASRA_DATABASE_URL and ASRA_ADMIN_DATABASE_URL lines." >&2; exit 1 ;;
  esac
  set_env ASRA_DATABASE_URL "$APP_URL"
  (umask 077; printf 'ASRA_ADMIN_DATABASE_URL=%s\n' "$ADMIN_URL" > /etc/asra/admin.env)
  rm -f "$DB_ENV_FILE"
fi
if ! grep -q '^ASRA_DATABASE_URL=postgresql://' /etc/asra/asra.env; then
  echo "No database login yet. On the Proxmox host, run deploy/postgres/setup.sh, then" >&2
  echo "  pct push <CTID> /root/asra-db.env /root/asra-db.env --perms 0600" >&2
  echo "and run this again. See README.md." >&2
  exit 1
fi

# Version 1 kept signatures in SQLite here. Remove the file only if it is empty.
OLD_DB=/var/lib/asra/signatures.db
if [ -f "$OLD_DB" ]; then
  n=$(python3 -c 'import sqlite3, sys; print(sqlite3.connect(sys.argv[1]).execute("SELECT count(*) FROM signatures").fetchone()[0])' "$OLD_DB" 2>/dev/null || echo unknown)
  if [ "$n" = 0 ]; then
    rm -f "$OLD_DB" "$OLD_DB-wal" "$OLD_DB-shm"
    rmdir /var/lib/asra 2>/dev/null || true
  else
    echo "Note: $OLD_DB from the previous version holds $n signatures. Copy them to Postgres before deleting it." >&2
  fi
fi

# Version 2 ran the API under systemd and Caddy from apt. Docker replaces both.
if [ -f /etc/systemd/system/asra.service ]; then
  echo "Removing the old systemd service"
  systemctl disable --now asra.service >/dev/null 2>&1 || true
  rm -f /etc/systemd/system/asra.service
  systemctl daemon-reload
fi
if systemctl is-enabled caddy >/dev/null 2>&1; then
  echo "Stopping the old Caddy service"
  systemctl disable --now caddy >/dev/null 2>&1 || true
fi

install -m 0755 "$APP_DIR/deploy/asra-admin" /usr/local/bin/asra-admin
install -m 0755 "$APP_DIR/deploy/asra-deploy" /usr/local/bin/asra-deploy
# A link, not a copy, so each "asra deploy" also updates the asra command.
ln -sfn "$APP_DIR/deploy/asra" /usr/local/bin/asra
# "pct enter" starts a shell with PATH=/sbin:/bin:/usr/sbin:/usr/bin, so link
# the commands there too.
for cmd in asra asra-admin asra-deploy; do ln -sfn "/usr/local/bin/$cmd" "/usr/sbin/$cmd"; done

echo "Building and starting the containers for $SITE_ADDRESS"
(umask 077; printf 'SITE_ADDRESS=%s\nTRUSTED_PROXY=%s\n' "$SITE_ADDRESS" "${TRUSTED_PROXY:-127.0.0.1/32}" > /etc/asra/compose.env)
docker compose --project-directory "$APP_DIR" --env-file /etc/asra/compose.env up -d --build --remove-orphans --quiet-pull
docker image prune -f >/dev/null

echo "Checking"
logs="docker compose --project-directory $APP_DIR logs --tail 50"
status=000
for _ in $(seq 1 40); do
  status=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1/api/health || true)
  case "$status" in 200|503) break ;; esac
  sleep 0.5
done
case "$status" in
  200) ;;
  503) echo "The site is running but can't reach the database. Check ASRA_DATABASE_URL in" >&2
       echo "/etc/asra/asra.env and that this container can reach the database server. See: $logs api" >&2
       exit 1 ;;
  *) echo "The site did not start. See: $logs" >&2; exit 1 ;;
esac
if [ "$SITE_ADDRESS" = ":80" ]; then
  # Read the whole page before searching it: "curl | grep -q" makes curl
  # fail (error 23) when grep stops reading early, and pipefail turns that
  # into a false alarm.
  page=$(curl -fsS http://127.0.0.1/ || true)
  case "$page" in
    *"The Asra Project"*) ;;
    *) echo "The page is not being served. See: $logs web" >&2; exit 1 ;;
  esac
fi
echo "Done. Version $(git -C "$APP_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown) is live at $SITE_ADDRESS."
