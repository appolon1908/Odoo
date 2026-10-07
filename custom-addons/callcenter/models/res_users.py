from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError


class ResUsers(models.Model):
    _inherit = "res.users"

    callcenter_primary_campaign_id = fields.Many2one(
        "crm.team",
        string="Primary Active Campaign",
        domain=[("is_callcenter_campaign", "=", True)],
        ondelete="restrict",
        index=True,
    )
    callcenter_assignment_ids = fields.One2many(
        "callcenter.campaign.assignment",
        "user_id",
        string="Campaign Assignment History",
        readonly=True,
    )
    callcenter_supervised_campaign_ids = fields.Many2many(
        "crm.team",
        string="Supervised Call Center Campaigns",
        compute="_compute_callcenter_supervised_campaign_ids",
        readonly=True,
    )

    @api.depends_context("uid")
    def _compute_callcenter_supervised_campaign_ids(self):
        Team = self.env["crm.team"]
        for user in self:
            user.callcenter_supervised_campaign_ids = Team.search(
                [
                    ("is_callcenter_campaign", "=", True),
                    "|",
                    ("primary_supervisor_id", "=", user.id),
                    ("backup_supervisor_ids", "in", user.id),
                ]
            )

    def _callcenter_can_manage_hierarchy(self):
        return (
            self.env.is_superuser()
            or self.env.user.has_group("base.group_system")
            or self.env.user.has_group("callcenter.group_callcenter_superuser")
        )

    @api.constrains("callcenter_primary_campaign_id")
    def _check_callcenter_primary_campaign(self):
        for user in self:
            if (
                user.callcenter_primary_campaign_id
                and not user.callcenter_primary_campaign_id.is_callcenter_campaign
            ):
                raise ValidationError(
                    _("The primary active campaign must be a call-center campaign.")
                )

    @api.model_create_multi
    def create(self, vals_list):
        if any(vals.get("callcenter_primary_campaign_id") for vals in vals_list):
            if not self._callcenter_can_manage_hierarchy():
                raise AccessError(
                    _("Only the Call Center Super User or a technical administrator may assign campaigns.")
                )
        users = super().create(vals_list)
        for user, vals in zip(users, vals_list):
            if vals.get("callcenter_primary_campaign_id"):
                user._sync_callcenter_agent_assignment()
        return users

    def write(self, vals):
        changing_campaign = "callcenter_primary_campaign_id" in vals
        if changing_campaign and not self._callcenter_can_manage_hierarchy():
            raise AccessError(
                _("Only the Call Center Super User or a technical administrator may transfer agents.")
            )
        result = super().write(vals)
        if changing_campaign and not self.env.context.get("skip_callcenter_assignment_sync"):
            for user in self:
                user._sync_callcenter_agent_assignment()
        return result

    def _sync_callcenter_agent_assignment(self):
        Assignment = self.env["callcenter.campaign.assignment"].sudo()
        now = fields.Datetime.now()
        for user in self:
            active_assignments = Assignment.search(
                [
                    ("user_id", "=", user.id),
                    ("role", "=", "agent"),
                    ("active", "=", True),
                ]
            )
            current_campaign = user.callcenter_primary_campaign_id

            stale = active_assignments.filtered(
                lambda assignment: assignment.campaign_id != current_campaign
            )
            if stale:
                stale.with_context(skip_callcenter_assignment_sync=True).action_close(now)

            if current_campaign:
                matching = active_assignments.filtered(
                    lambda assignment: assignment.campaign_id == current_campaign
                )
                if not matching:
                    Assignment.create(
                        {
                            "campaign_id": current_campaign.id,
                            "user_id": user.id,
                            "role": "agent",
                            "is_primary": True,
                            "date_from": now,
                            "assigned_by_id": self.env.user.id,
                        }
                    )

    def action_assign_callcenter_campaign(self, campaign):
        if not self._callcenter_can_manage_hierarchy():
            raise AccessError(
                _("Only the Call Center Super User or a technical administrator may transfer agents.")
            )
        if campaign and (
            campaign._name != "crm.team" or not campaign.is_callcenter_campaign
        ):
            raise ValidationError(_("The selected team is not a call-center campaign."))
        return self.write(
            {"callcenter_primary_campaign_id": campaign.id if campaign else False}
        )
