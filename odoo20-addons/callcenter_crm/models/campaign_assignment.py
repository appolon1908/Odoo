from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError


class CallCenterCampaignAssignment(models.Model):
    _name = "callcenter.campaign.assignment"
    _description = "Call Center Campaign Assignment"
    _order = "date_from desc, id desc"

    campaign_id = fields.Many2one(
        "callcenter.campaign", required=True, index=True, ondelete="restrict"
    )
    user_id = fields.Many2one(
        "res.users", required=True, index=True, ondelete="restrict",
        domain=[("share", "=", False)],
    )
    role = fields.Selection(
        [("admin", "Admin"), ("supervisor", "Supervisor"), ("agent", "Agent")],
        required=True, index=True,
    )
    is_primary = fields.Boolean(default=False, index=True)
    active = fields.Boolean(default=True, index=True)
    date_from = fields.Datetime(required=True, default=fields.Datetime.now, index=True)
    date_to = fields.Datetime(index=True)
    assigned_by_id = fields.Many2one(
        "res.users", required=True, readonly=True, ondelete="restrict",
        default=lambda self: self.env.user,
    )

    _one_active_agent = models.UniqueIndex(
        "(user_id) WHERE active AND role = 'agent'",
        "An agent may have only one active call-center campaign.",
    )
    _one_primary_supervisor = models.UniqueIndex(
        "(campaign_id) WHERE active AND role = 'supervisor' AND is_primary",
        "A campaign may have only one active primary supervisor.",
    )
    _one_active_admin = models.UniqueIndex(
        "(campaign_id, user_id) WHERE active AND role = 'admin'",
        "The same administrator cannot have duplicate active campaign assignments.",
    )
    _one_active_backup = models.UniqueIndex(
        "(campaign_id, user_id) WHERE active AND role = 'supervisor' AND NOT is_primary",
        "The same backup supervisor cannot be assigned twice.",
    )

    def _can_manage(self):
        return (
            self.env.is_superuser()
            or self.env.user.has_group("base.group_system")
            or self.env.user.has_group("callcenter_crm.group_callcenter_superuser")
        )

    @api.model_create_multi
    def create(self, vals_list):
        if not self._can_manage():
            raise AccessError(_("Only call-center administration may create campaign assignments."))
        for vals in vals_list:
            role = vals.get("role")
            if role == "agent":
                vals["is_primary"] = True
                campaign = self.env["callcenter.campaign"].sudo().browse(vals.get("campaign_id"))
                if not campaign.active or campaign.state == "closed":
                    raise ValidationError(_("Closed or archived campaigns cannot receive active agent assignments."))
                if vals.get("active", True) and self.sudo().search_count([
                    ("user_id", "=", vals.get("user_id")), ("role", "=", "agent"), ("active", "=", True)
                ]):
                    raise ValidationError(_("An agent may have only one active call-center campaign."))
            if role == "supervisor" and vals.get("is_primary") and vals.get("active", True):
                if self.sudo().search_count([
                    ("campaign_id", "=", vals.get("campaign_id")), ("role", "=", "supervisor"),
                    ("is_primary", "=", True), ("active", "=", True)
                ]):
                    raise ValidationError(_("A campaign may have only one active primary supervisor."))
            vals.setdefault("assigned_by_id", self.env.user.id)
        records = super().create(vals_list)
        records._validate_role_groups()
        records.filtered(lambda r: r.active and r.role == "agent")._sync_native_membership(True)
        return records

    def write(self, vals):
        if not self._can_manage():
            raise AccessError(_("Only call-center administration may modify campaign assignments."))
        if "role" in vals and vals["role"] == "agent":
            vals["is_primary"] = True
        was_active_agents = self.filtered(lambda r: r.active and r.role == "agent")
        result = super().write(vals)
        self._validate_role_groups()
        for record in self:
            if record.role == "agent":
                record._sync_native_membership(record.active)
        for record in was_active_agents - self.filtered(lambda r: r.active and r.role == "agent"):
            record._sync_native_membership(False)
        return result

    def unlink(self):
        if not (self.env.is_superuser() or self.env.user.has_group("base.group_system")):
            raise AccessError(_("Campaign assignment history cannot be deleted."))
        return super().unlink()

    def _validate_role_groups(self):
        agent_group = self.env.ref("callcenter_crm.group_callcenter_agent")
        supervisor_group = self.env.ref("callcenter_crm.group_callcenter_supervisor")
        superuser_group = self.env.ref("callcenter_crm.group_callcenter_superuser")
        system_group = self.env.ref("base.group_system")
        for assignment in self:
            groups = assignment.user_id.sudo().all_group_ids
            if not assignment.user_id.active:
                raise ValidationError(_("Inactive users cannot hold active call-center assignments."))
            if assignment.role == "agent" and agent_group not in groups:
                raise ValidationError(_("Agent assignments require the Call Center Agent group."))
            if assignment.role == "supervisor" and supervisor_group not in groups:
                raise ValidationError(_("Supervisor assignments require the Call Center Supervisor group."))
            if assignment.role == "admin" and superuser_group not in groups and system_group not in groups:
                raise ValidationError(_("Admin assignments are reserved for Call Center Super Users and technical administrators."))

    @api.constrains("date_from", "date_to")
    def _check_dates(self):
        for assignment in self:
            if assignment.date_to and assignment.date_to < assignment.date_from:
                raise ValidationError(_("Assignment end time cannot precede its start time."))

    @api.constrains("role", "is_primary")
    def _check_primary_role(self):
        for assignment in self:
            if assignment.role == "agent" and not assignment.is_primary:
                raise ValidationError(_("An active MVP agent assignment is always primary."))
            if assignment.role == "admin" and assignment.is_primary:
                raise ValidationError(_("Admin assignments are not primary/backup supervisor assignments."))

    def _sync_native_membership(self, activate):
        Member = self.env["crm.team.member"].sudo().with_context(active_test=False)
        for assignment in self.filtered(lambda r: r.role == "agent"):
            team = assignment.campaign_id.crm_team_id
            membership = Member.search(
                [("crm_team_id", "=", team.id), ("user_id", "=", assignment.user_id.id)],
                limit=1,
            )
            if activate:
                if membership:
                    if not membership.active:
                        membership.write({"active": True})
                else:
                    Member.create({"crm_team_id": team.id, "user_id": assignment.user_id.id})
            elif membership and membership.active:
                membership.write({"active": False})

    def action_close(self, date_to=None):
        if not self._can_manage():
            raise AccessError(_("Only call-center administration may close campaign assignments."))
        close_at = date_to or fields.Datetime.now()
        for assignment in self.filtered("active"):
            super(CallCenterCampaignAssignment, assignment.sudo()).write(
                {"active": False, "date_to": assignment.date_to or close_at}
            )
            if assignment.role == "agent":
                assignment._sync_native_membership(False)
        return True
