import io
import json
import os
import subprocess
import sys
import threading
import unittest
import urllib.error
import urllib.request
import uuid
from contextlib import redirect_stdout

import psycopg2
from psycopg2.extensions import make_dsn, parse_dsn

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "server"))
import asra_admin  # noqa: E402
import asra_server  # noqa: E402

# A superuser connection string for a throwaway Postgres, e.g.
# postgresql://postgres:postgres@localhost:5432/postgres
SUPER_DSN = os.environ.get("ASRA_TEST_DSN", "")
APP_PASSWORD = "app-test-password"
ADMIN_PASSWORD = "admin-test-password"


def setUpModule():
    if not SUPER_DSN:
        raise unittest.SkipTest("set ASRA_TEST_DSN to a superuser connection string to run these tests")


class PostgresCase(unittest.TestCase):
    """Creates a fresh database with deploy/postgres/setup.sql for each test."""

    def setUp(self):
        self.dbname = "asra_test_" + uuid.uuid4().hex[:8]
        subprocess.run(
            ["psql", SUPER_DSN, "-q", "-v", "ON_ERROR_STOP=1",
             "-v", "dbname=" + self.dbname,
             "-v", "app_password=" + APP_PASSWORD,
             "-v", "admin_password=" + ADMIN_PASSWORD,
             "-f", os.path.join(ROOT, "deploy", "postgres", "setup.sql")],
            check=True, stdout=subprocess.DEVNULL,
        )
        base = parse_dsn(SUPER_DSN)
        self.app_dsn = make_dsn(SUPER_DSN, dbname=self.dbname, user="asra_app", password=APP_PASSWORD)
        self.admin_dsn = make_dsn(SUPER_DSN, dbname=self.dbname, user="asra_admin", password=ADMIN_PASSWORD)
        self.super_db_dsn = make_dsn(SUPER_DSN, dbname=self.dbname, user=base.get("user"))

    def tearDown(self):
        conn = psycopg2.connect(SUPER_DSN)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute('DROP DATABASE "%s" WITH (FORCE)' % self.dbname)
        conn.close()

    def stored(self):
        with psycopg2.connect(self.super_db_dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT name, town, country, email, updates FROM signatures ORDER BY id")
            return cur.fetchall()


VALID = {"name": "Mere Tane", "town": "Christchurch", "country": "NZ", "consent": True}


class ServerTest(PostgresCase):
    def setUp(self):
        super().setUp()
        self.server = asra_server.make_server("127.0.0.1", 0, dsn=self.app_dsn, rate_limit=5)
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.server.db.close()
        super().tearDown()

    def request(self, path, body=None, headers=None, raw=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(self.base + path, data=data, method="POST" if data is not None else "GET")
        req.add_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_health(self):
        self.assertEqual(self.request("/api/health"), (200, {"ok": True, "database": True}))

    def test_database_down(self):
        down = asra_server.make_server("127.0.0.1", 0, dsn="postgresql://asra_app@127.0.0.1:1/asra")
        thread = threading.Thread(target=down.serve_forever, daemon=True)
        thread.start()
        base, self.base = self.base, "http://127.0.0.1:%d" % down.server_address[1]
        try:
            self.assertEqual(self.request("/api/health")[0], 503)
            self.assertEqual(self.request("/api/sign", VALID)[0], 503)
            self.assertEqual(self.request("/api/count")[0], 503)
        finally:
            self.base = base
            down.shutdown()
            down.server_close()

    def test_app_login_cannot_read_names(self):
        with psycopg2.connect(self.app_dsn) as conn, conn.cursor() as cur:
            with self.assertRaises(psycopg2.errors.InsufficientPrivilege):
                cur.execute("SELECT name FROM signatures")
        with psycopg2.connect(self.app_dsn) as conn, conn.cursor() as cur:
            with self.assertRaises(psycopg2.errors.InsufficientPrivilege):
                cur.execute("DELETE FROM signatures")

    def test_sign_and_count(self):
        status, body = self.request("/api/sign", VALID)
        self.assertEqual((status, body), (200, {"ok": True}))
        self.request("/api/sign", dict(VALID, name="Aoife Byrne", town="Dublin", country="ie"))
        status, body = self.request("/api/count")
        self.assertEqual(status, 200)
        self.assertEqual(body["total"], 2)
        self.assertEqual(body["countries"], {"NZ": 1, "IE": 1})

    def test_names_are_cleaned(self):
        self.request("/api/sign", dict(VALID, name="  Mere ​\n Tane  ", town="Ōtautahi"))
        self.assertEqual(self.stored()[0][:2], ("Mere Tane", "Ōtautahi"))

    def test_duplicate_is_ignored_with_the_same_reply(self):
        self.assertEqual(self.request("/api/sign", VALID)[0], 200)
        self.assertEqual(self.request("/api/sign", dict(VALID, name="mere tane"))[0], 200)
        self.assertEqual(len(self.stored()), 1)

    def test_validation(self):
        cases = [
            dict(VALID, name=""),
            dict(VALID, name="x" * 101),
            dict(VALID, town=""),
            dict(VALID, country="XX"),
            dict(VALID, email="not-an-email"),
            dict(VALID, consent=False),
            {k: v for k, v in VALID.items() if k != "consent"},
        ]
        for case in cases:
            with self.subTest(case=case):
                status, body = self.request("/api/sign", case)
                self.assertEqual(status, 400)
                self.assertIn("error", body)
        self.assertEqual(self.stored(), [])

    def test_updates_need_an_email(self):
        self.request("/api/sign", dict(VALID, updates=True))
        self.request("/api/sign", dict(VALID, name="Sam", email="Sam@Example.org", updates=True))
        rows = self.stored()
        self.assertEqual(rows[0][3:], (None, False))
        self.assertEqual(rows[1][3:], ("sam@example.org", True))

    def test_honeypot_pretends_success(self):
        status, _ = self.request("/api/sign", dict(VALID, website="http://spam.example"))
        self.assertEqual(status, 200)
        self.assertEqual(self.stored(), [])

    def test_rate_limit(self):
        for i in range(5):
            self.assertEqual(self.request("/api/sign", dict(VALID, name="Person %d" % i))[0], 200)
        status, body = self.request("/api/sign", dict(VALID, name="One too many"))
        self.assertEqual(status, 429)
        # A different visitor behind the same proxy is not limited.
        other = {"X-Forwarded-For": "203.0.113.9"}
        self.assertEqual(self.request("/api/sign", dict(VALID, name="Someone else"), other)[0], 200)

    def test_bad_requests(self):
        self.assertEqual(self.request("/api/sign", raw=b"{not json")[0], 400)
        self.assertEqual(self.request("/api/sign", raw=b"x" * 5000)[0], 413)
        self.assertEqual(self.request("/api/sign", raw=b"[]")[0], 400)
        self.assertEqual(self.request("/api/other")[0], 404)

    def test_count_does_not_leak_names(self):
        self.request("/api/sign", VALID)
        _, body = self.request("/api/count")
        self.assertNotIn("Mere", json.dumps(body))


class AdminTest(PostgresCase):
    def setUp(self):
        super().setUp()
        db = asra_server.Database(self.app_dsn)
        record, _ = asra_server.validate(dict(VALID, email="m@example.org", updates=True))
        asra_server.add_signature(db, record)
        db.close()

    def run_admin(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            asra_admin.main(["--dsn", self.admin_dsn, *args])
        return out.getvalue()

    def test_export_hides_emails_unless_asked(self):
        plain = self.run_admin("export")
        self.assertIn("Mere Tane", plain)
        self.assertNotIn("m@example.org", plain)
        self.assertIn("m@example.org", self.run_admin("export", "--emails"))

    def test_find_and_delete(self):
        self.assertIn("Mere Tane", self.run_admin("find", "mere"))
        self.assertIn("No matching", self.run_admin("find", "100%"))
        self.assertEqual(self.run_admin("delete", "1").strip(), "Deleted.")
        self.assertIn("Total  0", self.run_admin("count"))

    def test_setup_is_safe_to_run_again(self):
        subprocess.run(
            ["psql", SUPER_DSN, "-q", "-v", "ON_ERROR_STOP=1", "-v", "dbname=" + self.dbname,
             "-v", "app_password=" + APP_PASSWORD, "-v", "admin_password=" + ADMIN_PASSWORD,
             "-f", os.path.join(ROOT, "deploy", "postgres", "setup.sql")],
            check=True, stdout=subprocess.DEVNULL,
        )
        self.assertIn("Total  1", self.run_admin("count"))


if __name__ == "__main__":
    unittest.main()
