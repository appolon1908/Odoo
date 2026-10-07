from odoo import fields, models, _
from odoo.exceptions import AccessError, ValidationError


class CallCenterAgentTransferWizard(models.TransientModel):
    _name = "callcenter.agent.transfer.wizard"
    _description = "Call Center Agent Transfer"

    user_id = fields.Many2one(
        "res.users",
        string="Agent",
        required=True,
        domain=lambda self: [
            ("share", "=", False),
            (
                "all_group_ids",
                "in",
                self.env.ref("callcenter.group_callcenter_agent").id,
            ),
        ],
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
                _(
                    "Only the Call Center Super User or a technical administrator "
                    "may transfer agents."
                )
            )
        agent_group = self.env.ref("callcenter.group_callcenter_agent")
        if agent_group not in self.user_id.sudo().all_group_ids:
            raise ValidationError(
                _("The selected user does not have the Call Center Agent role.")
            )

        self.user_id.action_assign_callcenter_campaign(
            self.campaign_id,
            effective_at=self.effective_at,
        )
        return {"type": "ir.actions.act_window_close"}
