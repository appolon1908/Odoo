import tempfile
import unittest
from pathlib import Path
from scripts.odoo20.inventory import build_report


class TestMigrationInventory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addons = self.root / "custom-addons"
        self.addons.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def addon(self, name, version, code=""):
        directory = self.addons / name
        directory.mkdir()
        (directory / "__manifest__.py").write_text(
            repr({"name": name, "version": version, "depends": ["base"]}),
            encoding="utf-8",
        )
        if code:
            (directory / "models.py").write_text(code, encoding="utf-8")

    def test_19_remains_not_ready(self):
        self.addon("old", "19.0.1.0.0")
        report = build_report(self.root)
        self.assertEqual(report["manifest_count"], 1)
        self.assertEqual(report["requires_port"], 1)
        self.assertFalse(report["runtime_certified"])

    def test_version_20_still_not_runtime_certified(self):
        self.addon("new", "20.0.1.0.0")
        report = build_report(self.root)
        self.assertEqual(report["requires_port"], 0)
        self.assertFalse(report["runtime_certified"])

    def test_removed_table_query_is_reported(self):
        self.addon("risk", "20.0.1.0.0", code="x = '_table_query'")
        report = build_report(self.root)
        self.assertEqual(report["static_risk_addons"], 1)

    def test_invalid_manifest_fail_closed(self):
        bad = self.addons / "bad"
        bad.mkdir()
        (bad / "__manifest__.py").write_text("{", encoding="utf-8")
        self.assertEqual(build_report(self.root)["invalid_manifests"], 1)


if __name__ == "__main__":
    unittest.main()
