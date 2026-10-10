#!/usr/bin/env bash
# Installs or updates the Asra site inside its container. Run as root from the
# repo checkout at /opt/asra. Safe to run again; settings from the first run are
# kept in /etc/asra/install.conf.
#
#   SITE_ADDRESS   ":80" (default) serves plain HTTP for a reverse proxy in front.
#                  A domain such as "asra.joshcross.co.nz" makes Caddy fetch an
#                  HTTPS certificate itself (ports 80 and 443 must reach the container).
#   TRUSTED_PROXY  IP or CIDR of a reverse proxy in front of this container, so
#                  rate limits see real visitor addresses. Leave empty if none.
#   CLIENT_IP_HEADER  header a CDN sets to the visitor's address, for rate
#                  limits (Cloudflare: CF-Connecting-IP). Leave empty if none.
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
echo "Installing packages"
apt-get update -qq
apt-get install -y -qq caddy python3 git ca-certificates curl >/dev/null

id asra >/dev/null 2>&1 || useradd --system --home-dir /nonexistent --no-create-home --shell /usr/sbin/nologin asra

install -d -m 0750 -g asra /etc/asra
printf 'SITE_ADDRESS=%q\nTRUSTED_PROXY=%q\nCLIENT_IP_HEADER=%q\n' "$SITE_ADDRESS" "$TRUSTED_PROXY" "$CLIENT_IP_HEADER" > "$CONF"
chmod 0640 "$CONF"
if [ ! -f /etc/asra/asra.env ]; then
  install -m 0640 -g asra "$APP_DIR/deploy/asra.env.example" /etc/asra/asra.env
fi
grep -q '^ASRA_CLIENT_IP_HEADER=' /etc/asra/asra.env || echo 'ASRA_CLIENT_IP_HEADER=' >> /etc/asra/asra.env
sed -i "s/^ASRA_CLIENT_IP_HEADER=.*/ASRA_CLIENT_IP_HEADER=$CLIENT_IP_HEADER/" /etc/asra/asra.env
if [ -n "$TRUSTED_PROXY" ]; then
  sed -i 's/^ASRA_PROXY_HOPS=.*/ASRA_PROXY_HOPS=1/' /etc/asra/asra.env
fi

echo "Configuring the signature service"
install -m 0644 "$APP_DIR/deploy/asra.service" /etc/systemd/system/asra.service
install -m 0755 "$APP_DIR/deploy/asra-admin" /usr/local/bin/asra-admin
systemctl daemon-reload
systemctl enable asra.service >/dev/null
systemctl restart asra.service

echo "Configuring Caddy for $SITE_ADDRESS"
GLOBAL=""
if [ -n "$TRUSTED_PROXY" ]; then
  GLOBAL=$(printf '{\n\tservers {\n\t\ttrusted_proxies static %s\n\t}\n}' "$TRUSTED_PROXY")
fi
python3 - "$APP_DIR/deploy/Caddyfile" /etc/caddy/Caddyfile.new "$SITE_ADDRESS" "$GLOBAL" <<'PY'
import sys
src, dest, address, global_options = sys.argv[1:5]
text = open(src).read().replace("__GLOBAL_OPTIONS__", global_options).replace("__SITE_ADDRESS__", address)
open(dest, "w").write(text)
PY
caddy validate --config /etc/caddy/Caddyfile.new --adapter caddyfile >/dev/null
mv /etc/caddy/Caddyfile.new /etc/caddy/Caddyfile
systemctl enable caddy >/dev/null
systemctl reload-or-restart caddy

echo "Checking"
for _ in $(seq 1 20); do
  if curl -fsS http://127.0.0.1:8080/api/health >/dev/null 2>&1; then break; fi
  sleep 0.5
done
curl -fsS http://127.0.0.1:8080/api/health >/dev/null || {
  echo "The signature service did not start. See: journalctl -u asra -n 50" >&2; exit 1; }
if [ "$SITE_ADDRESS" = ":80" ]; then
  curl -fsS http://127.0.0.1/ | grep -q "The Asra Project" || {
    echo "Caddy is not serving the site. See: journalctl -u caddy -n 50" >&2; exit 1; }
  curl -fsS http://127.0.0.1/api/count >/dev/null
fi
echo "Done. Version $(git -C "$APP_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown) is live at $SITE_ADDRESS."
