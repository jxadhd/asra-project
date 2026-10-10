import io
import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))
import asra_admin  # noqa: E402
import asra_server  # noqa: E402

VALID = {"name": "Mere Tane", "town": "Christchurch", "country": "NZ", "consent": True}


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "sigs.db")
        self.server = asra_server.make_server("127.0.0.1", 0, db_path=self.db, rate_limit=5)
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

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

    def stored(self):
        with sqlite3.connect(self.db) as conn:
            return conn.execute("SELECT name, town, country, email, updates FROM signatures").fetchall()

    def test_health(self):
        self.assertEqual(self.request("/api/health"), (200, {"ok": True}))

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
        rows = sorted(self.stored())
        self.assertEqual(rows[0][3:], (None, 0))
        self.assertEqual(rows[1][3:], ("sam@example.org", 1))

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


class AdminTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "sigs.db")
        asra_server.init_db(self.db)
        record, _ = asra_server.validate(dict(VALID, email="m@example.org", updates=True))
        asra_server.add_signature(record, self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def run_admin(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            asra_admin.main(["--db", self.db, *args])
        return out.getvalue()

    def test_export_hides_emails_unless_asked(self):
        plain = self.run_admin("export")
        self.assertIn("Mere Tane", plain)
        self.assertNotIn("m@example.org", plain)
        self.assertIn("m@example.org", self.run_admin("export", "--emails"))

    def test_find_and_delete(self):
        self.assertIn("Mere Tane", self.run_admin("find", "mere"))
        self.assertEqual(self.run_admin("delete", "1").strip(), "Deleted.")
        self.assertIn("Total  0", self.run_admin("count"))

    def test_backup(self):
        target = os.path.join(self.tmp.name, "copy.db")
        self.run_admin("backup", target)
        with sqlite3.connect(target) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM signatures").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
