"""Synthetic-only tests for the offline Spanish lead preflight."""
import csv
import importlib.util
import pathlib
import tempfile
import unittest

SOURCE = pathlib.Path(__file__).resolve().parents[1] / "scripts/spanish_leads_preflight.py"
spec = importlib.util.spec_from_file_location("lead_preflight", SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class SpanishLeadPreflightTest(unittest.TestCase):
    def test_markets_and_wizard_columns(self):
        self.assertEqual(len(module.MARKETS), 3)
        self.assertIn("contact_name", module.HEADERS)
        self.assertIn("external_id", module.HEADERS)

    def test_valid_duplicate_invalid_row_accounting(self):
        with tempfile.TemporaryDirectory() as folder:
            p = pathlib.Path(folder) / "Spain.csv"
            with p.open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=[
                    "lead_id", "full_name", "normalized_phone_primary"])
                w.writeheader()
                w.writerow(dict(lead_id="1", full_name="Synthetic A",
                                normalized_phone_primary="+34 911 111 111"))
                w.writerow(dict(lead_id="2", full_name="Synthetic B",
                                normalized_phone_primary="+34 911 111 111"))
                w.writerow(dict(lead_id="3", full_name="Synthetic C",
                                normalized_phone_primary="invalid"))
            result = module.audit(p, "Spain")
            self.assertEqual(result["rows"], 3)
            self.assertEqual(result["valid"], 1)
            self.assertEqual(result["duplicates"], 1)
            self.assertEqual(result["missing"], 1)
            self.assertFalse(result["consent_verified"])
            self.assertFalse(result["odoo_import_performed"])

    def test_refuse_missing_file(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                module.audit(pathlib.Path(folder) / "missing.csv", "Spain")

    def test_refuse_unexpected_headers(self):
        with tempfile.TemporaryDirectory() as folder:
            p = pathlib.Path(folder) / "Mexico_CRM.csv"
            p.write_text("name,telephone\nSynthetic,123\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                module.audit(p, "Mexico_CRM")


if __name__ == "__main__":
    unittest.main()
