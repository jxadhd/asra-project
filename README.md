# The Asra Project

Campaign site calling for the Red Cross, the Red Crescent and other impartial
humanitarian agencies to be allowed to visit every Palestinian detainee, as
Israel's own Supreme Court ordered in June 2026. *Asrā* (أسرى) is Arabic for
prisoners.

The site has the facts, the New Zealand and international petitions, a guide to
starting a local cell, and an online signing form for the international
petition.

## What's here

| Path | What it is |
| --- | --- |
| `petitions/` | Petition wording, ready to paste into Parliament's form. |
| `site/` | The public page: `index.html`, `style.css`, `app.js`. No build step. |
| `server/asra_server.py` | Signature API (Python standard library and psycopg2). |
| `server/asra_admin.py` | Organiser commands: counts, CSV export, removal requests. |
| `deploy/postgres/` | Database setup: `setup.sh` for a Postgres in Docker on Proxmox, `setup.sql` and `schema.sql` for any Postgres. |
| `deploy/create-lxc.sh` | Creates a Debian container on a Proxmox VE host and installs everything. |
| `deploy/install.sh` | Installs or updates the site inside the container. Safe to re-run. |
| `deploy/update.sh` | Pulls `main` and re-runs the installer. |
| `deploy/Caddyfile` | Caddy template: static files, security headers, `/api/*` to the API. |
| `deploy/asra.service` | systemd unit for the API, sandboxed, running as the `asra` user. |
| `tests/` | Tests for the API and admin commands. |

### The API

| Request | Result |
| --- | --- |
| `POST /api/sign` | JSON `{name, town, country, email?, updates?, consent: true}`. Always answers `{"ok": true}` on success, including for a repeat signature, so the form can't be used to check whether someone has signed. |
| `GET /api/count` | `{total, countries: {NZ: 12, ...}, updated}`. Never includes names. |
| `GET /api/health` | `{"ok": true, "database": true}`, or status 503 when the database can't be reached. |

Countries are ISO 3166 two-letter codes. A repeat of the same name, town and
country is stored once. A hidden honeypot field catches simple bots.

## Run it locally

The tests need a throwaway Postgres and a superuser login for it. Each test
creates its own database with `deploy/postgres/setup.sql` and drops it after.

```sh
pip install psycopg2-binary
ASRA_TEST_DSN=postgresql://postgres:postgres@localhost:5432/postgres \
  python3 -m unittest discover -s tests -v
ASRA_DATABASE_URL=postgresql://asra_app:...@localhost/asra \
  python3 server/asra_server.py                   # API on 127.0.0.1:8080
```

To serve the page and the API together, install Caddy and run it with a copy of
`deploy/Caddyfile` where `__GLOBAL_OPTIONS__` is removed, `__SITE_ADDRESS__` is
`:8099` and `root` points at your `site/` folder.

## Deploy on Proxmox

Signatures live in Postgres, not in the web container. The web container uses
the `asra_app` login, which can add signatures and count them by country but
can't read names or delete anything. Organiser commands use `asra_admin`.

### 1. The database

On the Proxmox host, as root, with this repo downloaded:

```sh
bash deploy/postgres/setup.sh
```

It finds the container named `database`, runs `setup.sql` with `psql` inside
its `pgsql` Docker container, and saves both logins, with fresh random
passwords, to `/root/asra-db.env` (root only). It prints the address and port
the web container will use. Override them with `DB_CT`, `PG_CONTAINER`,
`DB_HOST` and `DB_PORT`. Running it again keeps the same passwords.

For any other Postgres, run `setup.sql` yourself as a superuser:
`psql -v app_password=... -v admin_password=... -f deploy/postgres/setup.sql`.

Back up with `pg_dump asra` on the database server, or with your usual Postgres
backups.

### 2. The web container

`deploy/create-lxc.sh` makes an unprivileged Debian container (newest Debian 13
template, falling back to 12) with 1 core, 512 MB of memory and a 4 GB disk. It
starts on boot, clones this repo to `/opt/asra`, copies in `/root/asra-db.env`
and runs `install.sh`. That installs Caddy, creates the `asra` user, starts the
API and checks that the page and the database both answer. The app login goes
in `/etc/asra/asra.env` and the admin login in `/etc/asra/admin.env` (root only).

Run it as root in the Proxmox shell (web UI, then the node, then **Shell**):

```sh
curl -fsSLO https://raw.githubusercontent.com/jxadhd/asra-project/main/deploy/create-lxc.sh
STORAGE=local-zfs CLIENT_IP_HEADER=CF-Connecting-IP bash create-lxc.sh
```

It prints the container's id and LAN address when it finishes. Settings are
listed at the top of the script: `CTID`, `STORAGE`, `BRIDGE`, `IPCONFIG` (for a
fixed address, for example `ip=192.168.50.60/24,gw=192.168.50.1`), `VLAN`, sizes,
`SITE_ADDRESS`, `TRUSTED_PROXY`, `CLIENT_IP_HEADER` and `DB_ENV_FILE`.

To connect a container made before the database existed:

```sh
pct push <CTID> /root/asra-db.env /root/asra-db.env --perms 0600
pct exec <CTID> -- /opt/asra/deploy/update.sh
```

### Making it public

By default the container serves plain HTTP on port 80 (`SITE_ADDRESS=:80`) and
expects a reverse proxy in front to handle HTTPS. For a setup with Cloudflare DNS
and Nginx Proxy Manager:

1. **Cloudflare:** add a proxied (orange cloud) record for `asra`, pointing the
   same way as your other sites (an `A` record to the home WAN address, or a
   `CNAME` to an existing hostname).
2. **Nginx Proxy Manager:** add a proxy host for `asra.joshcross.co.nz`,
   scheme `http`, forward to the container's LAN address, port `80`. On the SSL
   tab, request a Let's Encrypt certificate and turn on Force SSL.
3. Open the site and sign once to check the form works end to end. Remove the
   test signature with `asra-admin find` and `asra-admin delete`.

To let Caddy handle HTTPS itself instead, set `SITE_ADDRESS` to the domain and
forward ports 80 and 443 straight to the container.

### Rate limits

Each connection can add 30 signatures an hour (`ASRA_RATE_LIMIT` in
`/etc/asra/asra.env`). The limit is generous because a cell at a street stall may
sign many people up from one phone. Only valid signatures count toward it.

Behind proxies, the API needs to know the visitor's real address:

- **Behind Cloudflare:** set `CLIENT_IP_HEADER=CF-Connecting-IP`.
- **Behind another reverse proxy only:** set `TRUSTED_PROXY` to its address or
  range (for example `192.168.50.0/24`). Caddy then trusts its
  `X-Forwarded-For` header.

Addresses are only held in memory, hashed, for up to an hour. They are never
stored, and Caddy keeps no access log.

## Update the site

Inside the container, as root:

```sh
/opt/asra/deploy/update.sh
```

From the Proxmox host: `pct exec <CTID> -- /opt/asra/deploy/update.sh`. Settings
from the first install are kept in `/etc/asra/install.conf`. To change one, pass
it with `_OVERRIDE` added, for example `SITE_ADDRESS_OVERRIDE=:80`.

## Organiser commands

Inside the container, as root:

```sh
asra-admin count                  # totals by country
asra-admin export > sigs.csv      # names, towns, countries, dates (no emails)
asra-admin export --emails        # adds emails and the updates opt-in
asra-admin find "Mere"            # look someone up for a removal request
asra-admin delete 42              # remove one signature
```

Keep exports off shared drives and chat: they hold people's names.

## Privacy

The page tells signers what is collected and why. Names, towns and countries go
to the bodies the petition is addressed to. Emails are optional and are never
published or passed on. Only totals and per-country counts are public.

## Before launch

- [ ] Add a contact address for removal requests and new cells. The page currently
      says "contact the organisers" without an address.
- [ ] Re-check the facts on the page after Israel's 11 October 2026 court deadline.
- [ ] Have a native Arabic speaker check the name and its spelling.
