import base64
import csv
import io

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "codestra_lead_ingestion")
class TestLeadIngestion(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.unit = cls.env["call.center.business.unit"].create({
            "name": "Synthetic Ingestion Unit", "code": "SYN-ING",
            "company_id": cls.env.company.id,
        })
        cls.campaign = cls.env["call.center.campaign"].create({
            "name": "Synthetic Manual Campaign", "code": "SYN_IMPORT",
            "business_unit_id": cls.unit.id, "state": "active",
            "consent_required": False,
            # This suite exercises an existing synthetic manual campaign, not
            # the separately governed automatic-design approval workflow.
            "design_automation_enabled": False,
        })
        cls.importer = cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Synthetic Importer", "login": "synthetic-importer",
            "email": "synthetic-importer@example.invalid",
            "company_id": cls.env.company.id,
            "company_ids": [(6, 0, cls.env.company.ids)],
            "call_center_business_unit_ids": [(6, 0, cls.unit.ids)],
            "call_center_campaign_ids": [(6, 0, cls.campaign.ids)],
            "group_ids": [(6, 0, [
                cls.env.ref("base.group_user").id,
                cls.env.ref("codestra_lead_ingestion.group_lead_importer").id,
            ])],
        })
        cls.compliance = cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Synthetic Compliance", "login": "synthetic-compliance",
            "email": "synthetic-compliance@example.invalid",
            "company_id": cls.env.company.id,
            "company_ids": [(6, 0, cls.env.company.ids)],
            "call_center_business_unit_ids": [(6, 0, cls.unit.ids)],
            "call_center_campaign_ids": [(6, 0, cls.campaign.ids)],
            "group_ids": [(6, 0, [
                cls.env.ref("base.group_user").id,
                cls.env.ref("codestra_lead_ingestion.group_compliance_manager").id,
            ])],
        })
        cls.import_admin = cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Synthetic Import Admin", "login": "synthetic-import-admin",
            "email": "synthetic-import-admin@example.invalid",
            "company_id": cls.env.company.id,
            "company_ids": [(6, 0, cls.env.company.ids)],
            "call_center_business_unit_ids": [(6, 0, cls.unit.ids)],
            "call_center_campaign_ids": [(6, 0, cls.campaign.ids)],
            "group_ids": [(6, 0, [
                cls.env.ref("base.group_user").id,
                cls.env.ref("codestra_lead_ingestion.group_lead_import_admin").id,
                cls.env.ref("codestra_cc_security.group_cc_call_center_superuser").id,
            ])],
        })

    def _batch(self, user=None, content=b"first_name,last_name,phone\nAda,Lovelace,+12025550198\n"):
        env = self.env(user=user or self.import_admin)
        return env["codestra.lead.import.batch"].create({
            "campaign_id": self.campaign.id, "business_unit_id": self.unit.id,
            "company_id": self.env.company.id, "original_filename": "synthetic.csv",
            "file_mimetype": "text/csv", "file_data": base64.b64encode(content),
        })

    def test_legacy_importer_cannot_create_or_upload_batches(self):
        with self.assertRaises(AccessError):
            self._batch(user=self.importer)

    def test_defaults_upload_hash_and_lines(self):
        batch = self._batch()
        self.assertEqual(batch.state, "draft")
        self.assertEqual(batch.upload_user_id, self.import_admin)
        batch.action_upload()
        self.assertEqual(batch.state, "uploaded")
        self.assertEqual(len(batch.file_sha256), 64)
        self.assertEqual(batch.total_rows, 1)
        self.assertEqual(batch.line_ids.status, "new")

    def test_duplicate_checksum_and_invalid_transition(self):
        first = self._batch()
        first.action_upload()
        second = self._batch(content=base64.b64decode(first.file_data))
        with self.assertRaises(ValidationError):
            second.action_upload()
        with self.assertRaises(UserError):
            first.action_approve()
        with self.assertRaises(AccessError):
            first.write({"state": "approved"})

    def test_csv_validation_import_and_outbox(self):
        batch = self._batch()
        batch.action_upload()
        batch.action_validate()
        self.assertEqual(batch.state, "awaiting_approval")
        self.assertEqual(batch.line_ids.status, "approved")
        with self.assertRaises(AccessError):
            batch.action_approve()
        compliance_batch = batch.with_user(self.compliance)
        compliance_batch.action_approve()
        self.assertEqual(compliance_batch.approved_by_id, self.compliance)
        self.assertTrue(compliance_batch.approved_at)
        with self.assertRaises(AccessError):
            compliance_batch.action_import()
        admin_batch = compliance_batch.with_user(self.import_admin)
        admin_batch.action_import()
        self.assertTrue(admin_batch.line_ids.lead_id)
        self.assertEqual(len(admin_batch.sudo().outbox_ids), 1)
        self.assertEqual(admin_batch.sudo().outbox_ids.state, "pending")

    def test_invalid_phone_quarantine_and_error_report(self):
        batch = self._batch(content=b"first_name,last_name,phone\nBad,Phone,not-a-phone\n")
        batch.action_upload()
        batch.action_validate()
        self.assertEqual(batch.line_ids.status, "quarantined")
        action = batch.action_download_error_report()
        self.assertEqual(action["type"], "ir.actions.act_url")
        decoded = base64.b64decode(batch.error_report).decode()
        self.assertIn("invalid_phone", decoded)

    def test_completion_requires_zero_reconciliation(self):
        batch = self._batch()
        batch.action_upload()
        line = batch.line_ids
        line.write({"status": "sent_to_vicidial"})
        batch.with_context(codestra_transition=True).write({"state": "reconciling"})
        self.assertEqual(batch.reconciliation_difference, 1)
        with self.assertRaises(ValidationError):
            batch.with_user(self.compliance).action_complete()

    def test_audit_is_immutable(self):
        batch = self._batch()
        batch.action_upload()
        self.assertTrue(batch.audit_ids)
        with self.assertRaises(AccessError):
            batch.audit_ids.unlink()
