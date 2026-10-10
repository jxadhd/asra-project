#!/usr/bin/env bash
# Creates a new unprivileged Debian container on a Proxmox VE host and installs
# the Asra site in it. Run as root on the Proxmox host:
#
#   curl -fsSLO https://raw.githubusercontent.com/jxadhd/asra-project/main/deploy/create-lxc.sh
#   STORAGE=local-lvm BRIDGE=vmbr0 bash create-lxc.sh
#
# Settings (environment variables, all optional):
#   CTID              container id (default: next free id)
#   CT_HOSTNAME       default: asra
#   STORAGE           storage for the root disk (default: local-lvm)
#   TEMPLATE_STORAGE  storage holding container templates (default: local)
#   BRIDGE            network bridge (default: vmbr0)
#   IPCONFIG          "ip=dhcp" (default) or "ip=192.168.1.50/24,gw=192.168.1.1"
#   VLAN              VLAN tag, if the bridge needs one
#   DISK_GB MEMORY_MB CORES   defaults: 4, 512, 1
#   SITE_ADDRESS      passed to install.sh (default: ":80", see README)
#   TRUSTED_PROXY     passed to install.sh (default: none)
#   CLIENT_IP_HEADER  passed to install.sh (default: none; Cloudflare: CF-Connecting-IP)
#   REPO_URL          default: https://github.com/jxadhd/asra-project.git
set -euo pipefail

command -v pct >/dev/null || { echo "Run this on a Proxmox VE host." >&2; exit 1; }

CTID=${CTID:-$(pvesh get /cluster/nextid)}
CT_HOSTNAME=${CT_HOSTNAME:-asra}
STORAGE=${STORAGE:-local-lvm}
TEMPLATE_STORAGE=${TEMPLATE_STORAGE:-local}
BRIDGE=${BRIDGE:-vmbr0}
IPCONFIG=${IPCONFIG:-ip=dhcp}
DISK_GB=${DISK_GB:-4}
MEMORY_MB=${MEMORY_MB:-512}
CORES=${CORES:-1}
SITE_ADDRESS=${SITE_ADDRESS:-:80}
TRUSTED_PROXY=${TRUSTED_PROXY:-}
CLIENT_IP_HEADER=${CLIENT_IP_HEADER:-}
REPO_URL=${REPO_URL:-https://github.com/jxadhd/asra-project.git}

if pct status "$CTID" >/dev/null 2>&1; then
  echo "Container $CTID already exists. Choose another CTID." >&2; exit 1
fi

echo "Finding the newest Debian template"
pveam update >/dev/null
TEMPLATE=""
for release in 13 12; do
  TEMPLATE=$(pveam available --section system | awk -v r="debian-$release-standard" '$2 ~ r {print $2}' | sort -V | tail -n 1)
  [ -n "$TEMPLATE" ] && break
done
[ -n "$TEMPLATE" ] || { echo "No Debian template found in pveam." >&2; exit 1; }
if ! pveam list "$TEMPLATE_STORAGE" | grep -q "$TEMPLATE"; then
  echo "Downloading $TEMPLATE"
  pveam download "$TEMPLATE_STORAGE" "$TEMPLATE"
fi

NET="name=eth0,bridge=$BRIDGE,$IPCONFIG"
[ -n "${VLAN:-}" ] && NET="$NET,tag=$VLAN"

echo "Creating container $CTID ($CT_HOSTNAME) from $TEMPLATE"
pct create "$CTID" "$TEMPLATE_STORAGE:vztmpl/$TEMPLATE" \
  --hostname "$CT_HOSTNAME" \
  --description "The Asra Project campaign site. Source: $REPO_URL" \
  --unprivileged 1 \
  --features nesting=1 \
  --cores "$CORES" \
  --memory "$MEMORY_MB" \
  --swap 256 \
  --rootfs "$STORAGE:$DISK_GB" \
  --net0 "$NET" \
  --onboot 1 \
  --start 1

echo "Waiting for the network"
for _ in $(seq 1 60); do
  if pct exec "$CTID" -- getent hosts deb.debian.org >/dev/null 2>&1; then break; fi
  sleep 2
done
pct exec "$CTID" -- getent hosts deb.debian.org >/dev/null || {
  echo "Container $CTID has no working network or DNS. Check BRIDGE and IPCONFIG." >&2; exit 1; }

echo "Installing the site"
pct exec "$CTID" -- env DEBIAN_FRONTEND=noninteractive bash -c 'apt-get update -qq && apt-get install -y -qq git ca-certificates >/dev/null'
pct exec "$CTID" -- git clone --quiet --depth 1 "$REPO_URL" /opt/asra
pct exec "$CTID" -- env SITE_ADDRESS="$SITE_ADDRESS" TRUSTED_PROXY="$TRUSTED_PROXY" CLIENT_IP_HEADER="$CLIENT_IP_HEADER" /opt/asra/deploy/install.sh

ADDRESSES=$(pct exec "$CTID" -- hostname -I)
echo
echo "Container $CTID is running the site. Addresses: $ADDRESSES"
echo "Next: point your reverse proxy or port forwarding at it (see README)."
