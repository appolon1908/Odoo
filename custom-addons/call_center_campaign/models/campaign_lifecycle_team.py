import re

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError


CALLCENTER_TEAM_CAMPAIGN_STATES = [
    ("draft", "Draft"),
    ("ready", "Ready"),
    ("active", "Active"),
    ("paused", "Paused"),
    ("closed", "Closed"),
]
CALLCENTER_TEAM_TRANSITIONS = {
    "draft": {"ready"},
    "ready": {"draft", "active"},
    "active": {"paused", "closed"},
    "paused": {"active", "closed"},
    "closed": set(),
}
CALLCENTER_CAMPAIGN_CODE = re.compile(r"^[A-Z0-9][A-Z0-9-]{2,63}$")
CALLCENTER_LIFECYCLE_CAPABILITY = object()
CALLCENTER_ASSIGNMENT_CAPABILITY = object()
CALLCENTER_STATE_LOG_CAPABILITY = object()
CALLCENTER_NATIVE_MEMBER_CAPABILITY = object()
CALLCENTER_SUPERVISOR_HISTORY_CAPABILITY = object()
CALLCENTER_CONFIG_FIELDS = {
    "name",
    "user_id",
    "backup_supervisor_ids",
    "campaign_code",
    "campaign_type",
    "start_at",
    "end_at",
    "client_id",
    "description",
    "business_unit_id",
    "default_campaign_id",
    "cc_campaign_id",
    "is_callcenter_campaign",
}
CALLCENTER_OPERATIONAL_GROUPS = (
    "codestra_cc_security.group_cc_campaign_agent",
    "codestra_cc_security.group_cc_senior_agent",
    "codestra_cc_security.group_cc_campaign_supervisor",
)


def _is_callcenter_superuser(user):
    return user.has_group("codestra_cc_security.group_cc_global_administrator")


def _is_operational_user(user):
    return any(user.has_group(xmlid) for xmlid in CALLCENTER_OPERATIONAL_GROUPS)


def _normalize_campaign_code(value):
    value = str(value or "").strip().upper()
    return value or False


def _require_active_internal_user(user, role_label):
    if not user or not user.exists() or not user.active or user.share:
        raise ValidationError(
            _("%(role)s must be an active internal Odoo user.", role=role_label)
        )


def _require_supervisor_user(user):
    _require_active_internal_user(user, _("Supervisor"))
    if not user.has_group("codestra_cc_security.group_cc_campaign_supervisor"):
        raise ValidationError(_("Supervisor assignments require the Supervisor role."))


def _require_agent_user(user):
    _require_active_internal_user(user, _("Agent"))
    if user.has_group("codestra_cc_security.group_cc_campaign_supervisor"):
        raise ValidationError(_("A Supervisor cannot be assigned as a campaign Agent."))
    if not user.has_group("codestra_cc_security.group_cc_campaign_agent"):
        raise ValidationError(_("Campaign assignments require the Agent role."))


class CrmTeamCallCenterCampaign(models.Model):
    _inherit = "crm.team"

    is_callcenter_campaign = fields.Boolean(
        string="Call Center Campaign",
        default=False,
        index=True,
        tracking=True,
    )
    campaign_code = fields.Char(
        string="Campaign Code",
        index=True,
        copy=False,
        tracking=True,
    )
    campaign_type = fields.Selection(
        [
            ("outbound", "Outbound"),
            ("inbound", "Inbound"),
            ("blended", "Blended"),
        ],
        string="Campaign Type",
        default="outbound",
        tracking=True,
    )
    campaign_state = fields.Selection(
        CALLCENTER_TEAM_CAMPAIGN_STATES,
        string="Campaign State",
        default="draft",
        index=True,
        copy=False,
        tracking=True,
    )
    backup_supervisor_ids = fields.Many2many(
        "res.users",
        "crm_team_callcenter_backup_supervisor_rel",
        "team_id",
        "user_id",
        string="Backup Supervisors",
        domain=[("share", "=", False)],
        tracking=True,
    )
    start_at = fields.Datetime(string="Campaign Start", tracking=True)
    end_at = fields.Datetime(string="Campaign End", tracking=True)
    client_id = fields.Many2one(
        "res.partner",
        string="Client",
        ondelete="restrict",
        tracking=True,
    )
    description = fields.Text(tracking=True)
    cc_campaign_id = fields.Many2one(
        "cc.campaign",
        string="Canonical Campaign Workspace",
        ondelete="restrict",
        index=True,
        copy=False,
        help=(
            "Optional link to the canonical Codestra campaign workspace. "
            "The native CRM team remains the operational campaign surface."
        ),
    )
    campaign_assignment_ids = fields.One2many(
        "callcenter.campaign.assignment",
        "campaign_id",
        string="Agent Assignment History",
    )
    campaign_state_log_ids = fields.One2many(
        "callcenter.campaign.state.log",
        "campaign_id",
        string="Campaign State Audit",
        readonly=True,
    )
    supervisor_history_ids = fields.Many2many(
        "res.users",
        "crm_team_callcenter_supervisor_history_rel",
        "team_id",
        "user_id",
        string="Supervisor History",
        readonly=True,
        copy=False,
    )
    callcenter_operational = fields.Boolean(
        compute="_compute_callcenter_operational",
        string="Operational",
    )

    _campaign_code_unique = models.Constraint(
        "UNIQUE(campaign_code)",
        "Call-center campaign codes must be unique.",
    )
    _canonical_campaign_unique = models.Constraint(
        "UNIQUE(cc_campaign_id)",
        "A canonical campaign workspace can be linked to only one CRM team campaign.",
    )

    @api.depends("is_callcenter_campaign", "campaign_state", "active")
    def _compute_callcenter_operational(self):
        for team in self:
            team.callcenter_operational = bool(
                team.is_callcenter_campaign
                and team.active
                and team.campaign_state == "active"
            )

    def _require_callcenter_superuser(self):
        if not _is_callcenter_superuser(self.env.user):
            raise AccessError(
                _("Only the Call Center Super User may configure campaigns.")
            )

    @api.model_create_multi
    def create(self, values_list):
        prepared = []
        for original in values_list:
            values = dict(original)
            if values.get("is_callcenter_campaign"):
                self._require_callcenter_superuser()
                if values.get("campaign_state", "draft") != "draft":
                    raise ValidationError(
                        _("Call-center campaigns must be created in Draft.")
                    )
                if values.get("active") is False:
                    raise ValidationError(
                        _("A new call-center campaign cannot start archived.")
                    )
                if values.get("member_ids"):
                    raise AccessError(
                        _(
                            "Use callcenter.campaign.assignment; "
                            "native team membership is synchronized automatically."
                        )
                    )
                if "campaign_code" in values:
                    values["campaign_code"] = _normalize_campaign_code(
                        values["campaign_code"]
                    )
                if values.get("cc_campaign_id") and not values.get(
                    "default_campaign_id"
                ):
                    canonical = self.env["cc.campaign"].browse(
                        values["cc_campaign_id"]
                    ).exists()
                    if canonical:
                        values["default_campaign_id"] = (
                            canonical.legacy_campaign_id.id
                        )
                values.setdefault("active", True)
            prepared.append(values)
        teams = super().create(prepared)
        governed = teams.filtered("is_callcenter_campaign")
        governed._check_callcenter_configuration()
        governed._sync_callcenter_supervisor_history()
        return teams

    def write(self, values):
        callcenter = self.filtered("is_callcenter_campaign")
        becoming_callcenter = bool(values.get("is_callcenter_campaign"))
        governed = callcenter or (self if becoming_callcenter else self.env["crm.team"])
        if governed:
            if CALLCENTER_CONFIG_FIELDS.intersection(values):
                self._require_callcenter_superuser()
            if values.get("is_callcenter_campaign") is False and callcenter:
                raise AccessError(
                    _("A governed call-center campaign cannot be converted back to a normal team.")
                )
            if "supervisor_history_ids" in values and self.env.context.get(
                "_callcenter_supervisor_history_capability"
            ) is not CALLCENTER_SUPERVISOR_HISTORY_CAPABILITY:
                raise AccessError(_("Supervisor history is system-maintained."))
            if "member_ids" in values and self.env.context.get(
                "_callcenter_native_member_capability"
            ) is not CALLCENTER_NATIVE_MEMBER_CAPABILITY:
                raise AccessError(
                    _(
                        "Campaign agents are changed through "
                        "callcenter.campaign.assignment, not member_ids."
                    )
                )
            if "campaign_state" in values and self.env.context.get(
                "_callcenter_lifecycle_capability"
            ) is not CALLCENTER_LIFECYCLE_CAPABILITY:
                raise AccessError(
                    _("Campaign state changes require a lifecycle action.")
                )
            if "active" in values and self.env.context.get(
                "_callcenter_lifecycle_capability"
            ) is not CALLCENTER_LIFECYCLE_CAPABILITY:
                if any(team.active != bool(values["active"]) for team in callcenter):
                    raise AccessError(
                        _("Archive/unarchive must use the governed campaign lifecycle.")
                    )
            if (
                CALLCENTER_CONFIG_FIELDS.intersection(values)
                and self.env.context.get("_callcenter_lifecycle_capability")
                is not CALLCENTER_LIFECYCLE_CAPABILITY
                and any(team.campaign_state == "closed" for team in callcenter)
            ):
                raise AccessError(
                    _("Closed campaigns are immutable; duplicate them for a new run.")
                )
            if "campaign_code" in values:
                values = dict(values)
                values["campaign_code"] = _normalize_campaign_code(
                    values["campaign_code"]
                )
            if values.get("cc_campaign_id") and not values.get(
                "default_campaign_id"
            ):
                canonical = self.env["cc.campaign"].browse(
                    values["cc_campaign_id"]
                ).exists()
                if canonical:
                    values = dict(values)
                    values["default_campaign_id"] = canonical.legacy_campaign_id.id
        supervisor_scope_changed = bool(
            {"user_id", "backup_supervisor_ids"}.intersection(values)
        )
        result = super().write(values)
        governed._check_callcenter_configuration()
        if supervisor_scope_changed:
            governed._sync_callcenter_supervisor_history()
        return result

    def _sync_callcenter_supervisor_history(self):
        for team in self.filtered("is_callcenter_campaign"):
            history = team.supervisor_history_ids | team.backup_supervisor_ids
            if team.user_id:
                history |= team.user_id
            team.with_context(
                _callcenter_supervisor_history_capability=(
                    CALLCENTER_SUPERVISOR_HISTORY_CAPABILITY
                )
            ).write({"supervisor_history_ids": [(6, 0, history.ids)]})

    def unlink(self):
        if any(self.mapped("is_callcenter_campaign")):
            raise AccessError(
                _("Call-center campaigns are retained and archived, not deleted.")
            )
        return super().unlink()

    def copy(self, default=None):
        if self.is_callcenter_campaign:
            self._require_callcenter_superuser()
            defaults = dict(default or {})
            defaults.update(
                {
                    "campaign_code": False,
                    "campaign_state": "draft",
                    "active": True,
                    "user_id": False,
                    "backup_supervisor_ids": [(5, 0, 0)],
                    "start_at": False,
                    "end_at": False,
                    "cc_campaign_id": False,
                    "default_campaign_id": False,
                }
            )
            return super().copy(defaults)
        return super().copy(default)

    @api.constrains(
        "is_callcenter_campaign",
        "campaign_code",
        "campaign_type",
        "campaign_state",
        "user_id",
        "backup_supervisor_ids",
        "start_at",
        "end_at",
        "active",
        "cc_campaign_id",
        "default_campaign_id",
    )
    def _check_callcenter_configuration(self):
        for team in self.filtered("is_callcenter_campaign"):
            code = _normalize_campaign_code(team.campaign_code)
            if code and not CALLCENTER_CAMPAIGN_CODE.fullmatch(code):
                raise ValidationError(
                    _(
                        "Campaign codes must contain only uppercase letters, "
                        "numbers, and hyphens."
                    )
                )
            if team.start_at and team.end_at and team.end_at <= team.start_at:
                raise ValidationError(
                    _("Campaign end time must be later than its start time.")
                )
            if team.user_id:
                _require_supervisor_user(team.user_id)
            if team.user_id and team.user_id in team.backup_supervisor_ids:
                raise ValidationError(
                    _("The Primary Supervisor cannot also be a Backup Supervisor.")
                )
            for supervisor in team.backup_supervisor_ids:
                _require_supervisor_user(supervisor)
            if not team.active and team.campaign_state != "closed":
                raise ValidationError(
                    _("Only a Closed campaign may be archived with active=False.")
                )
            if (
                team.cc_campaign_id
                and team.default_campaign_id
                and team.cc_campaign_id.legacy_campaign_id
                != team.default_campaign_id
            ):
                raise ValidationError(
                    _(
                        "The canonical campaign and legacy default campaign "
                        "must describe the same campaign."
                    )
                )

    def _validate_ready_requirements(self):
        for team in self:
            if not team.is_callcenter_campaign:
                raise ValidationError(_("This CRM team is not a call-center campaign."))
            if not team.active:
                raise ValidationError(_("An archived campaign cannot become Ready."))
            code = _normalize_campaign_code(team.campaign_code)
            if not code:
                raise ValidationError(_("Campaign Code is required before Ready."))
            if not CALLCENTER_CAMPAIGN_CODE.fullmatch(code):
                raise ValidationError(_("Campaign Code is invalid."))
            if not team.campaign_type:
                raise ValidationError(_("Campaign Type is required before Ready."))
            if not team.user_id:
                raise ValidationError(
                    _("A Primary Supervisor is required before Ready.")
                )
            _require_supervisor_user(team.user_id)
            for supervisor in team.backup_supervisor_ids:
                _require_supervisor_user(supervisor)
            if team.start_at and team.end_at and team.end_at <= team.start_at:
                raise ValidationError(_("Campaign dates are invalid."))

            assignments = team.campaign_assignment_ids.filtered("active")
            if not assignments:
                raise ValidationError(
                    _("At least one active Agent assignment is required before Ready.")
                )
            for assignment in assignments:
                _require_agent_user(assignment.user_id)
                conflict = self.env["callcenter.campaign.assignment"].sudo().search(
                    [
                        ("user_id", "=", assignment.user_id.id),
                        ("active", "=", True),
                        ("is_primary", "=", True),
                        ("campaign_id", "!=", team.id),
                    ],
                    limit=1,
                )
                if assignment.is_primary and conflict:
                    raise ValidationError(
                        _(
                            "%(agent)s already has another primary active campaign.",
                            agent=assignment.user_id.display_name,
                        )
                    )
            if set(team.member_ids.ids) != set(assignments.mapped("user_id").ids):
                raise ValidationError(
                    _("Native CRM team membership is not synchronized with assignments.")
                )

            if team.cc_campaign_id:
                expected = {
                    team.user_id.id: "supervisor",
                    **{
                        supervisor.id: "supervisor"
                        for supervisor in team.backup_supervisor_ids
                    },
                    **{
                        assignment.user_id.id: "agent"
                        for assignment in assignments
                    },
                }
                for user_id, expected_role in expected.items():
                    domain = [
                        ("user_id", "=", user_id),
                        ("campaign_id", "=", team.cc_campaign_id.id),
                        ("state", "=", "active"),
                    ]
                    if expected_role == "supervisor":
                        domain.append(("role", "=", "supervisor"))
                    else:
                        domain.append(("role", "in", ("agent", "senior_agent")))
                    if not self.env["cc.campaign.membership"].sudo().search(
                        domain, limit=1
                    ):
                        raise ValidationError(
                            _(
                                "Canonical campaign membership is not active for %(user)s.",
                                user=self.env["res.users"].browse(user_id).display_name,
                            )
                        )
        return True

    def _log_campaign_transition(self, from_state, to_state, reason):
        self.ensure_one()
        self.env["callcenter.campaign.state.log"].sudo().with_context(
            _callcenter_state_log_capability=CALLCENTER_STATE_LOG_CAPABILITY
        ).create(
            {
                "campaign_id": self.id,
                "from_state": from_state,
                "to_state": to_state,
                "changed_by_id": self.env.user.id,
                "changed_at": fields.Datetime.now(),
                "reason": reason or False,
            }
        )

    def _transition_callcenter_campaign(self, target_state, reason=None):
        self._require_callcenter_superuser()
        for team in self:
            if not team.is_callcenter_campaign:
                raise ValidationError(_("This CRM team is not a call-center campaign."))
            source = team.campaign_state
            if target_state not in CALLCENTER_TEAM_TRANSITIONS.get(source, set()):
                raise ValidationError(
                    _(
                        "Campaign transition %(source)s → %(target)s is not allowed.",
                        source=source,
                        target=target_state,
                    )
                )
            if target_state in {"ready", "active"}:
                team._validate_ready_requirements()
            if target_state in {"paused", "closed"} and not str(reason or "").strip():
                raise ValidationError(
                    _("Pause and Close transitions require an audit reason.")
                )
            team.with_context(
                _callcenter_lifecycle_capability=CALLCENTER_LIFECYCLE_CAPABILITY
            ).write(
                {
                    "campaign_state": target_state,
                    "active": True,
                }
            )
            if target_state == "closed":
                team._close_campaign_assignments()
            team._log_campaign_transition(source, target_state, reason)
        return True

    def _close_campaign_assignments(self):
        now = fields.Datetime.now()
        for team in self:
            active_assignments = team.campaign_assignment_ids.filtered("active")
            if active_assignments:
                active_assignments.with_context(
                    _callcenter_assignment_capability=CALLCENTER_ASSIGNMENT_CAPABILITY
                ).write({"active": False, "date_to": now})
            if team.cc_campaign_id:
                memberships = self.env["cc.campaign.membership"].sudo().search(
                    [
                        ("campaign_id", "=", team.cc_campaign_id.id),
                        ("state", "=", "active"),
                        ("role", "in", ("agent", "senior_agent", "supervisor")),
                    ]
                )
                if memberships:
                    memberships.with_user(self.env.user).action_revoke()

    def action_callcenter_ready(self):
        return self._transition_callcenter_campaign("ready", _("Validated for Ready"))

    def action_callcenter_return_draft(self):
        return self._transition_callcenter_campaign(
            "draft", _("Returned to Draft for configuration")
        )

    def action_callcenter_activate(self):
        return self._transition_callcenter_campaign(
            "active", _("Campaign activated")
        )

    def _callcenter_transition_wizard(self, target_state):
        self.ensure_one()
        self._require_callcenter_superuser()
        return {
            "type": "ir.actions.act_window",
            "name": _("Campaign Transition"),
            "res_model": "callcenter.campaign.transition.wizard",
            "view_mode": "form",
            "view_id": self.env.ref(
                "call_center_campaign.view_callcenter_campaign_transition_wizard_form"
            ).id,
            "target": "new",
            "context": {
                "default_campaign_id": self.id,
                "default_target_state": target_state,
            },
        }

    def action_open_callcenter_pause_wizard(self):
        if self.campaign_state != "active":
            raise ValidationError(_("Only an Active campaign can be paused."))
        return self._callcenter_transition_wizard("paused")

    def action_callcenter_pause(self, reason=None):
        return self._transition_callcenter_campaign("paused", reason)

    def action_callcenter_resume(self):
        return self._transition_callcenter_campaign(
            "active", _("Campaign resumed")
        )

    def action_open_callcenter_close_wizard(self):
        if self.campaign_state not in {"active", "paused"}:
            raise ValidationError(_("Only an Active or Paused campaign can be closed."))
        return self._callcenter_transition_wizard("closed")

    def action_callcenter_close(self, reason=None):
        return self._transition_callcenter_campaign("closed", reason)

    def action_open_callcenter_archive_wizard(self):
        if self.campaign_state != "closed" or not self.active:
            raise ValidationError(_("Only an active Closed campaign can be archived."))
        return self._callcenter_transition_wizard("archived")

    def action_callcenter_archive(self, reason=None):
        self._require_callcenter_superuser()
        if not str(reason or "").strip():
            raise ValidationError(_("Archiving requires an audit reason."))
        for team in self:
            if (
                not team.is_callcenter_campaign
                or team.campaign_state != "closed"
                or not team.active
            ):
                raise ValidationError(
                    _("Only an active Closed campaign can be archived.")
                )
            team.with_context(
                _callcenter_lifecycle_capability=CALLCENTER_LIFECYCLE_CAPABILITY
            ).write({"active": False})
            team._log_campaign_transition("closed", "archived", reason)
        return True

    def action_duplicate_callcenter_campaign(self, new_code=None):
        self.ensure_one()
        self._require_callcenter_superuser()
        if self.campaign_state != "closed":
            raise ValidationError(_("Only Closed campaigns use the governed duplicate path."))
        duplicate = self.copy()
        if new_code:
            duplicate.write({"campaign_code": new_code})
        return duplicate

    def action_duplicate_callcenter_campaign_ui(self):
        duplicate = self.action_duplicate_callcenter_campaign()
        return {
            "type": "ir.actions.act_window",
            "name": _("New Campaign Draft"),
            "res_model": "crm.team",
            "res_id": duplicate.id,
            "view_mode": "form",
            "view_id": self.env.ref(
                "call_center_campaign.view_callcenter_crm_team_campaign_form"
            ).id,
            "target": "current",
        }

    def action_assign_callcenter_agent(
        self, user_id, is_primary=True, date_from=None
    ):
        self.ensure_one()
        self._require_callcenter_superuser()
        if self.campaign_state == "closed" or not self.active:
            raise AccessError(_("Closed or archived campaigns cannot receive agents."))
        return self.env["callcenter.campaign.assignment"].create(
            {
                "campaign_id": self.id,
                "user_id": user_id,
                "is_primary": bool(is_primary),
                "date_from": date_from or fields.Datetime.now(),
                "active": True,
            }
        )


class CallCenterCampaignAssignment(models.Model):
    _name = "callcenter.campaign.assignment"
    _description = "Call Center Campaign Assignment History"
    _order = "date_from desc, id desc"

    campaign_id = fields.Many2one(
        "crm.team",
        required=True,
        ondelete="restrict",
        index=True,
    )
    user_id = fields.Many2one(
        "res.users",
        required=True,
        ondelete="restrict",
        index=True,
    )
    is_primary = fields.Boolean(default=True, required=True, index=True)
    active = fields.Boolean(default=True, required=True, index=True)
    date_from = fields.Datetime(
        required=True,
        default=fields.Datetime.now,
        index=True,
    )
    date_to = fields.Datetime(index=True)
    created_by_id = fields.Many2one(
        "res.users",
        required=True,
        default=lambda self: self.env.user,
        ondelete="restrict",
        readonly=True,
    )

    _one_active_assignment_per_team = models.UniqueIndex(
        "(campaign_id, user_id) WHERE active IS TRUE",
        "An agent may have only one active assignment in a campaign.",
    )
    _one_primary_active_campaign = models.UniqueIndex(
        "(user_id) WHERE active IS TRUE AND is_primary IS TRUE",
        "An agent may have only one primary active campaign.",
    )

    def _require_superuser(self):
        if not _is_callcenter_superuser(self.env.user):
            raise AccessError(
                _("Only the Call Center Super User may change Agent assignments.")
            )

    @api.model_create_multi
    def create(self, values_list):
        self._require_superuser()
        prepared = []
        for original in values_list:
            values = dict(original)
            campaign = self.env["crm.team"].browse(
                values.get("campaign_id")
            ).exists()
            user = self.env["res.users"].browse(values.get("user_id")).exists()
            if not campaign or not campaign.is_callcenter_campaign:
                raise ValidationError(
                    _("Agent assignments require a call-center campaign.")
                )
            if campaign.campaign_state == "closed" or not campaign.active:
                raise AccessError(
                    _("Closed or archived campaigns cannot receive Agent assignments.")
                )
            _require_agent_user(user)
            if values.get("active", True) and values.get("date_to"):
                raise ValidationError(
                    _("An active assignment cannot already have an end date.")
                )
            values.setdefault("date_from", fields.Datetime.now())
            values.setdefault("active", True)
            values["created_by_id"] = self.env.user.id
            prepared.append(values)
        records = super().create(prepared)
        records._sync_native_membership()
        return records

    def write(self, values):
        internal = (
            self.env.context.get("_callcenter_assignment_capability")
            is CALLCENTER_ASSIGNMENT_CAPABILITY
        )
        if not internal:
            self._require_superuser()
        immutable = {"campaign_id", "user_id", "is_primary", "date_from"}
        if immutable.intersection(values):
            raise AccessError(
                _("Assignment identity is immutable; end it and create a new assignment.")
            )
        if any(not assignment.active for assignment in self) and not internal:
            raise AccessError(_("Historical assignments are immutable."))
        if values.get("active") is True and any(not row.active for row in self):
            raise AccessError(_("Historical assignments cannot be reactivated."))
        mutable = dict(values)
        if mutable.get("active") is False and not mutable.get("date_to"):
            mutable["date_to"] = fields.Datetime.now()
        result = super().write(mutable)
        self._sync_native_membership()
        return result

    def unlink(self):
        raise AccessError(
            _("Campaign assignment history is retained and cannot be deleted.")
        )

    @api.constrains("campaign_id", "user_id", "active", "date_from", "date_to")
    def _check_assignment_dates_and_scope(self):
        for assignment in self:
            if not assignment.campaign_id.is_callcenter_campaign:
                raise ValidationError(
                    _("Assignment campaign must be a call-center campaign.")
                )
            _require_agent_user(assignment.user_id)
            if (
                assignment.date_from
                and assignment.date_to
                and assignment.date_to <= assignment.date_from
            ):
                raise ValidationError(
                    _("Assignment end must be later than its start.")
                )
            if assignment.active and assignment.date_to:
                raise ValidationError(
                    _("An active assignment cannot have an end date.")
                )

    def _sync_native_membership(self):
        Native = self.env["crm.team.member"].sudo().with_context(active_test=False)
        for assignment in self:
            native = Native.search(
                [
                    ("crm_team_id", "=", assignment.campaign_id.id),
                    ("user_id", "=", assignment.user_id.id),
                ],
                order="id desc",
                limit=1,
            )
            native = native.with_context(
                _callcenter_native_member_capability=(
                    CALLCENTER_NATIVE_MEMBER_CAPABILITY
                )
            )
            if assignment.active:
                if native:
                    if not native.active:
                        native.write({"active": True})
                else:
                    Native.with_context(
                        _callcenter_native_member_capability=(
                            CALLCENTER_NATIVE_MEMBER_CAPABILITY
                        )
                    ).create(
                        {
                            "crm_team_id": assignment.campaign_id.id,
                            "user_id": assignment.user_id.id,
                            "active": True,
                        }
                    )
            elif native and native.active:
                native.write({"active": False})

    def action_end_assignment(self):
        self._require_superuser()
        return self.write({"active": False, "date_to": fields.Datetime.now()})


class CrmTeamMemberCallCenterGuard(models.Model):
    _inherit = "crm.team.member"

    @api.model_create_multi
    def create(self, values_list):
        if self.env.context.get(
            "_callcenter_native_member_capability"
        ) is not CALLCENTER_NATIVE_MEMBER_CAPABILITY:
            team_ids = {
                int(values.get("crm_team_id"))
                for values in values_list
                if values.get("crm_team_id")
            }
            teams = self.env["crm.team"].browse(team_ids).exists()
            if any(teams.mapped("is_callcenter_campaign")):
                raise AccessError(
                    _(
                        "Native campaign members are synchronized from "
                        "callcenter.campaign.assignment."
                    )
                )
        return super().create(values_list)

    def write(self, values):
        if self.env.context.get(
            "_callcenter_native_member_capability"
        ) is not CALLCENTER_NATIVE_MEMBER_CAPABILITY:
            teams = self.mapped("crm_team_id")
            if values.get("crm_team_id"):
                teams |= self.env["crm.team"].browse(values["crm_team_id"])
            if any(teams.mapped("is_callcenter_campaign")) and {
                "crm_team_id",
                "user_id",
                "active",
            }.intersection(values):
                raise AccessError(
                    _(
                        "Native campaign members are synchronized from "
                        "callcenter.campaign.assignment."
                    )
                )
        return super().write(values)

    def unlink(self):
        if self.env.context.get(
            "_callcenter_native_member_capability"
        ) is not CALLCENTER_NATIVE_MEMBER_CAPABILITY:
            if any(self.mapped("crm_team_id.is_callcenter_campaign")):
                raise AccessError(
                    _("Call-center CRM memberships are retained, not deleted.")
                )
        return super().unlink()


class CallCenterCampaignStateLog(models.Model):
    _name = "callcenter.campaign.state.log"
    _description = "Call Center Campaign State Audit"
    _order = "changed_at desc, id desc"

    campaign_id = fields.Many2one(
        "crm.team",
        required=True,
        ondelete="restrict",
        index=True,
    )
    from_state = fields.Selection(
        CALLCENTER_TEAM_CAMPAIGN_STATES + [("archived", "Archived")],
        required=True,
        index=True,
    )
    to_state = fields.Selection(
        CALLCENTER_TEAM_CAMPAIGN_STATES + [("archived", "Archived")],
        required=True,
        index=True,
    )
    changed_by_id = fields.Many2one(
        "res.users",
        required=True,
        ondelete="restrict",
        index=True,
    )
    changed_at = fields.Datetime(
        required=True,
        default=fields.Datetime.now,
        index=True,
    )
    reason = fields.Text()

    @api.model_create_multi
    def create(self, values_list):
        if self.env.context.get(
            "_callcenter_state_log_capability"
        ) is not CALLCENTER_STATE_LOG_CAPABILITY:
            raise AccessError(_("Campaign state logs are written only by lifecycle actions."))
        return super().create(values_list)

    def write(self, values):
        raise AccessError(_("Campaign state audit rows are immutable."))

    def unlink(self):
        raise AccessError(_("Campaign state audit rows cannot be deleted."))


class CrmLeadCallCenterLifecycleGate(models.Model):
    _inherit = "crm.lead"

    @api.model_create_multi
    def create(self, values_list):
        prepared = []
        for original in values_list:
            values = dict(original)
            team = self.env["crm.team"]
            if values.get("team_id"):
                team = self.env["crm.team"].browse(values["team_id"]).exists()
            elif _is_operational_user(self.env.user) and not _is_callcenter_superuser(
                self.env.user
            ):
                assignments = self.env[
                    "callcenter.campaign.assignment"
                ].sudo().search(
                    [
                        ("user_id", "=", self.env.user.id),
                        ("active", "=", True),
                        ("is_primary", "=", True),
                    ]
                )
                if len(assignments) == 1:
                    team = assignments.campaign_id
                    values["team_id"] = team.id

            if team and team.is_callcenter_campaign:
                if team.campaign_state == "closed" or not team.active:
                    raise AccessError(
                        _("Closed or archived campaigns cannot receive new leads.")
                    )
                operational = (
                    _is_operational_user(self.env.user)
                    and not _is_callcenter_superuser(self.env.user)
                )
                if operational and team.campaign_state != "active":
                    raise AccessError(
                        _("Agents and supervisors may create leads only while the campaign is Active.")
                    )
                if team.campaign_state != "active" and values.get("user_id"):
                    raise AccessError(
                        _("Preloaded leads must remain unassigned until the campaign is Active.")
                    )

                is_supervisor = self.env.user.has_group(
                    "codestra_cc_security.group_cc_campaign_supervisor"
                )
                if operational and not is_supervisor:
                    if values.get("user_id") and values["user_id"] != self.env.user.id:
                        raise AccessError(_("Agents may create leads only for themselves."))
                    assignment = self.env["callcenter.campaign.assignment"].sudo().search(
                        [
                            ("campaign_id", "=", team.id),
                            ("user_id", "=", self.env.user.id),
                            ("active", "=", True),
                        ],
                        limit=1,
                    )
                    if not assignment:
                        raise AccessError(_("An active campaign assignment is required."))
                    values["user_id"] = self.env.user.id
                elif values.get("user_id"):
                    assignment = self.env["callcenter.campaign.assignment"].sudo().search(
                        [
                            ("campaign_id", "=", team.id),
                            ("user_id", "=", values["user_id"]),
                            ("active", "=", True),
                        ],
                        limit=1,
                    )
                    if not assignment:
                        raise ValidationError(
                            _("The assigned Agent is not active in this campaign.")
                        )
            prepared.append(values)
        return super().create(prepared)

    def write(self, values):
        governed = self.filtered(
            lambda lead: lead.team_id and lead.team_id.is_callcenter_campaign
        )
        if governed:
            if (
                _is_operational_user(self.env.user)
                and not _is_callcenter_superuser(self.env.user)
                and any(
                    lead.team_id.campaign_state != "active" or not lead.team_id.active
                    for lead in governed
                )
            ):
                raise AccessError(
                    _("Operational lead work is blocked unless the campaign is Active.")
                )
            if "user_id" in values:
                if any(
                    lead.team_id.campaign_state != "active" or not lead.team_id.active
                    for lead in governed
                ):
                    raise AccessError(
                        _("Lead assignment is available only while the campaign is Active.")
                    )
                if values.get("user_id"):
                    for lead in governed:
                        assignment = self.env[
                            "callcenter.campaign.assignment"
                        ].sudo().search(
                            [
                                ("campaign_id", "=", lead.team_id.id),
                                ("user_id", "=", values["user_id"]),
                                ("active", "=", True),
                            ],
                            limit=1,
                        )
                        if not assignment:
                            raise ValidationError(
                                _("The assigned Agent is not active in this campaign.")
                            )

        if "team_id" in values:
            target = self.env["crm.team"].browse(values["team_id"]).exists()
            for lead in self:
                source = lead.team_id
                if (
                    source
                    and source.is_callcenter_campaign
                    and target
                    and target != source
                    and not _is_callcenter_superuser(self.env.user)
                ):
                    raise AccessError(
                        _("Only the Call Center Super User may move a lead to another campaign.")
                    )
            if target and target.is_callcenter_campaign:
                if target.campaign_state == "closed" or not target.active:
                    raise AccessError(
                        _("Leads cannot be moved into a Closed or archived campaign.")
                    )
                for lead in self:
                    effective_user_id = (
                        values.get("user_id")
                        if "user_id" in values
                        else lead.user_id.id
                    )
                    if target.campaign_state != "active" and effective_user_id:
                        raise AccessError(
                            _("A lead moved into a non-Active campaign must be unassigned.")
                        )
                    if effective_user_id:
                        assignment = self.env[
                            "callcenter.campaign.assignment"
                        ].sudo().search(
                            [
                                ("campaign_id", "=", target.id),
                                ("user_id", "=", effective_user_id),
                                ("active", "=", True),
                            ],
                            limit=1,
                        )
                        if not assignment:
                            raise ValidationError(
                                _("The assigned Agent is not active in the target campaign.")
                            )
        return super().write(values)


class VicidialCallCallCenterLifecycleGate(models.Model):
    _inherit = "codestra.vicidial.call"

    @api.model_create_multi
    def create(self, values_list):
        for values in values_list:
            lead_id = values.get("crm_lead_id") or values.get("lead_id")
            if not lead_id:
                continue
            lead = self.env["crm.lead"].browse(lead_id).exists()
            if not lead or not lead.team_id or not lead.team_id.is_callcenter_campaign:
                continue
            team = lead.team_id
            if not team.active or team.campaign_state != "active":
                raise AccessError(
                    _(
                        "New call activity is blocked because campaign %(campaign)s "
                        "is not Active.",
                        campaign=team.display_name,
                    )
                )
        return super().create(values_list)


class CallCenterCampaignTransitionWizard(models.TransientModel):
    _name = "callcenter.campaign.transition.wizard"
    _description = "Call Center Campaign Transition Reason"

    campaign_id = fields.Many2one(
        "crm.team",
        required=True,
        readonly=True,
        ondelete="cascade",
    )
    target_state = fields.Selection(
        [
            ("paused", "Paused"),
            ("closed", "Closed"),
            ("archived", "Archived"),
        ],
        required=True,
        readonly=True,
    )
    reason = fields.Text(required=True)

    def action_confirm(self):
        self.ensure_one()
        if not _is_callcenter_superuser(self.env.user):
            raise AccessError(
                _("Only the Call Center Super User may change campaign lifecycle.")
            )
        reason = str(self.reason or "").strip()
        if not reason:
            raise ValidationError(_("A transition reason is required."))
        if self.target_state == "paused":
            self.campaign_id.action_callcenter_pause(reason)
        elif self.target_state == "closed":
            self.campaign_id.action_callcenter_close(reason)
        elif self.target_state == "archived":
            self.campaign_id.action_callcenter_archive(reason)
        else:
            raise ValidationError(_("Unsupported campaign transition."))
        return {"type": "ir.actions.act_window_close"}
