#!/usr/bin/env python3
"""Signature API for The Asra Project petition site.

Standard library only. Caddy serves the static site and forwards /api/* here.

  POST /api/sign    add a signature (JSON body)
  GET  /api/count   total signatures and counts by country
  GET  /api/health  liveness check

Configuration comes from the environment (see deploy/asra.env.example).
"""
import hashlib
import json
import os
import re
import sqlite3
import sys
import threading
import time
import unicodedata
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DB_PATH = os.environ.get("ASRA_DB", "/var/lib/asra/signatures.db")
HOST = os.environ.get("ASRA_HOST", "127.0.0.1")
PORT = int(os.environ.get("ASRA_PORT", "8080"))
# Signatures accepted per client address per hour. Generous, because a cell
# collecting at a stall may sign many people up from one phone or wifi.
RATE_LIMIT = int(os.environ.get("ASRA_RATE_LIMIT", "30"))
# How to find the visitor's address for rate limits (see README, "Rate limits").
# A header set by a CDN in front, such as CF-Connecting-IP for Cloudflare.
CLIENT_IP_HEADER = os.environ.get("ASRA_CLIENT_IP_HEADER", "").strip()
# Otherwise: reverse proxies in front of Caddy that Caddy trusts.
PROXY_HOPS = int(os.environ.get("ASRA_PROXY_HOPS", "0"))
COUNT_CACHE_SECONDS = 30
MAX_BODY = 4096
PETITION = "international"

# ISO 3166-1 alpha-2 codes. The page shows names with Intl.DisplayNames.
COUNTRIES = frozenset("""
AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL
BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV
CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD
GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM
IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK
LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW
MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR
PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS
ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY
UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW
""".split())

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS signatures (
    id          INTEGER PRIMARY KEY,
    petition    TEXT NOT NULL,
    name        TEXT NOT NULL,
    town        TEXT NOT NULL,
    country     TEXT NOT NULL,
    email       TEXT,
    updates     INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    dedupe_key  TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS signatures_country ON signatures (petition, country);
"""


def connect(path=None):
    conn = sqlite3.connect(path or DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def init_db(path=None):
    directory = os.path.dirname(os.path.abspath(path or DB_PATH))
    os.makedirs(directory, exist_ok=True)
    with connect(path) as conn:
        conn.executescript(SCHEMA)


def clean_text(value, limit):
    """Normalise a free-text field: NFC, no control characters, single spaces."""
    if not isinstance(value, str):
        return ""
    value = unicodedata.normalize("NFC", value)
    value = "".join(ch for ch in value if unicodedata.category(ch)[0] != "C")
    value = " ".join(value.split())
    return value if len(value) <= limit else None


def validate(payload):
    """Return (record, None) for a valid signature, or (None, message)."""
    if not isinstance(payload, dict):
        return None, "Send the form as JSON."
    name = clean_text(payload.get("name"), 100)
    town = clean_text(payload.get("town"), 100)
    country = str(payload.get("country", "")).strip().upper()
    email = clean_text(payload.get("email") or "", 254)
    if not name:
        return None, "Enter your full name (up to 100 characters)."
    if not town:
        return None, "Enter your town or city (up to 100 characters)."
    if country not in COUNTRIES:
        return None, "Choose your country from the list."
    if email is None or (email and not EMAIL_RE.match(email)):
        return None, "Check your email address, or leave it blank."
    if payload.get("consent") is not True:
        return None, "Tick the box to agree to your name being added to the petition."
    updates = bool(email) and payload.get("updates") is True
    return {
        "name": name,
        "town": town,
        "country": country,
        "email": email.lower() or None,
        "updates": 1 if updates else 0,
    }, None


def dedupe_key(record):
    raw = "|".join([PETITION, record["name"].casefold(), record["town"].casefold(), record["country"]])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def add_signature(record, path=None):
    """Store a signature. A repeat of the same name, town and country is ignored."""
    with connect(path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO signatures (petition, name, town, country, email, updates, created_at, dedupe_key)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                PETITION,
                record["name"],
                record["town"],
                record["country"],
                record["email"],
                record["updates"],
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                dedupe_key(record),
            ),
        )


def counts(path=None):
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT country, COUNT(*) FROM signatures WHERE petition = ? GROUP BY country ORDER BY COUNT(*) DESC",
            (PETITION,),
        ).fetchall()
    by_country = {country: n for country, n in rows}
    return {
        "total": sum(by_country.values()),
        "countries": by_country,
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


class RateLimiter:
    """Sliding one-hour window per client, held in memory only.

    Addresses are hashed with a per-process salt and never written to disk.
    """

    def __init__(self, limit, window=3600):
        self.limit = limit
        self.window = window
        self.salt = os.urandom(16)
        self.hits = {}
        self.lock = threading.Lock()

    def allow(self, address):
        key = hashlib.sha256(self.salt + address.encode("utf-8")).digest()
        now = time.monotonic()
        with self.lock:
            recent = [t for t in self.hits.get(key, ()) if now - t < self.window]
            if len(recent) >= self.limit:
                self.hits[key] = recent
                return False
            recent.append(now)
            self.hits[key] = recent
            if len(self.hits) > 50000:
                self.hits = {k: v for k, v in self.hits.items() if v and now - v[-1] < self.window}
            return True


class Handler(BaseHTTPRequestHandler):
    server_version = "asra"
    sys_version = ""
    db_path = None
    limiter = None
    count_cache = {"at": 0.0, "body": None}
    count_lock = threading.Lock()

    def log_message(self, fmt, *args):
        # Log the request line and status only, never the client address.
        sys.stderr.write("%s\n" % (fmt % args))

    def client_address_for_limits(self):
        if CLIENT_IP_HEADER:
            value = self.headers.get(CLIENT_IP_HEADER, "").strip()
            if value:
                return value
        forwarded = [p.strip() for p in self.headers.get("X-Forwarded-For", "").split(",") if p.strip()]
        if forwarded:
            index = max(0, len(forwarded) - 1 - PROXY_HOPS)
            return forwarded[index]
        return self.client_address[0]

    def send_json(self, status, body, cache=False):
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=30" if cache else "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/api/health":
            return self.send_json(200, {"ok": True})
        if self.path == "/api/count":
            with self.count_lock:
                cache = self.count_cache
                if cache["body"] is None or time.monotonic() - cache["at"] > COUNT_CACHE_SECONDS:
                    cache["body"] = counts(self.db_path)
                    cache["at"] = time.monotonic()
                body = cache["body"]
            return self.send_json(200, body, cache=True)
        return self.send_json(404, {"error": "Not found."})

    def do_POST(self):
        if self.path != "/api/sign":
            return self.send_json(404, {"error": "Not found."})
        if "application/json" not in self.headers.get("Content-Type", ""):
            return self.send_json(415, {"error": "Send the form as JSON."})
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY:
            return self.send_json(413, {"error": "The form is too large."})
        try:
            payload = json.loads(self.rfile.read(length) or b"null")
        except (ValueError, UnicodeDecodeError):
            return self.send_json(400, {"error": "Send the form as JSON."})

        # Honeypot: people never see this field, bots fill it. Pretend success.
        if isinstance(payload, dict) and payload.get("website"):
            return self.send_json(200, {"ok": True})

        record, error = validate(payload)
        if error:
            return self.send_json(400, {"error": error})
        # Only valid signatures count toward the limit, so typing mistakes at a
        # busy stall don't lock people out.
        if not self.limiter.allow(self.client_address_for_limits()):
            return self.send_json(429, {"error": "Too many signatures from this connection. Try again in an hour."})
        add_signature(record, self.db_path)
        with self.count_lock:
            self.count_cache["body"] = None
        # The same reply whether or not this person had already signed, so the
        # form cannot be used to check whether someone has signed.
        return self.send_json(200, {"ok": True})


def make_server(host=HOST, port=PORT, db_path=None, rate_limit=RATE_LIMIT):
    init_db(db_path)
    handler = type("BoundHandler", (Handler,), {
        "db_path": db_path,
        "limiter": RateLimiter(rate_limit),
        "count_cache": {"at": 0.0, "body": None},
        "count_lock": threading.Lock(),
    })
    return ThreadingHTTPServer((host, port), handler)


def main():
    server = make_server()
    sys.stderr.write("asra signature API listening on %s:%d\n" % (HOST, PORT))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
