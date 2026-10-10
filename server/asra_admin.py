#!/usr/bin/env python3
"""Organiser commands for the signature database. Run on the web server as root
through the asra-admin wrapper, which loads the asra_admin login from
/etc/asra/admin.env.

  asra-admin count                 totals by country
  asra-admin export > sigs.csv     name, town, country, date (no emails)
  asra-admin export --emails       adds email and the updates opt-in
  asra-admin find "text"           search names, towns and emails, for removal requests
  asra-admin delete ID             remove one signature by its id

Back up the database on the Postgres server, for example with pg_dump.
"""
import argparse
import csv
import os
import sys

import psycopg2


def cmd_count(cur, args):
    cur.execute("SELECT country, count(*) FROM signatures GROUP BY country ORDER BY count(*) DESC, country")
    rows = cur.fetchall()
    for country, n in rows:
        print(f"{country}  {n}")
    print(f"Total  {sum(n for _, n in rows)}")


def cmd_export(cur, args):
    columns = ["id", "petition", "name", "town", "country", "created_at"]
    if args.emails:
        columns += ["email", "updates"]
    cur.execute(f"SELECT {', '.join(columns)} FROM signatures ORDER BY id")
    writer = csv.writer(sys.stdout)
    writer.writerow(columns)
    for row in cur:
        writer.writerow(row)


def cmd_find(cur, args):
    like = "%" + args.text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    cur.execute(
        "SELECT id, name, town, country, created_at FROM signatures"
        " WHERE name ILIKE %s OR town ILIKE %s OR email ILIKE %s ORDER BY id",
        (like, like, like),
    )
    rows = cur.fetchall()
    for row in rows:
        print("  ".join(str(v) for v in row))
    if not rows:
        print("No matching signatures.")


def cmd_delete(cur, args):
    cur.execute("DELETE FROM signatures WHERE id = %s", (args.id,))
    print("Deleted." if cur.rowcount else "No signature with that id.")


COMMANDS = {"count": cmd_count, "export": cmd_export, "find": cmd_find, "delete": cmd_delete}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="asra-admin", description="Manage Asra petition signatures.")
    parser.add_argument("--dsn", default=os.environ.get("ASRA_ADMIN_DATABASE_URL", ""),
                        help="connection string for the asra_admin login (default: $ASRA_ADMIN_DATABASE_URL)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("count", help="totals by country")
    export = sub.add_parser("export", help="CSV of signatures to standard output")
    export.add_argument("--emails", action="store_true", help="include email addresses and the updates opt-in")
    find = sub.add_parser("find", help="search names, towns and emails")
    find.add_argument("text")
    delete = sub.add_parser("delete", help="remove one signature")
    delete.add_argument("id", type=int)
    args = parser.parse_args(argv)

    if not args.dsn:
        parser.error("no database login: set ASRA_ADMIN_DATABASE_URL (see /etc/asra/admin.env)")
    conn = psycopg2.connect(args.dsn, connect_timeout=10, application_name="asra-admin")
    try:
        with conn:
            with conn.cursor() as cur:
                COMMANDS[args.command](cur, args)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
