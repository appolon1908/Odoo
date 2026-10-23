from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError


OPERATIONAL_ROLES = {"agent", "senior_agent", "supervisor"}
ROLE_GROUP_XMLIDS = {
    "agent": "codestra_cc_security.group_cc_campaign_agent",
    "senior_agent": "codestra_cc_security.group_cc_senior_agent",
    "supervisor": "codestra_cc_security.group_cc_campaign_supervisor",
    "qa": "codestra_cc_security.group_cc_quality_analyst",
    "workforce": "codestra_cc_security.group_cc_workforce_analyst",
    "compliance": "codestra_cc_security.group_cc_compliance_officer",
    "configuration_manager": (
        "codestra_cc_security.group_cc_campaign_configuration_manager"
    ),
    "auditor": "codestra_cc_security.group_cc_auditor",
}
IMMUTABLE_MEMBERSHIP_FIELDS = {
    "user_id",
    "employee_id",
    "campaign_id",
    "role",
    "requested_by_id",
}


class ResUsers(models.Model):
    _inherit = "res.users"

    cc_campaign_membership_ids = fields.One2many(
        "cc.campaign.membership", "user_id", string="Campaign Memberships"
    )
    cc_allowed_campaign_ids = fields.Many2many(
        "cc.campaign",
        "cc_campaign_user_scope_rel",
        "user_id",
        "campaign_id",
        compute="_compute_cc_authorization_scope",
        compute_sudo=False,
        store=True,
        readonly=True,
        string="Allowed Campaign Workspaces",
        context={"active_test": False},
    )
    cc_allowed_business_unit_ids = fields.Many2many(
        "cc.business.unit",
        "cc_business_unit_user_scope_rel",
        "user_id",
        "business_unit_id",
        compute="_compute_cc_authorization_scope",
        compute_sudo=False,
        store=True,
        readonly=True,
        string="Allowed Contact Center Business Units",
        context={"active_test": False},
    )
    cc_supervised_campaign_ids = fields.Many2many(
        "cc.campaign",
        "cc_supervisor_campaign_scope_rel",
        "user_id",
        "campaign_id",
        compute="_compute_cc_authorization_scope",
        compute_sudo=False,
        store=True,
        readonly=True,
        string="Supervised Campaign Workspaces",
        context={"active_test": False},
    )
    cc_active_operational_membership_id = fields.Many2one(
        "cc.campaign.membership",
        compute="_compute_cc_authorization_scope",
        compute_sudo=False,
        store=True,
        readonly=True,
        string="Active Operational Membership",
    )
    cc_has_active_break_glass = fields.Boolean(
        compute="_compute_cc_has_active_break_glass",
        compute_sudo=False,
        string="Active Contact Center Break Glass",
    )

    @api.depends(
        "cc_campaign_membership_ids.state",
        "cc_campaign_membership_ids.role",
        "cc_campaign_membership_ids.campaign_id",
    )
    def _compute_cc_authorization_scope(self):
        for user in self:
            active_memberships = user.cc_campaign_membership_ids.filtered(
                lambda membership: membership.state == "active"
            )
            user.cc_allowed_campaign_ids = active_memberships.mapped("campaign_id")
            user.cc_allowed_business_unit_ids = active_memberships.mapped(
                "business_unit_id"
            )
            supervisor_memberships = active_memberships.filtered(
                lambda membership: membership.role == "supervisor"
            )
            user.cc_supervised_campaign_ids = supervisor_memberships.mapped(
                "campaign_id"
            )
            operational = active_memberships.filtered(
                lambda membership: membership.role in OPERATIONAL_ROLES
            )
            user.cc_active_operational_membership_id = (
                operational if len(operational) == 1 else False
            )

    def _compute_cc_has_active_break_glass(self):
        now = fields.Datetime.now()
        Grant = self.env["cc.break.glass.grant"]
        for user in self:
            user.cc_has_active_break_glass = bool(
                Grant.search_count(
                    [
                        ("user_id", "=", user.id),
                        ("state", "=", "active"),
                        ("starts_at", "<=", now),
                        ("ends_at", ">", now),
                    ],
                    limit=1,
                )
            )


class CcCampaign(models.Model):
    _inherit = "cc.campaign"

    primary_supervisor_membership_id = fields.Many2one(
        "cc.campaign.membership",
        string="Primary Supervisor Membership",
        ondelete="restrict",
        copy=False,
        readonly=True,
    )

    @api.constrains("primary_supervisor_membership_id")
    def _check_primary_supervisor_membership(self):
        for campaign in self:
            membership = campaign.primary_supervisor_membership_id
            if not membership:
                continue
            if (
                membership.campaign_id != campaign
                or membership.state != "active"
                or membership.role != "supervisor"
                or not membership.is_primary_supervisor
            ):
                raise ValidationError(
                    _("The primary supervisor must be the campaign's active supervisor.")
                )


class CcCampaignMembership(models.Model):
    _name = "cc.campaign.membership"
    _description = "Campaign Membership"
    _inherit = "cc.campaign.scoped.mixin"
    _order = "campaign_id, role, user_id"

    user_id = fields.Many2one(
        "res.users", required=True, ondelete="restrict", index=True, copy=False
    )
    employee_id = fields.Many2one(
        "hr.employee", required=True, ondelete="restrict", index=True, copy=False
    )
    role = fields.Selection(
        [
            ("agent", "Campaign Agent"),
            ("senior_agent", "Senior Agent / SME"),
            ("supervisor", "Campaign Supervisor"),
            ("qa", "Quality Assurance Analyst"),
            ("workforce", "Workforce / Real-Time Analyst"),
            ("compliance", "Compliance Officer"),
            ("configuration_manager", "Campaign Configuration Manager"),
            ("auditor", "Auditor"),
        ],
        required=True,
        index=True,
        copy=False,
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("pending_approval", "Pending Approval"),
            ("pending_sync", "Pending Synchronization"),
            ("active", "Active"),
            ("suspended", "Suspended"),
            ("expired", "Expired"),
            ("revoked", "Revoked"),
            ("blocked", "Blocked"),
        ],
        required=True,
        default="draft",
        index=True,
        copy=False,
    )
    starts_at = fields.Datetime(copy=False)
    ends_at = fields.Datetime(copy=False)
    is_primary_supervisor = fields.Boolean(
        default=False,
        copy=False,
        index=True,
        help=(
            "Primary supervisor when enabled. An active supervisor membership "
            "without this flag is a backup supervisor."
        ),
    )
    requested_by_id = fields.Many2one(
        "res.users",
        required=True,
        default=lambda self: self.env.user,
        ondelete="restrict",
        copy=False,
    )
    approved_by_id = fields.Many2one(
        "res.users", string="Approved By", ondelete="restrict", copy=False
    )
    approved_at = fields.Datetime(copy=False)
    source_ticket = fields.Char(copy=False, index=True)
    keycloak_subject = fields.Char(copy=False, index=True)
    vicidial_user = fields.Char(copy=False, index=True)
    vicidial_user_group = fields.Char(copy=False, index=True)
    campaign_email_identity = fields.Char(copy=False, index=True)
    distribution_groups = fields.Json(default=list, copy=False)
    scope_version = fields.Integer(
        related="campaign_id.scope_version",
        store=True,
        readonly=True,
        string="Membership Scope Version",
    )
    last_sync_status = fields.Selection(
        [
            ("not_started", "Not Started"),
            ("pending", "Pending"),
            ("matched", "Read-Back Matched"),
            ("mismatch", "Read-Back Mismatch"),
            ("failed", "Failed"),
        ],
        required=True,
        default="not_started",
        copy=False,
        index=True,
    )
    read_back_evidence = fields.Text(copy=False)

    _one_active_agent_campaign = models.UniqueIndex(
        "(user_id) WHERE state = 'active' AND role IN ('agent', 'senior_agent')",
        "An agent or senior agent may have only one active campaign.",
    )
    _one_active_supervisor_per_campaign = models.UniqueIndex(
        "(campaign_id) WHERE state = 'active' AND role = 'supervisor' "
        "AND is_primary_supervisor IS TRUE",
        "A campaign may have only one active primary supervisor.",
    )
    _supervisor_one_active_campaign = models.UniqueIndex(
        "(user_id) WHERE state = 'active' AND role = 'supervisor'",
        "A supervisor may have only one active campaign.",
    )
    _one_active_operational_membership = models.UniqueIndex(
        "(user_id) WHERE state = 'active' "
        "AND role IN ('agent', 'senior_agent', 'supervisor')",
        "A user may hold only one active operational campaign role.",
    )

    @api.model_create_multi
    def create(self, values_list):
        if any(values.get("state", "draft") == "active" for values in values_list):
            raise AccessError(
                _("Memberships must be activated through the approval workflow.")
            )
        return super().create(values_list)

    def write(self, values):
        if "campaign_id" in values and not self.env.context.get("cc_scope_migration"):
            if any(record.campaign_id.id != values["campaign_id"] for record in self):
                raise AccessError(
                    _("Campaign reassignment is revoke-then-grant, never an in-place edit.")
                )
        immutable_changes = IMMUTABLE_MEMBERSHIP_FIELDS & values.keys()
        if immutable_changes and not self.env.context.get("cc_scope_migration"):
            for membership in self:
                for field_name in immutable_changes:
                    current = membership[field_name]
                    current_value = (
                        current.id
                        if membership._fields[field_name].type == "many2one"
                        else current
                    )
                    if current_value != values[field_name]:
                        raise AccessError(_("Membership identity and ownership are immutable."))
        if "state" in values and not self.env.context.get("cc_membership_transition"):
            if any(membership.state != values["state"] for membership in self):
                raise AccessError(
                    _("Membership state changes require the governed workflow.")
                )
        result = super().write(values)
        return result

    def unlink(self):
        raise AccessError(_("Campaign membership evidence cannot be deleted."))

    @api.constrains(
        "user_id",
        "employee_id",
        "campaign_id",
        "role",
        "state",
        "starts_at",
        "ends_at",
        "is_primary_supervisor",
        "approved_by_id",
        "requested_by_id",
        "approved_at",
        "source_ticket",
        "last_sync_status",
        "read_back_evidence",
    )
    def _check_membership_invariants(self):
        for membership in self:
            if membership.employee_id.user_id != membership.user_id:
                raise ValidationError(
                    _("The employee must be linked to the membership user.")
                )
            if membership.ends_at and membership.starts_at:
                if membership.ends_at <= membership.starts_at:
                    raise ValidationError(_("Membership expiry must follow its start."))
            if membership.role != "supervisor" and membership.is_primary_supervisor:
                raise ValidationError(
                    _("Only a supervisor can be marked as primary supervisor.")
                )
            if membership.state == "active":
                required_group = self.env.ref(ROLE_GROUP_XMLIDS[membership.role])
                if required_group not in membership.user_id.group_ids:
                    raise ValidationError(
                        _("The user's directly assigned contact-center role must match.")
                    )
                if not membership.starts_at:
                    raise ValidationError(_("Active memberships require a start time."))
                if membership.ends_at and membership.ends_at <= fields.Datetime.now():
                    raise ValidationError(_("An expired membership cannot become active."))
                if (
                    not membership.approved_by_id
                    or not membership.approved_at
                    or not membership.source_ticket
                ):
                    raise ValidationError(
                        _("Active memberships require approval and a source ticket.")
                    )
                if membership.approved_by_id == membership.requested_by_id:
                    raise ValidationError(
                        _("The membership requester cannot approve the same request.")
                    )
                if (
                    membership.last_sync_status != "matched"
                    or not membership.read_back_evidence
                ):
                    raise ValidationError(
                        _("Active membership requires matched read-back evidence.")
                    )
                operational_count = self.search_count(
                    [
                        ("user_id", "=", membership.user_id.id),
                        ("state", "=", "active"),
                        ("role", "in", sorted(OPERATIONAL_ROLES)),
                        ("id", "!=", membership.id),
                    ],
                    limit=1,
                )
                if membership.role in OPERATIONAL_ROLES and operational_count:
                    raise ValidationError(
                        _("A user may hold only one active operational membership.")
                    )
                if membership.role == "supervisor" and membership.is_primary_supervisor:
                    duplicate_supervisor = self.search_count(
                        [
                            ("campaign_id", "=", membership.campaign_id.id),
                            ("state", "=", "active"),
                            ("role", "=", "supervisor"),
                            ("is_primary_supervisor", "=", True),
                            ("id", "!=", membership.id),
                        ],
                        limit=1,
                    )
                    if duplicate_supervisor:
                        raise ValidationError(
                            _("A campaign may have only one active primary supervisor.")
                        )

    def _require_global_administrator(self):
        if not self.env.user.has_group(
            "codestra_cc_security.group_cc_global_administrator"
        ):
            raise AccessError(
                _("Only a global contact-center administrator may change access.")
            )

    def _bump_campaign_scope(self):
        for campaign in self.mapped("campaign_id"):
            campaign.write({"scope_version": campaign.scope_version + 1})

    def _sync_primary_supervisor(self):
        for campaign in self.mapped("campaign_id"):
            supervisor = self.search(
                [
                    ("campaign_id", "=", campaign.id),
                    ("state", "=", "active"),
                    ("role", "=", "supervisor"),
                    ("is_primary_supervisor", "=", True),
                ],
                limit=1,
            )
            campaign.write({"primary_supervisor_membership_id": supervisor.id})

    def _check_activation_conflicts(self):
        for membership in self:
            if membership.role in OPERATIONAL_ROLES and self.search_count(
                [
                    ("user_id", "=", membership.user_id.id),
                    ("state", "=", "active"),
                    ("role", "in", sorted(OPERATIONAL_ROLES)),
                    ("id", "!=", membership.id),
                ],
                limit=1,
            ):
                raise ValidationError(
                    _("A user may hold only one active operational membership.")
                )
            if (
                membership.role == "supervisor"
                and membership.is_primary_supervisor
                and self.search_count(
                [
                    ("campaign_id", "=", membership.campaign_id.id),
                    ("state", "=", "active"),
                    ("role", "=", "supervisor"),
                    ("is_primary_supervisor", "=", True),
                    ("id", "!=", membership.id),
                ],
                limit=1,
                )
            ):
                raise ValidationError(
                    _("A campaign may have only one active primary supervisor.")
                )

    def _invalidate_authorization_scope(self):
        users = self.mapped("user_id")
        users._recompute_recordset(
            [
                "cc_allowed_campaign_ids",
                "cc_allowed_business_unit_ids",
                "cc_supervised_campaign_ids",
                "cc_active_operational_membership_id",
            ]
        )
        users.flush_recordset(
            [
                "cc_allowed_campaign_ids",
                "cc_allowed_business_unit_ids",
                "cc_supervised_campaign_ids",
                "cc_active_operational_membership_id",
            ]
        )
        self.env.registry.clear_cache()

    def action_activate(self):
        self._require_global_administrator()
        now = fields.Datetime.now()
        for membership in self:
            if membership.state not in {"pending_approval", "pending_sync"}:
                raise ValidationError(
                    _("Only approved or synchronized requests can be activated.")
                )
            if membership.requested_by_id == self.env.user:
                raise AccessError(
                    _("The membership requester cannot approve the same request.")
                )
            membership._check_activation_conflicts()
            membership.with_context(cc_membership_transition=True).write(
                {
                    "state": "active",
                    "approved_by_id": self.env.user.id,
                    "approved_at": now,
                    "starts_at": membership.starts_at or now,
                }
            )
            membership._bump_campaign_scope()
            membership._sync_primary_supervisor()
        self._invalidate_authorization_scope()
        return True

    def action_suspend(self):
        self._require_global_administrator()
        for membership in self:
            if membership.state != "active":
                raise ValidationError(_("Only active memberships can be suspended."))
            membership.with_context(cc_membership_transition=True).write(
                {"state": "suspended"}
            )
            membership._bump_campaign_scope()
            membership._sync_primary_supervisor()
        self._invalidate_authorization_scope()
        return True

    def action_revoke(self):
        self._require_global_administrator()
        for membership in self:
            if membership.state in {"revoked", "expired"}:
                continue
            membership.with_context(cc_membership_transition=True).write(
                {"state": "revoked"}
            )
            membership._bump_campaign_scope()
            membership._sync_primary_supervisor()
        self._invalidate_authorization_scope()
        return True


class CcBreakGlassGrant(models.Model):
    _name = "cc.break.glass.grant"
    _description = "Contact Center Break-Glass Grant"
    _order = "create_date desc, id desc"

    user_id = fields.Many2one(
        "res.users", required=True, ondelete="restrict", index=True, copy=False
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("pending_approval", "Pending Approval"),
            ("active", "Active"),
            ("expired", "Expired"),
            ("revoked", "Revoked"),
            ("rejected", "Rejected"),
        ],
        required=True,
        default="draft",
        index=True,
        copy=False,
    )
    reason = fields.Text(required=True, copy=False)
    source_ticket = fields.Char(required=True, index=True, copy=False)
    starts_at = fields.Datetime(required=True, copy=False)
    ends_at = fields.Datetime(required=True, copy=False)
    requested_by_id = fields.Many2one(
        "res.users",
        required=True,
        default=lambda self: self.env.user,
        ondelete="restrict",
        copy=False,
    )
    approved_by_id = fields.Many2one(
        "res.users", string="Approved By", ondelete="restrict", copy=False
    )
    approved_at = fields.Datetime(copy=False)
    revoked_by_id = fields.Many2one(
        "res.users", string="Revoked By", ondelete="restrict", copy=False
    )
    revoked_at = fields.Datetime(copy=False)

    _one_active_break_glass = models.UniqueIndex(
        "(user_id) WHERE state = 'active'",
        "A user may hold only one active contact-center break-glass grant.",
    )

    @api.model_create_multi
    def create(self, values_list):
        if any(values.get("state", "draft") == "active" for values in values_list):
            raise AccessError(_("Break-glass grants require separate approval."))
        return super().create(values_list)

    def write(self, values):
        if "state" in values and not self.env.context.get("cc_break_glass_transition"):
            if any(grant.state != values["state"] for grant in self):
                raise AccessError(_("Use the governed break-glass workflow."))
        if any(grant.state in {"active", "expired", "revoked"} for grant in self):
            allowed = {
                "state",
                "approved_by_id",
                "approved_at",
                "revoked_by_id",
                "revoked_at",
            }
            if values.keys() - allowed:
                raise AccessError(_("Approved break-glass evidence is immutable."))
        return super().write(values)

    def unlink(self):
        raise AccessError(_("Break-glass evidence cannot be deleted."))

    @api.constrains(
        "user_id",
        "state",
        "starts_at",
        "ends_at",
        "requested_by_id",
        "approved_by_id",
        "approved_at",
    )
    def _check_break_glass(self):
        for grant in self:
            if grant.ends_at <= grant.starts_at:
                raise ValidationError(_("Break-glass expiry must follow its start."))
            if grant.ends_at - grant.starts_at > timedelta(hours=4):
                raise ValidationError(
                    _("A break-glass grant cannot exceed four hours.")
                )
            if not grant.user_id.has_group(
                "codestra_cc_security.group_cc_technical_administrator"
            ):
                raise ValidationError(
                    _("Break-glass access is reserved for technical administrators.")
                )
            if grant.state == "active":
                if grant.requested_by_id == grant.approved_by_id:
                    raise ValidationError(
                        _("The break-glass requester cannot approve the same grant.")
                    )
                if not grant.approved_by_id or not grant.approved_at:
                    raise ValidationError(_("Active break-glass access requires approval."))

    def _invalidate_security_scope(self):
        self.mapped("user_id").invalidate_recordset(["cc_has_active_break_glass"])
        self.env.registry.clear_cache()

    def action_submit(self):
        for grant in self:
            if grant.requested_by_id != self.env.user:
                raise AccessError(_("Only the requester may submit this grant."))
            if grant.state != "draft":
                raise ValidationError(_("Only draft grants can be submitted."))
            grant.with_context(cc_break_glass_transition=True).write(
                {"state": "pending_approval"}
            )
        return True

    def action_activate(self):
        if not (
            self.env.user.has_group(
                "codestra_cc_security.group_cc_global_administrator"
            )
            or self.env.user.has_group(
                "codestra_cc_security.group_cc_compliance_officer"
            )
        ):
            raise AccessError(_("Global administration or Compliance must approve."))
        now = fields.Datetime.now()
        for grant in self:
            if grant.state != "pending_approval":
                raise ValidationError(_("Only pending grants can be activated."))
            if grant.requested_by_id == self.env.user:
                raise AccessError(_("A requester cannot approve the same grant."))
            if not (grant.starts_at <= now < grant.ends_at):
                raise ValidationError(
                    _("Break-glass access can activate only inside its approved window.")
                )
            grant.with_context(cc_break_glass_transition=True).write(
                {
                    "state": "active",
                    "approved_by_id": self.env.user.id,
                    "approved_at": now,
                }
            )
            grant._invalidate_security_scope()
        return True

    def action_revoke(self):
        now = fields.Datetime.now()
        for grant in self:
            if not (
                self.env.user == grant.user_id
                or self.env.user.has_group(
                    "codestra_cc_security.group_cc_global_administrator"
                )
                or self.env.user.has_group(
                    "codestra_cc_security.group_cc_compliance_officer"
                )
            ):
                raise AccessError(_("This user cannot revoke the break-glass grant."))
            if grant.state not in {"active", "pending_approval"}:
                continue
            grant.with_context(cc_break_glass_transition=True).write(
                {
                    "state": "revoked",
                    "revoked_by_id": self.env.user.id,
                    "revoked_at": now,
                }
            )
            grant._invalidate_security_scope()
        return True