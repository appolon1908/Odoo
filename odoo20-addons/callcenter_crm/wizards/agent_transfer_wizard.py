from odoo import fields, models, _
from odoo.exceptions import AccessError, ValidationError


class CallCenterAgentTransferWizard(models.TransientModel):
    _name = "callcenter.agent.transfer.wizard"
    _description = "Call Center Agent Transfer"

    user_id = fields.Many2one(
        "res.users",
        required=True,
        domain=[("share", "=", False), ("active", "=", True)],
    )
    campaign_id = fields.Many2one(
        "callcenter.campaign",
        required=True,
        domain=[("active", "=", True), ("state", "!=", "closed")],
    )
    effective_at = fields.Datetime(required=True, default=fields.Datetime.now)

    def action_apply(self):
        self.ensure_one()
        if not (
            self.env.is_superuser()
            or self.env.user.has_group("callcenter_crm.group_callcenter_superuser")
        ):
            raise AccessError(_("Only the Call Center Super User may transfer agents."))
        if not self.user_id.has_group("callcenter_crm.group_callcenter_agent"):
            raise ValidationError(_("The selected user must have the Call Center Agent role."))
        self.campaign_id.action_assign_agent(self.user_id, effective_at=self.effective_at)
        return {"type": "ir.actions.act_window_close"}
