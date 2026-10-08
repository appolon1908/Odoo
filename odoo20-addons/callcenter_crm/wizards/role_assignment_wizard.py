from odoo import fields, models, _
from odoo.exceptions import AccessError


class CallCenterRoleAssignmentWizard(models.TransientModel):
    _name = "callcenter.role.assignment.wizard"
    _description = "Call Center Role Administration"

    user_id = fields.Many2one(
        "res.users", required=True, domain=[("share", "=", False), ("active", "=", True)]
    )
    role = fields.Selection(
        [("agent", "Agent"), ("supervisor", "Supervisor")], required=True
    )

    def action_apply(self):
        self.ensure_one()
        if not (
            self.env.is_superuser()
            or self.env.user.has_group("base.group_system")
            or self.env.user.has_group("callcenter_crm.group_callcenter_superuser")
        ):
            raise AccessError(_("Only Call Center Super Users may assign operational roles."))
        if self.role == "agent":
            self.user_id.action_promote_callcenter_agent()
        else:
            self.user_id.action_promote_callcenter_supervisor()
        return {"type": "ir.actions.act_window_close"}
