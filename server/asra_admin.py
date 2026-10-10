#!/usr/bin/env python3
"""Organiser commands for the signature database. Run on the server through the asra-admin wrapper, which runs it as the asra user.

  asra-admin count                 totals by country
  asra-admin export > sigs.csv     name, town, country, date (no emails)
  asra-admin export --emails       adds email and the updates opt-in
  asra-admin find "text"           search names and towns, to handle removal requests
  asra-admin delete ID             remove one signature by its id
  asra-admin backup [FILE]         consistent copy of the database
                                   (default: /var/lib/asra/backups/asra-<time>.db)
"""
import argparse
import csv
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import asra_server  # noqa: E402


def cmd_count(conn, args):
    rows = conn.execute(
        "SELECT country, COUNT(*) FROM signatures GROUP BY country ORDER BY COUNT(*) DESC"
    ).fetchall()
    total = sum(n for _, n in rows)
    for country, n in rows:
        print(f"{country}  {n}")
    print(f"Total  {total}")


def cmd_export(conn, args):
    columns = ["id", "name", "town", "country", "created_at"]
    if args.emails:
        columns += ["email", "updates"]
    writer = csv.writer(sys.stdout)
    writer.writerow(columns)
    for row in conn.execute(f"SELECT {', '.join(columns)} FROM signatures ORDER BY id"):
        writer.writerow(row)


def cmd_find(conn, args):
    like = f"%{args.text}%"
    rows = conn.execute(
        "SELECT id, name, town, country, created_at FROM signatures"
        " WHERE name LIKE ? OR town LIKE ? OR email LIKE ? ORDER BY id",
        (like, like, like),
    ).fetchall()
    for row in rows:
        print("  ".join(str(v) for v in row))
    if not rows:
        print("No matching signatures.")


def cmd_delete(conn, args):
    cur = conn.execute("DELETE FROM signatures WHERE id = ?", (args.id,))
    conn.commit()
    print("Deleted." if cur.rowcount else "No signature with that id.")


def cmd_backup(conn, args):
    target = args.file
    if not target:
        folder = os.path.join(os.path.dirname(os.path.abspath(args.db)), "backups")
        os.makedirs(folder, mode=0o700, exist_ok=True)
        target = os.path.join(folder, time.strftime("asra-%Y%m%d-%H%M%S.db"))
    dest = sqlite3.connect(target)
    try:
        conn.backup(dest)
    finally:
        dest.close()
    os.chmod(target, 0o600)
    print(f"Backed up to {target}")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="asra-admin", description="Manage Asra petition signatures.")
    parser.add_argument("--db", default=asra_server.DB_PATH, help="database path")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("count", help="totals by country")
    export = sub.add_parser("export", help="CSV of signatures to standard output")
    export.add_argument("--emails", action="store_true", help="include email addresses and the updates opt-in")
    find = sub.add_parser("find", help="search names, towns and emails")
    find.add_argument("text")
    delete = sub.add_parser("delete", help="remove one signature")
    delete.add_argument("id", type=int)
    backup = sub.add_parser("backup", help="copy the database to a file")
    backup.add_argument("file", nargs="?")
    args = parser.parse_args(argv)

    if not os.path.exists(args.db):
        parser.error(f"no database at {args.db}")
    conn = sqlite3.connect(args.db)
    try:
        {"count": cmd_count, "export": cmd_export, "find": cmd_find,
         "delete": cmd_delete, "backup": cmd_backup}[args.command](conn, args)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
