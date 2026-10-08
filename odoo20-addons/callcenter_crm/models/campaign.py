from odoo import Command, api, fields, models, _
from odoo.exceptions import AccessError, ValidationError


CALLCENTER_CAMPAIGN_CONFIG_FIELDS = {
    "name", "code", "company_id", "campaign_type", "start_at", "end_at",
    "primary_supervisor_id", "backup_supervisor_ids", "client_id", "description",
}


class CallCenterCampaign(models.Model):
    _name = "callcenter.campaign"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "Call Center Campaign"
    _order = "code, id"

    name = fields.Char(required=True, tracking=True)
    code = fields.Char(required=True, index=True, copy=False, tracking=True)
    crm_team_id = fields.Many2one(
        "crm.team", required=True, readonly=True, index=True, ondelete="restrict"
    )
    company_id = fields.Many2one(
        "res.company", required=True, index=True, default=lambda self: self.env.company
    )
    campaign_type = fields.Selection(
        [("outbound", "Outbound"), ("inbound", "Inbound"), ("blended", "Blended")],
        required=True, default="outbound", tracking=True,
    )
    state = fields.Selection(
        [("draft", "Draft"), ("ready", "Ready"), ("active", "Active"),
         ("paused", "Paused"), ("closed", "Closed")],
        required=True, default="draft", index=True, tracking=True,
    )
    start_at = fields.Datetime(tracking=True)
    end_at = fields.Datetime(tracking=True)
    primary_supervisor_id = fields.Many2one(
        "res.users", domain=[("share", "=", False)], ondelete="restrict", tracking=True
    )
    backup_supervisor_ids = fields.Many2many(
        "res.users", "callcenter_campaign_backup_supervisor_rel",
        "campaign_id", "user_id", domain=[("share", "=", False)], tracking=True,
    )
    client_id = fields.Many2one("res.partner", ondelete="restrict", tracking=True)
    description = fields.Text()
    assignment_ids = fields.One2many(
        "callcenter.campaign.assignment", "campaign_id", context={"active_test": False}
    )
    state_log_ids = fields.One2many("callcenter.campaign.state.log", "campaign_id", readonly=True)
    active = fields.Boolean(default=True, index=True)

    agent_count = fields.Integer(compute="_compute_counts")
    lead_count = fields.Integer(compute="_compute_counts")
    available_lead_count = fields.Integer(compute="_compute_counts")
    assigned_lead_count = fields.Integer(compute="_compute_counts")

    _unique_code = models.UniqueIndex("(code)", "Campaign code must be unique.")
    _unique_team = models.UniqueIndex("(crm_team_id)", "Each call-center campaign must have its own native CRM team.")
    _valid_dates = models.Constraint(
        "CHECK(end_at IS NULL OR start_at IS NULL OR end_at > start_at)",
        "Campaign end time must be later than its start time.",
    )

    def _can_manage(self):
        return (
            self.env.is_superuser()
            or self.env.user.has_group("callcenter_crm.group_callcenter_superuser")
        )

    @api.model_create_multi
    def create(self, vals_list):
        if not self._can_manage():
            raise AccessError(_("Only the Call Center Super User may create campaigns."))
        records = self.browse()
        for incoming in vals_list:
            vals = dict(incoming)
            if vals.get("crm_team_id"):
                raise ValidationError(_("The native CRM team is created automatically."))
            vals["code"] = (vals.get("code") or "").strip().upper()
            if self.sudo().with_context(active_test=False).search_count([
                ("code", "=", vals["code"])
            ]):
                raise ValidationError(_("Campaign code must be unique."))
            company_id = vals.get("company_id") or self.env.company.id
            primary_id = vals.get("primary_supervisor_id") or False
            with self.env.cr.savepoint():
                team = self.env["crm.team"].sudo().create({
                    "name": vals.get("name"),
                    "company_id": company_id,
                    "user_id": primary_id,
                    "use_leads": True,
                    "assignment_optout": True,
                })
                vals["crm_team_id"] = team.id
                record = super(CallCenterCampaign, self).create(vals)
                team.sudo().write({
                    "cc_campaign_id": record.id,
                    "assignment_optout": True,
                })
                record._sync_admin_assignments()
                record._validate_supervisors()
                record._sync_supervisor_assignments()
                records |= record
        return records

    def write(self, vals):
        if not self._can_manage():
            raise AccessError(_("Only the Call Center Super User may modify campaigns."))
        if ("state" in vals or "active" in vals) and not self.env.is_superuser():
            raise AccessError(_("Use the campaign lifecycle actions instead of writing state or archive flags directly."))
        if (
            not self.env.is_superuser()
            and CALLCENTER_CAMPAIGN_CONFIG_FIELDS.intersection(vals)
            and any(campaign.state == "closed" for campaign in self)
        ):
            raise AccessError(_("Closed campaign configuration is immutable; duplicate the campaign for a new run."))
        vals = dict(vals)
        if "code" in vals:
            vals["code"] = (vals["code"] or "").strip().upper()
            if self.sudo().with_context(active_test=False).search_count([
                ("id", "not in", self.ids), ("code", "=", vals["code"])
            ]):
                raise ValidationError(_("Campaign code must be unique."))
        result = super().write(vals)
        self._validate_supervisors()
        sync_fields = {"name", "primary_supervisor_id", "company_id"}
        if sync_fields.intersection(vals):
            self._sync_native_team()
        if {"primary_supervisor_id", "backup_supervisor_ids"}.intersection(vals):
            self._sync_supervisor_assignments()
        return result

    def unlink(self):
        if not self.env.is_superuser():
            raise AccessError(_("Campaigns are archived, not physically deleted."))
        return super().unlink()

    @api.constrains("primary_supervisor_id", "backup_supervisor_ids")
    def _check_supervisors(self):
        self._validate_supervisors()

    def _validate_supervisors(self):
        supervisor_group = self.env.ref("callcenter_crm.group_callcenter_supervisor")
        for campaign in self:
            if campaign.primary_supervisor_id:
                if not campaign.primary_supervisor_id.active:
                    raise ValidationError(_("The primary supervisor must be an active user."))
                if supervisor_group not in campaign.primary_supervisor_id.sudo().all_group_ids:
                    raise ValidationError(_("The primary supervisor must have the Call Center Supervisor group."))
            if campaign.primary_supervisor_id and campaign.primary_supervisor_id in campaign.backup_supervisor_ids:
                raise ValidationError(_("The primary supervisor cannot also be a backup supervisor."))
            invalid = campaign.backup_supervisor_ids.filtered(
                lambda u: not u.active or supervisor_group not in u.sudo().all_group_ids
            )
            if invalid:
                raise ValidationError(_("Every backup supervisor must be active and have the Call Center Supervisor group."))

    def _sync_native_team(self):
        for campaign in self:
            campaign.crm_team_id.sudo().write({
                "name": campaign.name,
                "company_id": campaign.company_id.id,
                "user_id": campaign.primary_supervisor_id.id or False,
                "use_leads": True,
                "assignment_optout": True,
            })

    def _sync_supervisor_assignments(self):
        Assignment = self.env["callcenter.campaign.assignment"].sudo().with_context(active_test=False)
        now = fields.Datetime.now()
        for campaign in self:
            desired = {}
            if campaign.primary_supervisor_id:
                desired[campaign.primary_supervisor_id.id] = True
            for user in campaign.backup_supervisor_ids:
                desired[user.id] = False
            current = Assignment.search([
                ("campaign_id", "=", campaign.id), ("role", "=", "supervisor"), ("active", "=", True)
            ])
            for row in current:
                expected = desired.get(row.user_id.id)
                if expected is None or expected != row.is_primary:
                    row.action_close(now)
            for user_id, is_primary in desired.items():
                if not Assignment.search_count([
                    ("campaign_id", "=", campaign.id), ("user_id", "=", user_id),
                    ("role", "=", "supervisor"), ("is_primary", "=", is_primary), ("active", "=", True)
                ]):
                    Assignment.create({
                        "campaign_id": campaign.id, "user_id": user_id,
                        "role": "supervisor", "is_primary": is_primary,
                        "date_from": now, "assigned_by_id": self.env.user.id,
                    })
            campaign._sync_native_team()

    def action_assign_agent(self, user, effective_at=None):
        self.ensure_one()
        if not self._can_manage():
            raise AccessError(_("Only call-center administration may assign or transfer agents."))
        if self.state == "closed" or not self.active:
            raise ValidationError(_("Closed or archived campaigns cannot receive new agent assignments."))
        if not user or user._name != "res.users" or len(user) != 1:
            raise ValidationError(_("A single internal user is required."))
        if not user.active or user.share or not user.has_group("callcenter_crm.group_callcenter_agent"):
            raise ValidationError(_("The selected user must be an active Call Center Agent."))
        when = effective_at or fields.Datetime.now()
        Assignment = self.env["callcenter.campaign.assignment"].sudo().with_context(active_test=False)
        existing = Assignment.search([
            ("user_id", "=", user.id), ("role", "=", "agent"), ("active", "=", True)
        ])
        if existing:
            existing.action_close(when)
        return Assignment.create({
            "campaign_id": self.id, "user_id": user.id, "role": "agent",
            "is_primary": True, "date_from": when, "assigned_by_id": self.env.user.id,
        })

    def action_remove_agent(self, user, effective_at=None):
        self.ensure_one()
        if not self._can_manage():
            raise AccessError(_("Only call-center administration may remove agents."))
        Assignment = self.env["callcenter.campaign.assignment"].sudo().with_context(active_test=False)
        rows = Assignment.search([
            ("campaign_id", "=", self.id), ("user_id", "=", user.id),
            ("role", "=", "agent"), ("active", "=", True)
        ])
        if rows:
            rows.action_close(effective_at or fields.Datetime.now())
        return True

    def _sync_admin_assignments(self):
        Assignment = self.env["callcenter.campaign.assignment"].sudo().with_context(active_test=False)
        admin_group = self.env.ref("callcenter_crm.group_callcenter_superuser")
        system_group = self.env.ref("base.group_system")
        admins = self.env["res.users"].sudo().search([
            ("active", "=", True),
            "|", ("all_group_ids", "in", admin_group.id), ("all_group_ids", "in", system_group.id),
        ])
        now = fields.Datetime.now()
        for campaign in self:
            current = Assignment.search([
                ("campaign_id", "=", campaign.id), ("role", "=", "admin"), ("active", "=", True)
            ])
            stale = current.filtered(lambda a: a.user_id not in admins)
            if stale:
                stale.action_close(now)
            current_users = current.filtered("active").user_id
            for user in admins - current_users:
                Assignment.create({
                    "campaign_id": campaign.id, "user_id": user.id, "role": "admin",
                    "is_primary": False, "date_from": now, "assigned_by_id": self.env.user.id,
                })

    def _validate_ready(self):
        for campaign in self:
            errors = []
            if not campaign.code:
                errors.append(_("campaign code"))
            if not campaign.primary_supervisor_id:
                errors.append(_("primary supervisor"))
            if not campaign.start_at:
                errors.append(_("start date"))
            if not campaign.end_at:
                errors.append(_("end date"))
            active_agents = campaign.assignment_ids.filtered(
                lambda a: a.active and a.role == "agent"
            )
            if not active_agents:
                errors.append(_("at least one active agent"))
            if active_agents.filtered(lambda a: not a.user_id.active):
                errors.append(_("all assigned agents must be active users"))
            if errors:
                raise ValidationError(_("Campaign is not ready: %s", ", ".join(errors)))
            campaign._validate_supervisors()

    def _transition(self, to_state, reason=None, bypass_auth=False):
        if not bypass_auth and not self._can_manage():
            raise AccessError(_("Only the Call Center Super User may perform campaign lifecycle transitions."))
        if to_state in {"paused", "closed"} and not str(reason or "").strip():
            raise ValidationError(_("Pause and Close transitions require an audit reason."))
        allowed = {
            "draft": {"ready"},
            "ready": {"draft", "active"},
            "active": {"paused", "closed"},
            "paused": {"active", "closed"},
            "closed": set(),
        }
        actor_id = self.env.user.id
        now = fields.Datetime.now()
        for campaign in self:
            if to_state not in allowed[campaign.state]:
                raise ValidationError(
                    _("Invalid campaign transition: %(from_state)s → %(to_state)s",
                      from_state=campaign.state, to_state=to_state)
                )
            if to_state in {"ready", "active"}:
                campaign._validate_ready()
            old = campaign.state
            super(CallCenterCampaign, campaign.sudo()).write({"state": to_state})
            self.env["callcenter.campaign.state.log"].sudo().create({
                "campaign_id": campaign.id,
                "from_state": old,
                "to_state": to_state,
                "changed_by_id": actor_id,
                "changed_at": now,
                "reason": str(reason or "").strip() or False,
            })
            if to_state == "closed":
                campaign.assignment_ids.filtered(
                    lambda a: a.active and a.role in {"agent", "supervisor"}
                ).sudo().action_close(now)
        return True

    def action_ready(self):
        return self._transition("ready", _("Campaign marked ready"))

    def action_back_to_draft(self):
        return self._transition("draft", _("Campaign returned to draft"))

    def action_activate(self):
        return self._transition("active", _("Campaign activated"))

    def action_pause(self, reason=None):
        return self._transition("paused", reason)

    def action_resume(self):
        return self._transition("active", _("Campaign resumed"))

    def action_close(self, reason=None):
        return self._transition("closed", reason)

    def action_archive(self, reason=None):
        if not self._can_manage():
            raise AccessError(_("Only the Call Center Super User may archive campaigns."))
        if not str(reason or "").strip():
            raise ValidationError(_("Archiving requires an audit reason."))
        actor_id = self.env.user.id
        now = fields.Datetime.now()
        for campaign in self:
            if campaign.state != "closed":
                raise ValidationError(_("Only closed campaigns may be archived."))
            if campaign.active:
                super(CallCenterCampaign, campaign.sudo()).write({"active": False})
                campaign.crm_team_id.sudo().write({"active": False})
                self.env["callcenter.campaign.state.log"].sudo().create({
                    "campaign_id": campaign.id,
                    "from_state": "closed",
                    "to_state": "archived",
                    "changed_by_id": actor_id,
                    "changed_at": now,
                    "reason": str(reason).strip(),
                })
        return True

    def _open_lifecycle_wizard(self, operation):
        self.ensure_one()
        if not self._can_manage():
            raise AccessError(_("Only the Call Center Super User may perform campaign lifecycle transitions."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Campaign Lifecycle"),
            "res_model": "callcenter.campaign.lifecycle.wizard",
            "view_mode": "form",
            "view_id": self.env.ref(
                "callcenter_crm.view_callcenter_campaign_lifecycle_wizard_form"
            ).id,
            "target": "new",
            "context": {
                "default_campaign_id": self.id,
                "default_operation": operation,
            },
        }

    def action_open_pause_wizard(self):
        self.ensure_one()
        if self.state != "active":
            raise ValidationError(_("Only an Active campaign can be paused."))
        return self._open_lifecycle_wizard("pause")

    def action_open_close_wizard(self):
        self.ensure_one()
        if self.state not in {"active", "paused"}:
            raise ValidationError(_("Only Active or Paused campaigns can be closed."))
        return self._open_lifecycle_wizard("close")

    def action_open_archive_wizard(self):
        self.ensure_one()
        if self.state != "closed" or not self.active:
            raise ValidationError(_("Only a Closed campaign can be archived."))
        return self._open_lifecycle_wizard("archive")

    def action_open_duplicate_wizard(self):
        self.ensure_one()
        if self.state != "closed":
            raise ValidationError(_("Only a Closed campaign can be duplicated as a new run."))
        return self._open_lifecycle_wizard("duplicate")

    def action_duplicate_as_draft(self, new_code):
        self.ensure_one()
        if not self._can_manage():
            raise AccessError(_("Only the Call Center Super User may duplicate campaigns."))
        if self.state != "closed":
            raise ValidationError(_("Only a Closed campaign can be duplicated as a new run."))
        code = str(new_code or "").strip().upper()
        if not code:
            raise ValidationError(_("A new Campaign Code is required."))
        return self.create({
            "name": self.name,
            "code": code,
            "company_id": self.company_id.id,
            "campaign_type": self.campaign_type,
            "primary_supervisor_id": self.primary_supervisor_id.id or False,
            "backup_supervisor_ids": [Command.set(self.backup_supervisor_ids.ids)],
            "client_id": self.client_id.id or False,
            "description": self.description,
            "start_at": False,
            "end_at": False,
        })

    @api.model
    def _cron_pause_expired_campaigns(self):
        now = fields.Datetime.now()
        campaigns = self.sudo().search([
            ("active", "=", True), ("state", "=", "active"), ("end_at", "<=", now)
        ])
        for campaign in campaigns:
            campaign._transition(
                "paused", _("Scheduled campaign end reached"), bypass_auth=True
            )
        return True

    def _compute_counts(self):
        Lead = self.env["crm.lead"].sudo()
        Assignment = self.env["callcenter.campaign.assignment"].sudo()
        for campaign in self:
            campaign.agent_count = Assignment.search_count([
                ("campaign_id", "=", campaign.id), ("role", "=", "agent"), ("active", "=", True)
            ])
            campaign.lead_count = Lead.search_count([("cc_campaign_id", "=", campaign.id)])
            campaign.available_lead_count = Lead.search_count([
                ("cc_campaign_id", "=", campaign.id), ("queue_state", "=", "available"), ("active", "=", True)
            ])
            campaign.assigned_lead_count = Lead.search_count([
                ("cc_campaign_id", "=", campaign.id), ("queue_state", "=", "assigned"), ("active", "=", True)
            ])
