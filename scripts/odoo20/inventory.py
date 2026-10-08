#!/usr/bin/env python3
"""Read-only, fail-closed Odoo 20 migration inventory for Codestra addons.

Changing a manifest version is NOT proof of Odoo 20 compatibility.
"""
import argparse
import ast
import json
from collections import Counter
from pathlib import Path

TARGET = "20.0"
RISK_PATTERNS = {
    "removed_odoo20_table_query": "_table_query",
    "legacy_database_rpc": "/xmlrpc/2/db",
}

def build_report(root: Path):
    addons = root / "custom-addons"
    entries = []
    for manifest in sorted(addons.glob("*/__manifest__.py")):
        name = manifest.parent.name
        try:
            data = ast.literal_eval(manifest.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("manifest is not a dictionary")
            version = str(data.get("version", ""))
            deps = data.get("depends", [])
            if not isinstance(deps, list):
                raise ValueError("depends must be a list")
            risks = []
            for file in manifest.parent.rglob("*.py"):
                if file.name == "__manifest__.py":
                    continue
                try:
                    source = file.read_text(encoding="utf-8")
                except (UnicodeError, OSError):
                    risks.append("unreadable_python_source")
                    continue
                for reason, needle in RISK_PATTERNS.items():
                    if needle in source:
                        risks.append(reason + ":" + str(file.relative_to(manifest.parent)))
            entries.append({
                "addon": name,
                "version": version,
                "declares_odoo20": version.startswith(TARGET + "."),
                "installable": data.get("installable", True) is not False,
                "depends": deps,
                "static_risks": sorted(set(risks)),
                "manifest_error": None,
            })
        except (OSError, SyntaxError, ValueError, TypeError) as exc:
            entries.append({
                "addon": name, "version": None, "declares_odoo20": False,
                "installable": False, "depends": [], "static_risks": [],
                "manifest_error": type(exc).__name__,
            })
    counts = Counter("odoo20_declared" if item["declares_odoo20"] else "requires_port"
                     for item in entries)
    invalid = sum(bool(item["manifest_error"]) for item in entries)
    flagged = sum(bool(item["static_risks"]) for item in entries)
    return {
        "schema_version": 1,
        "target_odoo": TARGET,
        "manifest_count": len(entries),
        "odoo20_declared": counts["odoo20_declared"],
        "requires_port": counts["requires_port"],
        "invalid_manifests": invalid,
        "static_risk_addons": flagged,
        "runtime_certified": False,
        "note": "Version declarations and static scan never replace Odoo 20 installation/runtime tests.",
        "addons": entries,
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-ready", action="store_true",
                        help="Fail unless every addon declares Odoo 20 and has valid manifest.")
    args = parser.parse_args()
    report = build_report(args.root)
    serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "addons"}, sort_keys=True))
    if not report["manifest_count"]:
        raise SystemExit("No addon manifests found: refuse promotion")
    if args.require_ready and (report["requires_port"] or report["invalid_manifests"]):
        raise SystemExit("Odoo 20 promotion blocked: unported or invalid addon manifests")
if __name__ == "__main__":
    main()
