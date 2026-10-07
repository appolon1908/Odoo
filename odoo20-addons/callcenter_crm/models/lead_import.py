from odoo import api, fields, models, _
from odoo.exceptions import AccessError


class CallCenterLeadImportBatch(models.Model):
    _name = "callcenter.lead.import.batch"
    _description = "Call Center Lead Import Batch"
    _order = "imported_at desc, id desc"

    campaign_id = fields.Many2one("callcenter.campaign", required=True, index=True, ondelete="restrict")
    filename = fields.Char(required=True)
    file_hash = fields.Char(required=True, index=True)
    imported_by_id = fields.Many2one("res.users", required=True, readonly=True, ondelete="restrict")
    imported_at = fields.Datetime(required=True, readonly=True, default=fields.Datetime.now, index=True)
    state = fields.Selection(
        [("draft", "Draft"), ("validated", "Validated"), ("imported", "Imported"), ("failed", "Failed")],
        required=True, default="draft", index=True,
    )
    total_rows = fields.Integer(default=0)
    created_count = fields.Integer(default=0)
    duplicate_count = fields.Integer(default=0)
    error_count = fields.Integer(default=0)
    line_ids = fields.One2many("callcenter.lead.import.line", "batch_id", readonly=True)

    def unlink(self):
        if not self.env.is_superuser():
            raise AccessError(_("Lead import audit batches cannot be deleted."))
        return super().unlink()


class CallCenterLeadImportLine(models.Model):
    _name = "callcenter.lead.import.line"
    _description = "Call Center Lead Import Line"
    _order = "row_number, id"

    batch_id = fields.Many2one("callcenter.lead.import.batch", required=True, index=True, ondelete="restrict")
    row_number = fields.Integer(required=True)
    raw_data = fields.Json(required=True)
    normalized_phone = fields.Char(index=True)
    normalized_email = fields.Char(index=True)
    status = fields.Selection(
        [("ready", "Ready"), ("created", "Created"), ("duplicate", "Duplicate"), ("error", "Error")],
        required=True, default="ready", index=True,
    )
    lead_id = fields.Many2one("crm.lead", readonly=True, ondelete="restrict")
    error_message = fields.Char()

    def unlink(self):
        if not self.env.is_superuser():
            raise AccessError(_("Lead import audit lines cannot be deleted."))
        return super().unlink()
