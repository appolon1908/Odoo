from odoo import fields, models, _
from odoo.exceptions import AccessError, ValidationError


class CallCenterCampaignLifecycleWizard(models.TransientModel):
    _name = "callcenter.campaign.lifecycle.wizard"
    _description = "Call Center Campaign Lifecycle"

    campaign_id = fields.Many2one(
        "callcenter.campaign", required=True, readonly=True, ondelete="cascade"
    )
    operation = fields.Selection(
        [
            ("pause", "Pause"),
            ("close", "Close"),
            ("archive", "Archive"),
            ("duplicate", "Duplicate as Draft"),
        ],
        required=True,
        readonly=True,
    )
    reason = fields.Text()
    new_code = fields.Char(string="New Campaign Code")

    def action_confirm(self):
        self.ensure_one()
        if not self.env.user.has_group("callcenter_crm.group_callcenter_superuser"):
            raise AccessError(_("Only the Call Center Super User may perform campaign lifecycle actions."))
        if self.operation in {"pause", "close", "archive"}:
            reason = str(self.reason or "").strip()
            if not reason:
                raise ValidationError(_("A lifecycle reason is required."))
            if self.operation == "pause":
                self.campaign_id.action_pause(reason)
                return {"type": "ir.actions.act_window_close"}
            if self.operation == "close":
                self.campaign_id.action_close(reason)
                return {"type": "ir.actions.act_window_close"}
            self.campaign_id.action_archive(reason)
            return {"type": "ir.actions.act_window_close"}

        duplicate = self.campaign_id.action_duplicate_as_draft(self.new_code)
        return {
            "type": "ir.actions.act_window",
            "name": _("New Campaign Draft"),
            "res_model": "callcenter.campaign",
            "res_id": duplicate.id,
            "view_mode": "form",
            "target": "current",
        }
