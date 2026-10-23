#!/usr/bin/env python3
"""Offline staging validator. No Odoo writes, HTTP requests or contact logs."""
import argparse
import csv
import hashlib
import json
from pathlib import Path

MARKETS = ("Spain", "Dominican_Republic", "Mexico_CRM")
HEADERS = ("contact_name", "phone", "email", "external_id", "company", "source", "notes")


def audit(path: Path, market: str):
    if path.is_symlink() or not path.is_file() or path.suffix != ".csv":
        raise ValueError("source must be a regular CSV")
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("input exceeds 64 MiB")
    stats = {"market": market, "rows": 0, "valid": 0, "duplicates": 0, "missing": 0}
    seen = set()
    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        required = {"lead_id", "full_name", "normalized_phone_primary"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError("invalid CRM-ready source header")
        for row in reader:
            stats["rows"] += 1
            if None in row:
                stats["missing"] += 1
                continue
            number = "".join(c for c in str(row.get("normalized_phone_primary") or "") if c.isdigit())
            if not row.get("full_name") or not 7 <= len(number) <= 16:
                stats["missing"] += 1
            elif number in seen:
                stats["duplicates"] += 1
            else:
                seen.add(number)
                stats["valid"] += 1
    with path.open("rb") as file:
        stats["sha256"] = hashlib.file_digest(file, "sha256").hexdigest()
    stats["consent_verified"] = False
    stats["odoo_import_performed"] = False
    return stats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, required=True)
    args = parser.parse_args()
    report = [audit(args.source_dir / (market + ".csv"), market) for market in MARKETS]
    print(json.dumps({"schema": 1, "results": report, "live_outbound_enabled": False}))


if __name__ == "__main__":
    main()
