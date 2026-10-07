from odoo import fields, models, _
from odoo.exceptions import AccessError


class CallCenterAgentTransferWizard(models.TransientModel):
    _name = "callcenter.agent.transfer.wizard"
    _description = "Call Center Agent Transfer"

    user_id = fields.Many2one(
        "res.users",
        string="Agent",
        required=True,
        domain=[("share", "=", False)],
    )
    campaign_id = fields.Many2one(
        "crm.team",
        string="New Primary Campaign",
        required=True,
        domain=[("is_callcenter_campaign", "=", True)],
    )
    effective_at = fields.Datetime(
        string="Effective At",
        required=True,
        default=fields.Datetime.now,
    )

    def action_apply(self):
        self.ensure_one()
        if not (
            self.env.is_superuser()
            or self.env.user.has_group("base.group_system")
            or self.env.user.has_group("callcenter.group_callcenter_superuser")
        ):
            raise AccessError(
                _("Only the Call Center Super User or a technical administrator may transfer agents.")
            )

        self.user_id.action_assign_callcenter_campaign(
            self.campaign_id,
            effective_at=self.effective_at,
        )
        return {"type": "ir.actions.act_window_close"}
