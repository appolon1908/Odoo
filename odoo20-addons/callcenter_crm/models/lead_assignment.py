from odoo import api, fields, models, _
from odoo.exceptions import AccessError


class CallCenterLeadAssignment(models.Model):
    _name = "callcenter.lead.assignment"
    _description = "Call Center Lead Assignment History"
    _order = "assigned_at desc, id desc"

    lead_id = fields.Many2one("crm.lead", required=True, index=True, ondelete="restrict")
    campaign_id = fields.Many2one("callcenter.campaign", required=True, index=True, ondelete="restrict")
    agent_id = fields.Many2one("res.users", required=True, index=True, ondelete="restrict")
    assigned_at = fields.Datetime(required=True, default=fields.Datetime.now, index=True)
    released_at = fields.Datetime(index=True)
    assignment_source = fields.Selection(
        [("queue", "Queue"), ("admin", "Admin"), ("manual_agent_creation", "Manual Agent Creation"),
         ("transfer", "Transfer")],
        required=True, default="admin", index=True,
    )
    assigned_by_id = fields.Many2one("res.users", required=True, readonly=True, ondelete="restrict")
    reason = fields.Char()

    _one_open_history = models.UniqueIndex(
        "(lead_id) WHERE released_at IS NULL",
        "A call-center lead may have only one open assignment history row.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        if not (
            self.env.is_superuser()
            or self.env.user.has_group("base.group_system")
            or self.env.user.has_group("callcenter_crm.group_callcenter_superuser")
        ):
            raise AccessError(_("Lead assignment history is system-managed."))
        return super().create(vals_list)

    def write(self, vals):
        if not (self.env.is_superuser() or self.env.user.has_group("base.group_system")):
            raise AccessError(_("Lead assignment history is immutable except for system release timestamps."))
        return super().write(vals)

    def unlink(self):
        if not self.env.is_superuser():
            raise AccessError(_("Lead assignment history cannot be physically deleted."))
        return super().unlink()

    def _release(self, released_at=None):
        when = released_at or fields.Datetime.now()
        for row in self.filtered(lambda r: not r.released_at):
            super(CallCenterLeadAssignment, row.sudo()).write({"released_at": when})
        return True
