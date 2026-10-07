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
        context={"active_test": False},
    )
    callcenter_supervised_campaign_ids = fields.Many2many(
        "crm.team",
        string="Supervised Call Center Campaigns",
        compute="_compute_callcenter_access_context",
        readonly=True,
    )
    callcenter_visible_campaign_ids = fields.Many2many(
        "crm.team",
        string="Visible Call Center Campaigns",
        compute="_compute_callcenter_access_context",
        readonly=True,
    )
    callcenter_is_operator = fields.Boolean(
        string="Call Center Operator",
        compute="_compute_callcenter_access_context",
        readonly=True,
    )
    callcenter_is_superuser = fields.Boolean(
        string="Call Center Super User",
        compute="_compute_callcenter_access_context",
        readonly=True,
    )

    @api.depends("group_ids", "callcenter_primary_campaign_id")
    def _compute_callcenter_access_context(self):
        Team = self.env["crm.team"].sudo()
        agent_group = self.env.ref("callcenter.group_callcenter_agent").id
        supervisor_group = self.env.ref("callcenter.group_callcenter_supervisor").id
        superuser_group = self.env.ref("callcenter.group_callcenter_superuser").id

        for user in self:
            group_ids = set(user.sudo().all_group_ids.ids)
            is_superuser = superuser_group in group_ids
            is_supervisor = supervisor_group in group_ids
            is_agent = agent_group in group_ids

            supervised = Team.browse()
            if is_superuser:
                visible = Team.search([("is_callcenter_campaign", "=", True)])
                supervised = visible
            elif is_supervisor:
                supervised = Team.search(
                    [
                        ("is_callcenter_campaign", "=", True),
                        "|",
                        ("primary_supervisor_id", "=", user.id),
                        ("backup_supervisor_ids", "in", user.id),
                    ]
                )
                visible = supervised | user.sudo().callcenter_primary_campaign_id
            elif is_agent:
                visible = user.sudo().callcenter_primary_campaign_id
            else:
                visible = Team.browse()

            user.callcenter_is_operator = is_agent or is_supervisor or is_superuser
            user.callcenter_is_superuser = is_superuser
            user.callcenter_supervised_campaign_ids = supervised
            user.callcenter_visible_campaign_ids = visible

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
            self.env["ir.access"]._clear_caches()
        return result

    def _sync_callcenter_agent_assignment(self):
        Assignment = self.env["callcenter.campaign.assignment"].sudo()
        effective_at = self.env.context.get("callcenter_effective_at") or fields.Datetime.now()
        actor_id = self.env.context.get("callcenter_actor_id") or self.env.user.id

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
                stale.with_context(skip_callcenter_assignment_sync=True).action_close(
                    effective_at
                )

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
                            "date_from": effective_at,
                            "assigned_by_id": actor_id,
                        }
                    )

    def action_assign_callcenter_campaign(self, campaign, effective_at=None):
        if not self._callcenter_can_manage_hierarchy():
            raise AccessError(
                _("Only the Call Center Super User or a technical administrator may transfer agents.")
            )
        if campaign and (
            campaign._name != "crm.team" or not campaign.is_callcenter_campaign
        ):
            raise ValidationError(_("The selected team is not a call-center campaign."))

        actor_id = self.env.user.id
        target = self.sudo().with_context(
            callcenter_actor_id=actor_id,
            callcenter_effective_at=effective_at or fields.Datetime.now(),
        )
        return target.write(
            {"callcenter_primary_campaign_id": campaign.id if campaign else False}
        )
