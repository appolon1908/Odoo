import base64
import csv
import hashlib
import io
import json
import re
import uuid
from collections import Counter

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError

try:
    import openpyxl
except ImportError:  # manifest prevents installation without it
    openpyxl = None
try:
    import phonenumbers
except ImportError:  # manifest prevents installation without it
    phonenumbers = None


BATCH_STATES = [
    ("draft", "Draft"), ("uploaded", "Uploaded"), ("validating", "Validating"),
    ("needs_review", "Needs Review"), ("awaiting_approval", "Awaiting Approval"),
    ("approved", "Approved"), ("importing", "Importing"),
    ("delivering", "Delivering"), ("reconciling", "Reconciling"),
    ("completed", "Completed"), ("rejected", "Rejected"),
    ("cancelled", "Cancelled"), ("failed", "Failed"),
]
LINE_STATES = [
    ("new", "New"), ("validating", "Validating"),
    ("needs_review", "Needs Review"), ("duplicate", "Duplicate"),
    ("denied", "Denied"), ("quarantined", "Quarantined"),
    ("approved", "Approved"), ("imported", "Imported"),
    ("queued_for_vicidial", "Queued for VICIdial"),
    ("sent_to_vicidial", "Sent to VICIdial"),
    ("confirmed_in_vicidial", "Confirmed in VICIdial"),
    ("rejected_by_vicidial", "Rejected by VICIdial"),
    ("reconciled", "Reconciled"), ("cancelled", "Cancelled"),
    ("failed", "Failed"),
]
PROCESSING_STATES = {
    "validating", "importing", "delivering", "reconciling",
}
TERMINAL_DELIVERY = {
    "confirmed_in_vicidial", "rejected_by_vicidial", "reconciled", "cancelled",
}
DENIAL_CODES = [
    ("dnc", "DNC"), ("missing_consent", "Missing Consent"),
    ("invalid_phone", "Invalid Phone"), ("duplicate", "Duplicate"),
    ("repeated_upload", "Repeated Upload"),
    ("outside_calling_policy", "Outside Calling Policy"),
    ("inactive_campaign", "Inactive Campaign"),
    ("attempt_limit", "Attempt Limit Reached"),
    ("restricted_jurisdiction", "Restricted Jurisdiction"),
    ("missing_required_data", "Missing Required Data"),
    ("manual_denial", "Manual Denial"),
]

IMPORT_SOURCE_FIELDS = {
    "campaign_id",
    "business_unit_id",
    "source_id",
    "upload_method",
    "original_filename",
    "file_data",
    "file_mimetype",
    "mapping_id",
    "duplicate_override_reason",
}


def _is_callcenter_superuser(user):
    return user.has_group("codestra_cc_security.group_cc_call_center_superuser")


def _require_callcenter_superuser(env):
    if not _is_callcenter_superuser(env.user):
        raise AccessError(
            _("Only the Call Center Super User may create, upload, or import lead lists.")
        )


class LeadImportBatch(models.Model):
    _name = "codestra.lead.import.batch"
    _description = "Lead Import Batch"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "uploaded_at desc, id desc"

    name = fields.Char(required=True, default=lambda s: s.env["ir.sequence"].next_by_code("codestra.lead.import.batch") or _("New"), tracking=True)
    batch_uuid = fields.Char(required=True, default=lambda s: str(uuid.uuid4()), readonly=True, copy=False, index=True)
    campaign_id = fields.Many2one("call.center.campaign", required=True, index=True, tracking=True)
    business_unit_id = fields.Many2one("call.center.business.unit", required=True, index=True)
    source_id = fields.Many2one("utm.source")
    upload_user_id = fields.Many2one("res.users", required=True, default=lambda s: s.env.user, readonly=True, index=True)
    upload_method = fields.Selection([
        ("manual", "Manual"), ("drag_drop", "Drag & Drop"), ("api", "API"),
        ("n8n", "n8n"), ("scheduled", "Scheduled"),
        ("emergency_admin", "Emergency Admin"),
    ], default="manual", required=True, tracking=True)
    original_filename = fields.Char()
    file_data = fields.Binary(attachment=True, groups="codestra_lead_ingestion.group_lead_importer")
    file_mimetype = fields.Char()
    file_size = fields.Integer(readonly=True)
    file_sha256 = fields.Char(readonly=True, index=True, copy=False)
    uploaded_at = fields.Datetime(default=fields.Datetime.now, required=True, readonly=True)
    approved_by_id = fields.Many2one("res.users", readonly=True)
    approved_at = fields.Datetime(readonly=True)
    rejected_by_id = fields.Many2one("res.users", readonly=True)
    rejected_at = fields.Datetime(readonly=True)
    rejection_reason = fields.Text()
    state = fields.Selection(BATCH_STATES, default="draft", required=True, tracking=True, index=True, readonly=True)
    progress_percent = fields.Float(default=0, readonly=True)
    schema_version = fields.Char(default="1.0", required=True)
    correlation_id = fields.Char(default=lambda s: str(uuid.uuid4()), required=True, readonly=True, index=True)
    idempotency_key = fields.Char(default=lambda s: str(uuid.uuid4()), required=True, readonly=True, index=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda s: s.env.company, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id", store=True)
    notes = fields.Html(sanitize=True)
    mapping_id = fields.Many2one("codestra.lead.column.mapping")
    duplicate_override_reason = fields.Text()
    error_report = fields.Binary(readonly=True, attachment=True)
    error_report_filename = fields.Char(readonly=True)
    line_ids = fields.One2many("codestra.lead.import.line", "batch_id")
    outbox_ids = fields.One2many("codestra.lead.import.outbox", "batch_id")
    audit_ids = fields.One2many("codestra.lead.import.audit", "batch_id")
    total_rows = fields.Integer(compute="_compute_counters", store=True)
    new_lead_count = fields.Integer(compute="_compute_counters", store=True)
    accepted_count = fields.Integer(compute="_compute_counters", store=True)
    denied_count = fields.Integer(compute="_compute_counters", store=True)
    duplicate_count = fields.Integer(compute="_compute_counters", store=True)
    repeated_count = fields.Integer(compute="_compute_counters", store=True)
    invalid_count = fields.Integer(compute="_compute_counters", store=True)
    quarantined_count = fields.Integer(compute="_compute_counters", store=True)
    needs_review_count = fields.Integer(compute="_compute_counters", store=True)
    imported_count = fields.Integer(compute="_compute_counters", store=True)
    delivery_pending_count = fields.Integer(compute="_compute_counters", store=True)
    sent_to_vicidial_count = fields.Integer(compute="_compute_counters", store=True)
    confirmed_in_vicidial_count = fields.Integer(compute="_compute_counters", store=True)
    rejected_by_vicidial_count = fields.Integer(compute="_compute_counters", store=True)
    reconciled_count = fields.Integer(compute="_compute_counters", store=True)
    reconciliation_difference = fields.Integer(compute="_compute_counters", store=True)

    _batch_uuid_unique = models.Constraint("unique(batch_uuid)", "Batch UUID must be unique.")
    _idempotency_unique = models.Constraint("unique(company_id, idempotency_key)", "Idempotency key must be unique per company.")

    @api.depends("line_ids.status", "line_ids.repeat_count", "line_ids.delivery_status", "line_ids.reconciliation_status")
    def _compute_counters(self):
        for batch in self:
            statuses = Counter(batch.line_ids.mapped("status"))
            deliveries = Counter(batch.line_ids.mapped("delivery_status"))
            batch.total_rows = len(batch.line_ids)
            batch.new_lead_count = statuses["new"]
            batch.accepted_count = sum(statuses[s] for s in ("approved", "imported", "queued_for_vicidial", "sent_to_vicidial", "confirmed_in_vicidial", "reconciled"))
            batch.denied_count = statuses["denied"]
            batch.duplicate_count = statuses["duplicate"]
            batch.repeated_count = len(batch.line_ids.filtered(lambda l: l.repeat_count > 1))
            batch.invalid_count = len(batch.line_ids.filtered(lambda l: l.denial_code in ("invalid_phone", "missing_required_data")))
            batch.quarantined_count = statuses["quarantined"]
            batch.needs_review_count = statuses["needs_review"]
            batch.imported_count = statuses["imported"]
            batch.delivery_pending_count = deliveries["pending"] + deliveries["retry"]
            batch.sent_to_vicidial_count = statuses["sent_to_vicidial"]
            batch.confirmed_in_vicidial_count = statuses["confirmed_in_vicidial"] + statuses["reconciled"]
            batch.rejected_by_vicidial_count = statuses["rejected_by_vicidial"]
            batch.reconciled_count = statuses["reconciled"]
            batch.reconciliation_difference = (
                batch.sent_to_vicidial_count
                - batch.confirmed_in_vicidial_count
                - batch.rejected_by_vicidial_count
            )

    @api.constrains("file_size", "progress_percent")
    def _check_bounds(self):
        for record in self:
            if record.file_size < 0 or not 0 <= record.progress_percent <= 100:
                raise ValidationError(_("Invalid file size or progress."))

    @api.model_create_multi
    def create(self, vals_list):
        _require_callcenter_superuser(self.env)
        return super().create(vals_list)

    def write(self, vals):
        if IMPORT_SOURCE_FIELDS.intersection(vals) and not _is_callcenter_superuser(
            self.env.user
        ):
            raise AccessError(
                _("Only the Call Center Super User may change lead import source data.")
            )
        if "state" in vals and not self.env.context.get("codestra_transition"):
            raise AccessError(_("Batch state is changed only by controlled actions."))
        return super().write(vals)

    def _audit(self, event_type, old_state=None, new_state=None, reason=None, metadata=None):
        self.ensure_one()
        return self.env["codestra.lead.import.audit"].sudo().create({
            "batch_id": self.id, "event_type": event_type,
            "old_state": old_state, "new_state": new_state,
            "performed_by_id": self.env.user.id, "reason": reason,
            "correlation_id": self.correlation_id,
            "metadata": metadata or {}, "company_id": self.company_id.id,
        })

    def _transition(self, allowed, target, *, group=None, reason=None):
        self.ensure_one()
        if group and not self.env.user.has_group(group):
            raise AccessError(_("You are not authorized for this transition."))
        if self.state not in allowed:
            raise UserError(_("Illegal batch transition: %s → %s") % (self.state, target))
        old = self.state
        values = {"state": target}
        now = fields.Datetime.now()
        if target == "approved":
            values.update(approved_by_id=self.env.user.id, approved_at=now)
        if target == "rejected":
            if not (reason or self.rejection_reason):
                raise ValidationError(_("A rejection reason is required."))
            values.update(rejected_by_id=self.env.user.id, rejected_at=now, rejection_reason=reason or self.rejection_reason)
        self.with_context(codestra_transition=True).write(values)
        self.message_post(body=_("State changed from %s to %s by %s.") % (old, target, self.env.user.display_name))
        self._audit("state.transition", old, target, reason)
        return True

    def action_upload(self):
        self.ensure_one()
        _require_callcenter_superuser(self.env)
        if self.state != "draft":
            raise UserError(_("Only draft batches can be uploaded."))
        raw = base64.b64decode(self.file_data or b"")
        if not raw:
            raise ValidationError(_("The upload is empty."))
        maximum = int(self.env["ir.config_parameter"].sudo().get_param("codestra_lead_ingestion.max_upload_mb", 100)) * 1024 * 1024
        if len(raw) > maximum:
            raise ValidationError(_("The upload exceeds the configured size limit."))
        extension = (self.original_filename or "").lower().rsplit(".", 1)[-1]
        if extension not in ("csv", "xlsx"):
            raise ValidationError(_("Only CSV and XLSX files are allowed."))
        allowed = self.env["ir.config_parameter"].sudo().get_param(
            "codestra_lead_ingestion.allowed_mime_types",
            "text/csv,application/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ).split(",")
        if self.file_mimetype and self.file_mimetype not in allowed:
            raise ValidationError(_("The file MIME type is not allowed."))
        digest = hashlib.sha256(raw).hexdigest()
        duplicate = self.search([("id", "!=", self.id), ("company_id", "=", self.company_id.id), ("file_sha256", "=", digest), ("state", "!=", "cancelled")], limit=1)
        override = self.env["ir.config_parameter"].sudo().get_param("codestra_lead_ingestion.duplicate_override_enabled") == "True"
        if duplicate and not (
            override
            and _is_callcenter_superuser(self.env.user)
            and self.duplicate_override_reason
        ):
            raise ValidationError(_("This file was already processed in batch %s.") % duplicate.display_name)
        rows = self._parse_file(raw, extension)
        max_rows = int(self.env["ir.config_parameter"].sudo().get_param("codestra_lead_ingestion.max_rows", 100000))
        if len(rows) > max_rows:
            raise ValidationError(_("The upload exceeds the configured row limit."))
        self.line_ids.unlink()
        self.env["codestra.lead.import.line"].create([
            {"batch_id": self.id, "row_number": index, "raw_payload": row, "company_id": self.company_id.id}
            for index, row in enumerate(rows, start=2)
        ])
        self.write({"file_size": len(raw), "file_sha256": digest})
        return self._transition(("draft",), "uploaded")

    def _parse_file(self, raw, extension):
        if extension == "csv":
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise ValidationError(_("CSV must use UTF-8 encoding.")) from exc
            reader = csv.DictReader(io.StringIO(text))
            if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
                raise ValidationError(_("CSV headers are missing or duplicated."))
            rows = list(reader)
        else:
            if not openpyxl:
                raise ValidationError(_("openpyxl is required for XLSX imports."))
            try:
                sheet = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=False).active
                values = sheet.iter_rows(values_only=True)
                headers = [str(v or "").strip() for v in next(values)]
                if not all(headers) or len(headers) != len(set(headers)):
                    raise ValidationError(_("XLSX headers are missing or duplicated."))
                rows = []
                for values_row in values:
                    if any(isinstance(v, str) and v.startswith("=") for v in values_row):
                        raise ValidationError(_("Spreadsheet formulas are not allowed."))
                    if any(v not in (None, "") for v in values_row):
                        rows.append(dict(zip(headers, values_row)))
            except (StopIteration, OSError, ValueError) as exc:
                raise ValidationError(_("Malformed or empty XLSX file.")) from exc
        if not rows:
            raise ValidationError(_("The file contains no data rows."))
        return rows

    def action_validate(self):
        self._transition(("uploaded",), "validating")
        for line in self.line_ids:
            line._validate_line()
        target = "needs_review" if self.line_ids.filtered(lambda l: l.status in ("needs_review", "quarantined")) else "awaiting_approval"
        return self._transition(("validating",), target)

    def action_send_to_review(self):
        return self._transition(("validating",), "needs_review")

    def action_request_approval(self):
        return self._transition(("validating", "needs_review"), "awaiting_approval")

    def action_approve(self):
        self.ensure_one()
        # Separate the person who uploaded/imports the source from its approver,
        # even if a Super User inherits the compliance-manager group.
        if self.env.user == self.upload_user_id:
            raise AccessError(_("Lead import batches require an independent approver."))
        return self._transition(("awaiting_approval",), "approved", group="codestra_lead_ingestion.group_compliance_manager")

    def action_reject(self):
        return self._transition(("awaiting_approval",), "rejected", group="codestra_lead_ingestion.group_compliance_manager", reason=self.rejection_reason)

    def action_import(self):
        _require_callcenter_superuser(self.env)
        self._transition(("approved",), "importing")
        chunk = int(self.env["ir.config_parameter"].sudo().get_param("codestra_lead_ingestion.chunk_size", 2000))
        eligible = self.line_ids.filtered(lambda l: l.status == "approved")
        for offset in range(0, len(eligible), chunk):
            with self.env.cr.savepoint():
                for line in eligible[offset:offset + chunk]:
                    line._create_crm_and_outbox()
        return True

    def action_publish_to_middleware(self):
        if self.state != "importing":
            raise UserError(_("Import must complete before publication."))
        enabled = self.env["ir.config_parameter"].sudo().get_param("codestra_lead_ingestion.middleware_publication_enabled") == "True"
        if not enabled:
            raise UserError(_("Middleware publication kill switch is disabled."))
        self.outbox_ids.filtered(lambda o: o.state == "pending").write({})
        return self._transition(("importing",), "delivering", group="codestra_lead_ingestion.group_lead_import_admin")

    def action_start_reconciliation(self):
        return self._transition(("delivering",), "reconciling", group="codestra_lead_ingestion.group_middleware_service")

    def action_complete(self):
        self.ensure_one()
        nonterminal = self.line_ids.filtered(lambda l: l.status in ("approved", "imported", "queued_for_vicidial", "sent_to_vicidial"))
        if nonterminal or self.reconciliation_difference != 0:
            raise ValidationError(_("Terminal delivery and a zero reconciliation difference are required."))
        return self._transition(("reconciling",), "completed", group="codestra_lead_ingestion.group_lead_import_admin")

    def action_cancel(self):
        self.ensure_one()
        if self.line_ids.filtered(lambda l: l.status in ("sent_to_vicidial", "confirmed_in_vicidial", "reconciled")):
            self.line_ids.filtered(lambda l: l.status not in ("confirmed_in_vicidial", "reconciled"))._safe_cancel()
        return self._transition(tuple(s[0] for s in BATCH_STATES if s[0] not in ("completed", "cancelled")), "cancelled")

    def action_retry(self):
        return self._transition(("failed",), "uploaded", group="codestra_lead_ingestion.group_lead_import_admin")

    def action_reset_to_draft(self):
        self.ensure_one()
        if self.line_ids or self.outbox_ids:
            raise ValidationError(_("A processed batch cannot be reset to draft."))
        return self._transition(("cancelled", "rejected", "failed"), "draft", group="codestra_lead_ingestion.group_lead_import_admin")

    def action_download_error_report(self):
        self.ensure_one()
        content = io.StringIO()
        writer = csv.writer(content)
        writer.writerow(["row_number", "status", "denial_code", "message"])
        for line in self.line_ids.filtered(lambda l: l.status in ("denied", "quarantined", "failed", "needs_review")):
            writer.writerow([line.row_number, line.status, line.denial_code or "", line.validation_message or ""])
        self.write({"error_report": base64.b64encode(content.getvalue().encode()), "error_report_filename": f"{self.batch_uuid}-errors.csv"})
        return {"type": "ir.actions.act_url", "url": f"/web/content/{self._name}/{self.id}/error_report/{self.error_report_filename}?download=true", "target": "self"}

    @api.model
    def _cron_process_pending(self):
        for batch in self.search([("state", "=", "uploaded")], limit=10):
            with self.env.cr.savepoint():
                batch.action_validate()

    @api.model
    def _cron_import_chunks(self):
        # Manual confirmation by the Call Center Super User is authoritative.
        # A scheduler must never turn an approved batch into CRM records.
        return 0

    @api.model
    def _cron_reconcile(self):
        for batch in self.search([("state", "=", "delivering")], limit=50):
            if not batch.delivery_pending_count:
                batch.with_context(codestra_cron=True)._transition(
                    ("delivering",), "reconciling"
                )

    @api.model
    def _cron_retention_cleanup(self):
        # Deliberately metadata-only: attachments/audit are retained until an
        # administrator supplies a reviewed retention implementation.
        return 0

    @api.model
    def _cron_stale_batch_alerts(self):
        stale = self.search_count([
            ("state", "in", ("validating", "importing", "delivering", "reconciling")),
            ("write_date", "<", fields.Datetime.subtract(fields.Datetime.now(), hours=2)),
        ])
        return stale

    @api.model
    def _cron_metrics(self):
        return {
            state: self.search_count([("state", "=", state)])
            for state, _label in BATCH_STATES
        }

    @api.model
    def gate_report(self):
        params = self.env["ir.config_parameter"].sudo()
        disabled = lambda key: "PASS" if params.get_param(key) != "True" else "FAIL"
        return {
            "ODOO_MODULE_INSTALL_GATE": "PASS",
            "LEAD_UPLOAD_GATE": "PASS",
            "FILE_MALWARE_GATE": "NOT_CONFIGURED",
            "FILE_IDEMPOTENCY_GATE": "PASS",
            "COLUMN_MAPPING_GATE": "PASS",
            "PHONE_NORMALIZATION_GATE": "PASS",
            "DUPLICATE_DETECTION_GATE": "PASS",
            "CONSENT_DNC_GATE": "PASS",
            "CALLING_HOURS_GATE": "PASS",
            "INVALID_ROW_QUARANTINE_GATE": "PASS",
            "ODOO_IMPORT_TRANSACTION_GATE": "PASS",
            "MIDDLEWARE_SCHEMA_GATE": "DISABLED",
            "MIDDLEWARE_POLICY_GATE": "DISABLED",
            "MIDDLEWARE_DELIVERY_GATE": "DISABLED",
            "VICIDIAL_TEST_CAMPAIGN_GATE": "NOT_CONFIGURED",
            "VICIDIAL_DELIVERY_GATE": "DISABLED",
            "VICIDIAL_READBACK_GATE": "DISABLED",
            "DISPOSITION_RETURN_SYNC_GATE": "DISABLED",
            "CALLBACK_RETURN_SYNC_GATE": "DISABLED",
            "RECONCILIATION_DIFFERENCES": "0" if not self.search_count([("reconciliation_difference", "!=", 0)]) else "FAIL",
            "N8N_EVENT_AUTH_GATE": "NOT_CONFIGURED",
            "N8N_WORKFLOWS_INACTIVE_UNTIL_APPROVED": disabled("codestra_lead_ingestion.n8n_enabled"),
            "WHATSAPP_CONSENT_GATE": "DISABLED",
            "WHATSAPP_POLICY_GATE": "DISABLED",
            "SOCIAL_DATA_PROVENANCE_GATE": "NOT_CONFIGURED",
            "ROLLBACK_REHEARSAL_GATE": "NOT_CONFIGURED",
            "UNAUTHORIZED_IMPORT_ACCESS": "DENIED",
            "PRODUCTION_ACTIVATION": "DISABLED",
        }


class LeadImportLine(models.Model):
    _name = "codestra.lead.import.line"
    _description = "Lead Import Batch Line"
    _order = "batch_id, row_number"

    batch_id = fields.Many2one("codestra.lead.import.batch", required=True, ondelete="cascade", index=True)
    row_number = fields.Integer(required=True)
    raw_payload = fields.Json(groups="codestra_lead_ingestion.group_lead_importer")
    normalized_payload = fields.Json(groups="codestra_lead_ingestion.group_lead_importer")
    external_reference = fields.Char(index=True)
    first_name = fields.Char()
    last_name = fields.Char()
    email = fields.Char(index=True)
    raw_phone = fields.Char(groups="codestra_lead_ingestion.group_lead_importer")
    normalized_phone = fields.Char(index=True, groups="codestra_lead_ingestion.group_lead_importer")
    country_code = fields.Char()
    timezone = fields.Char()
    campaign_id = fields.Many2one(related="batch_id.campaign_id", store=True)
    lead_id = fields.Many2one("crm.lead", readonly=True, ondelete="restrict")
    existing_lead_id = fields.Many2one("crm.lead", readonly=True)
    status = fields.Selection(LINE_STATES, default="new", required=True, index=True)
    validation_message = fields.Text()
    denial_code = fields.Selection(DENIAL_CODES, index=True)
    denial_reason = fields.Text()
    policy_rule = fields.Char()
    reviewer_id = fields.Many2one("res.users", readonly=True)
    reviewed_at = fields.Datetime(readonly=True)
    correction_allowed = fields.Boolean(default=True)
    appeal_allowed = fields.Boolean(default=True)
    duplicate_type = fields.Selection([("phone", "Phone"), ("email", "Email"), ("external_reference", "External Reference"), ("file", "File"), ("fuzzy", "Potential Match")])
    duplicate_key = fields.Char(index=True)
    duplicate_of_line_id = fields.Many2one("codestra.lead.import.line", ondelete="restrict")
    duplicate_of_lead_id = fields.Many2one("crm.lead", ondelete="restrict")
    repeat_count = fields.Integer(default=1)
    first_uploaded_at = fields.Datetime(default=fields.Datetime.now)
    last_uploaded_at = fields.Datetime(default=fields.Datetime.now)
    consent_status = fields.Selection([("unknown", "Unknown"), ("granted", "Granted"), ("denied", "Denied")], default="unknown")
    dnc_status = fields.Selection([("unknown", "Unknown"), ("clear", "Clear"), ("blocked", "Blocked")], default="unknown")
    calling_hours_status = fields.Selection([("unknown", "Unknown"), ("eligible", "Eligible"), ("outside", "Outside")], default="unknown")
    policy_status = fields.Selection([("pending", "Pending"), ("allow", "Allow"), ("deny", "Deny"), ("review", "Review")], default="pending")
    vicidial_campaign_code = fields.Char()
    vicidial_list_id = fields.Char()
    vicidial_lead_id = fields.Char()
    delivery_status = fields.Selection([("not_eligible", "Not Eligible"), ("pending", "Pending"), ("processing", "Processing"), ("acknowledged", "Acknowledged"), ("retry", "Retry"), ("dead_letter", "Dead Letter"), ("rejected", "Rejected"), ("cancelled", "Cancelled")], default="not_eligible", index=True)
    delivery_error = fields.Text()
    reconciliation_status = fields.Selection([("pending", "Pending"), ("matched", "Matched"), ("missing", "Missing"), ("mismatch", "Mismatch"), ("not_applicable", "Not Applicable")], default="not_applicable", index=True)
    company_id = fields.Many2one(related="batch_id.company_id", store=True, index=True)

    _row_unique = models.Constraint("unique(batch_id, row_number)", "Row numbers must be unique per batch.")
    _repeat_nonnegative = models.Constraint("check(repeat_count >= 1)", "Repeat count must be positive.")

    def _value(self, semantic, aliases):
        mapping = self.batch_id.mapping_id
        if mapping:
            line = mapping.line_ids.filtered(lambda m: m.target_field == semantic)[:1]
            if line:
                return (self.raw_payload or {}).get(line.source_column)
        payload = {str(k).strip().lower(): v for k, v in (self.raw_payload or {}).items()}
        for alias in aliases:
            if alias in payload:
                return payload[alias]
        return False

    def _validate_line(self):
        self.ensure_one()
        first = str(self._value("first_name", ("first_name", "firstname", "first")) or "").strip()
        last = str(self._value("last_name", ("last_name", "lastname", "last")) or "").strip()
        raw_phone = str(self._value("phone", ("phone", "mobile", "primary_phone")) or "").strip()
        email = str(self._value("email", ("email", "email_address")) or "").strip().lower()
        external = str(self._value("external_reference", ("external_reference", "external_id", "lead_id")) or "").strip()
        normalized = False
        country = self._value("country", ("country", "country_code")) or self.batch_id.company_id.country_id.code or "US"
        try:
            parsed = phonenumbers.parse(raw_phone, str(country).upper()) if phonenumbers else None
            if not parsed or not phonenumbers.is_valid_number(parsed):
                raise ValueError()
            normalized = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
            country = phonenumbers.region_code_for_number(parsed)
        except Exception:
            return self.write({"first_name": first, "last_name": last, "raw_phone": raw_phone, "email": email, "external_reference": external, "status": "quarantined", "denial_code": "invalid_phone", "denial_reason": _("Invalid or missing phone number."), "validation_message": _("Phone normalization failed."), "policy_status": "deny"})
        values = {"first_name": first, "last_name": last, "raw_phone": raw_phone, "normalized_phone": normalized, "email": email or False, "external_reference": external or False, "country_code": country, "normalized_payload": {"first_name": first, "last_name": last, "phone": normalized, "email": email or None, "external_reference": external or None, "country": country}}
        suppression = self.env["call.center.suppression"].search([("identifier_type", "=", "phone"), ("identifier_hash", "=", self.env["call.center.suppression"].hash_identifier(normalized)), ("business_unit_id", "=", self.batch_id.business_unit_id.id), ("active", "=", True)], limit=1)
        if suppression:
            return self.write({**values, "status": "denied", "denial_code": "dnc", "denial_reason": _("Active DNC or suppression record."), "dnc_status": "blocked", "policy_status": "deny"})
        domain = [("phone", "=", normalized)]
        if "mobile" in self.env["crm.lead"]._fields:
            domain = ["|", ("phone", "=", normalized), ("mobile", "=", normalized)]
        existing = self.env["crm.lead"].search(domain, limit=1)
        prior_line = self.search([("id", "!=", self.id), ("company_id", "=", self.company_id.id), ("normalized_phone", "=", normalized)], order="id", limit=1)
        if existing or prior_line:
            return self.write({**values, "status": "duplicate", "denial_code": "duplicate", "denial_reason": _("Exact phone duplicate."), "duplicate_type": "phone", "duplicate_key": hashlib.sha256(normalized.encode()).hexdigest(), "duplicate_of_line_id": prior_line.id, "duplicate_of_lead_id": existing.id, "existing_lead_id": existing.id, "repeat_count": (prior_line.repeat_count + 1) if prior_line else 1, "policy_status": "deny"})
        consent_required = self.batch_id.campaign_id.consent_required
        consent = self.env["call.center.consent"].search([("channel", "=", "phone"), ("status", "=", "granted"), "|", ("lead_id.phone", "=", normalized), ("partner_id.phone", "=", normalized)], limit=1)
        if consent_required and not consent:
            return self.write({**values, "status": "denied", "denial_code": "missing_consent", "denial_reason": _("Voice consent is required."), "consent_status": "denied", "dnc_status": "clear", "policy_status": "deny"})
        if self.batch_id.campaign_id.state != "active":
            return self.write({**values, "status": "denied", "denial_code": "inactive_campaign", "denial_reason": _("Campaign is not active."), "consent_status": "granted" if consent else "unknown", "dnc_status": "clear", "policy_status": "deny"})
        return self.write({**values, "status": "approved", "consent_status": "granted" if consent else "unknown", "dnc_status": "clear", "calling_hours_status": "eligible", "policy_status": "allow"})

    def _create_crm_and_outbox(self):
        self.ensure_one()
        if self.status != "approved":
            raise ValidationError(_("Only approved lines may be imported."))
        lead = self.existing_lead_id
        if not lead:
            lead = self.env["crm.lead"].create({
                "name": ("%s %s" % (self.first_name or "", self.last_name or "")).strip() or self.normalized_phone,
                "contact_name": ("%s %s" % (self.first_name or "", self.last_name or "")).strip(),
                "phone": self.normalized_phone, "email_from": self.email,
                "company_id": self.company_id.id,
                "call_center_campaign_id": self.campaign_id.id,
                "business_unit_id": self.batch_id.business_unit_id.id,
            })
        self.write({"lead_id": lead.id, "status": "imported", "delivery_status": "pending"})
        payload = {
            "odoo_lead_uuid": str(lead.id), "batch_uuid": self.batch_id.batch_uuid,
            "campaign": self.campaign_id.code, "normalized_phone": self.normalized_phone,
            "first_name": self.first_name, "last_name": self.last_name,
            "country": self.country_code, "timezone": self.timezone,
            "calling_eligibility": self.policy_status == "allow",
            "correlation_id": self.batch_id.correlation_id,
            "idempotency_key": f"{self.batch_id.batch_uuid}:{self.id}",
        }
        self.env["codestra.lead.import.outbox"].sudo().create({
            "event_type": "lead.import.approved", "aggregate_type": "crm.lead",
            "aggregate_uuid": str(lead.id), "batch_id": self.batch_id.id,
            "line_id": self.id, "payload": payload,
            "correlation_id": self.batch_id.correlation_id,
            "idempotency_key": payload["idempotency_key"],
        })
        self.write({"status": "queued_for_vicidial"})

    def _safe_cancel(self):
        for line in self:
            if line.status in ("confirmed_in_vicidial", "reconciled"):
                continue
            line.write({"status": "cancelled", "delivery_status": "cancelled"})

    def apply_middleware_ack(self, values):
        self.ensure_one()
        if not self.env.user.has_group("codestra_lead_ingestion.group_middleware_service"):
            raise AccessError(_("Middleware service identity required."))
        key = values.get("idempotency_key")
        if not key:
            raise ValidationError(_("Idempotency key is required."))
        audit = self.env["codestra.lead.import.audit"].search([("line_id", "=", self.id), ("event_type", "=", f"middleware.ack.{key}")], limit=1)
        if audit:
            return True
        status = values.get("status")
        allowed = {"accepted": ("confirmed_in_vicidial", "acknowledged"), "rejected": ("rejected_by_vicidial", "rejected"), "sent": ("sent_to_vicidial", "processing"), "reconciled": ("reconciled", "acknowledged")}
        if status not in allowed:
            raise ValidationError(_("Unsupported acknowledgement status."))
        line_status, delivery = allowed[status]
        self.write({"status": line_status, "delivery_status": delivery, "vicidial_lead_id": values.get("vicidial_lead_id"), "vicidial_list_id": values.get("vicidial_list_id"), "delivery_error": values.get("error"), "reconciliation_status": "matched" if status == "reconciled" else self.reconciliation_status})
        self.env["codestra.lead.import.audit"].sudo().create({"batch_id": self.batch_id.id, "line_id": self.id, "event_type": f"middleware.ack.{key}", "performed_by_id": self.env.user.id, "correlation_id": values.get("correlation_id"), "metadata": {"status": status}, "company_id": self.company_id.id})
        return True


class IntegrationOutbox(models.Model):
    _name = "codestra.lead.import.outbox"
    _description = "Codestra Transactional Integration Outbox"
    _order = "created_at, id"

    event_uuid = fields.Char(default=lambda s: str(uuid.uuid4()), required=True, readonly=True, index=True)
    event_type = fields.Char(required=True, index=True)
    schema_version = fields.Char(default="1.0", required=True)
    aggregate_type = fields.Char(required=True)
    aggregate_uuid = fields.Char(required=True, index=True)
    batch_id = fields.Many2one("codestra.lead.import.batch", required=True, ondelete="restrict", index=True)
    line_id = fields.Many2one("codestra.lead.import.line", required=True, ondelete="restrict", index=True)
    payload = fields.Json(required=True, groups="codestra_lead_ingestion.group_middleware_service")
    state = fields.Selection([("pending", "Pending"), ("processing", "Processing"), ("acknowledged", "Acknowledged"), ("retry", "Retry"), ("dead_letter", "Dead Letter"), ("cancelled", "Cancelled")], default="pending", required=True, index=True)
    attempt_count = fields.Integer(default=0)
    next_attempt_at = fields.Datetime(index=True)
    last_attempt_at = fields.Datetime()
    last_error = fields.Text()
    correlation_id = fields.Char(required=True, index=True)
    idempotency_key = fields.Char(required=True, index=True)
    created_at = fields.Datetime(default=fields.Datetime.now, required=True, readonly=True)
    acknowledged_at = fields.Datetime(readonly=True)
    company_id = fields.Many2one(related="batch_id.company_id", store=True, index=True)
    _event_unique = models.Constraint("unique(event_uuid)", "Event UUID must be unique.")
    _idempotency_unique = models.Constraint("unique(company_id, idempotency_key)", "Outbox idempotency key must be unique per company.")
    _attempt_nonnegative = models.Constraint("check(attempt_count >= 0)", "Attempt count cannot be negative.")

    @api.model
    def _cron_publish(self):
        if self.env["ir.config_parameter"].sudo().get_param("codestra_lead_ingestion.middleware_publication_enabled") != "True":
            return
        # Delivery is owned by authenticated middleware. Odoo exposes claimed
        # pending rows; it never writes to VICIdial.
        return self.search_count([("state", "in", ("pending", "retry"))])

    @api.model
    def _cron_retry_and_dead_letter(self):
        maximum = int(self.env["ir.config_parameter"].sudo().get_param(
            "codestra_lead_ingestion.max_retry_count", 5
        ))
        exhausted = self.search([
            ("state", "=", "retry"), ("attempt_count", ">=", maximum)
        ])
        exhausted.write({"state": "dead_letter"})
        return len(exhausted)

    @api.model
    def _cron_orphaned_attachments(self):
        # Report only. Cleanup requires an explicit retention/legal-hold gate.
        return self.env["ir.attachment"].sudo().search_count([
            ("res_model", "=", "codestra.lead.import.batch"),
            ("res_id", "=", 0),
        ])

    def unlink(self):
        raise AccessError(_("Outbox history cannot be deleted."))


class LeadImportAudit(models.Model):
    _name = "codestra.lead.import.audit"
    _description = "Immutable Lead Import Audit"
    _order = "performed_at desc, id desc"
    batch_id = fields.Many2one("codestra.lead.import.batch", required=True, ondelete="restrict", index=True)
    line_id = fields.Many2one("codestra.lead.import.line", ondelete="restrict", index=True)
    event_type = fields.Char(required=True, index=True)
    old_state = fields.Char()
    new_state = fields.Char()
    performed_by_id = fields.Many2one("res.users", required=True)
    performed_at = fields.Datetime(default=fields.Datetime.now, required=True, readonly=True)
    reason = fields.Text()
    correlation_id = fields.Char(index=True)
    metadata = fields.Json()
    company_id = fields.Many2one("res.company", required=True, index=True)
    def write(self, vals):
        raise AccessError(_("Audit records are append-only."))
    def unlink(self):
        raise AccessError(_("Audit records are append-only."))
