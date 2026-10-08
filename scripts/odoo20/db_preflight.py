#!/usr/bin/env python3
"""Read-only Odoo 20 PostgreSQL preflight.

Credentials come exclusively from standard PGHOST, PGPORT, PGUSER,
PGPASSWORD, PGDATABASE environment variables; never print a DSN or secret.
"""
import json
import os
import re
import subprocess
import sys

SAFE_DB = re.compile(r"^codestra_odoo20_(?:staging|certification(?:_[a-z0-9_]+)?)$")

def run():
    db = os.getenv("PGDATABASE", "")
    if not SAFE_DB.fullmatch(db):
        raise SystemExit("DENY: explicit Odoo 20 staging/certification database name required")
    if not os.getenv("PGPASSWORD"):
        raise SystemExit("DENY: empty PGPASSWORD")
    sql = (
        "SELECT current_database(), current_setting('server_version'),"
        " COALESCE((SELECT latest_version FROM ir_module_module WHERE name='base'), ''),"
        " (SELECT count(*) FROM ir_module_module WHERE name LIKE 'codestra_%' AND state='installed'),"
        " (SELECT count(*) FROM ir_module_module WHERE name LIKE 'codestra_%' AND state='uninstallable')"
    )
    command = ["psql", "-X", "--no-psqlrc", "-q", "-A", "-t", "-F", "|",
               "-v", "ON_ERROR_STOP=1", "-c", sql]
    try:
        process = subprocess.run(command, check=False, capture_output=True, text=True,
                                 timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SystemExit("DENY: psql unavailable or timed out") from exc
    if process.returncode:
        raise SystemExit("DENY: PostgreSQL preflight could not authenticate/query database")
    parts = process.stdout.strip().split("|")
    if len(parts) != 5 or parts[0] != db or not parts[2].startswith("20."):
        raise SystemExit("DENY: not an Odoo 20 database")
    result = {
        "database": db, "postgres_version": parts[1], "odoo_base_version": parts[2],
        "codestra_installed": int(parts[3]), "codestra_uninstallable": int(parts[4]),
        "promoted": False,
    }
    print(json.dumps(result, sort_keys=True))
    if result["codestra_uninstallable"]:
        raise SystemExit("DENY: Codestra addons remain uninstallable in Odoo 20")
if __name__ == "__main__":
    run()
