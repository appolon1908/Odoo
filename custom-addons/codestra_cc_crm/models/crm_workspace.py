import hashlib
import json
import re
import uuid

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


OPERATIONAL_GROUPS = (
    "codestra_cc_security.group_cc_campaign_agent",
    "codestra_cc_security.group_cc_senior_agent",
    "codestra_cc_security.group_cc_campaign_supervisor",
)
CRM_SCOPE_MIGRATION_CAPABILITY = object()
CRM_TRANSITION_CAPABILITY = object()
PROFILE_WRITE_CAPABILITY = object()
CRM_MAINTENANCE_DELETE_CAPABILITY = object()
PROFILE_AGENT_WRITE_FIELDS = {"verification_state", "verification_checklist"}
PROFILE_SUPERVISOR_WRITE_FIELDS = PROFILE_AGENT_WRITE_FIELDS | {
    "assigned_user_id",
    "state",
}
FORBIDDEN_CHECKLIST_KEYS = {
    "account_number",
    "api_key",
    "bank_account",
    "card_number",
    "cvv",
    "password",
    "pin",
    "secret",
    "security_code",
    "token",
}
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _sha256(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _is_operational(user):
    return any(user.has_group(xmlid) for xmlid in OPERATIONAL_GROUPS)


def _is_global_admin(user):
    return user.has_group("codestra_cc_security.group_cc_global_administrator")


def _is_supervisor(user):
    return user.has_group("codestra_cc_security.group_cc_campaign_supervisor")


def _validate_agent_assignment(env, campaign, user):
    if not user:
        return False
    membership = env["cc.campaign.membership"].sudo().search(
        [
            ("user_id", "=", user.id),
            ("campaign_id", "=", campaign.id),
            ("state", "=", "active"),
            ("role", "in", ("agent", "senior_agent")),
        ],
        limit=1,
    )
    if not membership:
        raise ValidationError(_("Agent is not assigned to this campaign."))
    return membership


def _mask_email(value):
    normalized = (value or "").strip().lower()
    if "@" not in normalized:
        return False
    local, domain = normalized.rsplit("@", 1)
    if not local or not domain:
        return False
    return f"{local[:1]}***@{domain}"


def _mask_phone(value):
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return f"***{digits[-4:]}" if len(digits) >= 4 else False


def _profile_campaign(env, supplied_campaign_id=False, profile=False):
    if _is_operational(env.user):
        membership = env.user._cc_resolve_operational_membership()
        campaign = membership.campaign_id
        if supplied_campaign_id and supplied_campaign_id != campaign.id:
            raise AccessError(_("The authenticated membership determines campaign scope."))
        if profile and profile.campaign_id != campaign:
            raise AccessError(_("The customer profile belongs to another campaign."))
        return campaign
    if profile:
        if supplied_campaign_id and supplied_campaign_id != profile.campaign_id.id:
            raise AccessError(_("Customer profile and campaign scope differ."))
        return profile.campaign_id
    campaign = env["cc.campaign"].browse(supplied_campaign_id).exists()
    if not campaign:
        raise ValidationError(_("A canonical campaign is required."))
    campaign.check_access("read")
    return campaign


class CcCustomerProfile(models.Model):
    _name = "cc.customer.profile"
    _description = "Campaign-Scoped Customer Profile"
    _inherit = ["cc.campaign.scoped.mixin", "mail.thread", "mail.activity.mixin"]
    _order = "write_date desc, id desc"
    _rec_name = "name"
    _rec_names_search = ["name", "profile_uuid"]

    profile_uuid = fields.Char(
        required=True,
        default=lambda self: str(uuid.uuid4()),
        readonly=True,
        copy=False,
        index=True,
    )
    integration_key = fields.Char(
        required=True,
        default=lambda self: str(uuid.uuid4()),
        readonly=True,
        copy=False,
        index=True,
    )
    environment = fields.Selection(
        related="campaign_id.environment", store=True, readonly=True, index=True
    )
    active = fields.Boolean(default=True, required=True, index=True)
    state = fields.Selection(
        [
            ("new", "New"),
            ("active", "Active"),
            ("blocked", "Blocked"),
            ("archived", "Archived"),
        ],
        required=True,
        default="new",
        tracking=True,
        index=True,
    )
    name = fields.Char(required=True, tracking=True, index=True)
    email_masked = fields.Char(readonly=True)
    phone_masked = fields.Char(readonly=True)
    partner_reference_hash = fields.Char(required=True, size=64, readonly=True, index=True)
    partner_id = fields.Many2one(
        "res.partner",
        ondelete="restrict",
        readonly=True,
        copy=False,
        groups=(
            "codestra_cc_security.group_cc_global_administrator,"
            "codestra_cc_crm.group_cc_crm_service"
        ),
    )
    assigned_user_id = fields.Many2one(
        "res.users", ondelete="restrict", tracking=True, index=True
    )
    verification_state = fields.Selection(
        [
            ("not_started", "Not Started"),
            ("in_progress", "In Progress"),
            ("verified", "Verified"),
            ("failed", "Failed"),
        ],
        required=True,
        default="not_started",
        tracking=True,
        index=True,
    )
    verification_checklist = fields.Json(default=dict)
    lead_ids = fields.One2many("crm.lead", "cc_customer_profile_id", readonly=True)
    _profile_uuid_unique = models.Constraint(
        "unique(profile_uuid)", "Customer profile UUIDs must be unique."
    )
    _integration_key_unique = models.Constraint(
        "unique(campaign_id, integration_key)",
        "Customer integration keys must be unique inside a campaign.",
    )
    _partner_campaign_unique = models.Constraint(
        "unique(campaign_id, partner_id)",
        "A contact may have only one profile in a campaign.",
    )

    @api.model_create_multi
    def create(self, values_list):
        prepared = []
        privileged_partner = _is_global_admin(self.env.user) or self.env.user.has_group(
            "codestra_cc_crm.group_cc_crm_service"
        )
        if not privileged_partner:
            raise AccessError(
                _("Customer projection creation requires the governed CRM service.")
            )
        for original in values_list:
            values = dict(original)
            campaign = _profile_campaign(self.env, values.get("campaign_id"))
            values["campaign_id"] = campaign.id
            if not values.get("partner_reference_hash"):
                raise ValidationError(_("A tokenized partner reference is required."))
            if not SHA256_PATTERN.fullmatch(
                str(values["partner_reference_hash"]).lower()
            ):
                raise ValidationError(_("Partner reference hashes must be SHA-256."))
            values.setdefault("active", True)
            prepared.append(values)
        return super().create(prepared)

    @api.model
    def create_from_partner(self, partner, campaign, integration_key=False):
        if not (
            _is_global_admin(self.env.user)
            or self.env.user.has_group("codestra_cc_crm.group_cc_crm_service")
        ):
            raise AccessError(_("Customer projection requires the governed CRM service."))
        partner = self.env["res.partner"].browse(partner.id).exists()
        campaign = self.env["cc.campaign"].browse(campaign.id).exists()
        if not partner or not campaign:
            raise ValidationError(_("A valid contact and campaign are required."))
        return self.create(
            {
                "campaign_id": campaign.id,
                "integration_key": integration_key or str(uuid.uuid4()),
                "name": partner.display_name,
                "email_masked": _mask_email(partner.email),
                "phone_masked": _mask_phone(partner.phone),
                "partner_reference_hash": _sha256(
                    f"{campaign.workspace_uuid}:{partner.id}"
                ),
                "partner_id": partner.id,
                "state": "active",
            }
        )

    def write(self, values):
        if "campaign_id" in values and self.env.context.get(
            "_cc_crm_scope_capability"
        ) is not CRM_SCOPE_MIGRATION_CAPABILITY:
            raise AccessError(_("Customer profile campaign ownership is immutable."))
        if _is_operational(self.env.user) and not _is_global_admin(self.env.user):
            allowed = (
                PROFILE_SUPERVISOR_WRITE_FIELDS
                if _is_supervisor(self.env.user)
                else PROFILE_AGENT_WRITE_FIELDS
            )
            forbidden = set(values).difference(allowed)
            if forbidden:
                raise AccessError(
                    _("Operational users cannot change customer identity or scope.")
                )
        protected = {
            "profile_uuid",
            "integration_key",
            "name",
            "partner_id",
            "partner_reference_hash",
            "email_masked",
            "phone_masked",
        }
        if protected.intersection(values) and self.env.context.get(
            "_cc_profile_write_capability"
        ) is not PROFILE_WRITE_CAPABILITY:
            raise AccessError(_("Customer projection identity is service-managed."))
        return super().write(values)

    def unlink(self):
        raise AccessError(_("Campaign customer profiles are retained, not deleted."))

    def copy(self, default=None):
        raise AccessError(_("Campaign customer profiles cannot be copied."))

    def export_data(self, fields_to_export, raw_data=False):
        if _is_operational(self.env.user):
            raise UserError(_("Agent and supervisor customer-profile export is disabled."))
        return super().export_data(fields_to_export, raw_data=raw_data)

    @api.constrains("verification_checklist")
    def _check_verification_checklist(self):
        for profile in self:
            checklist = profile.verification_checklist or {}
            if not isinstance(checklist, dict):
                raise ValidationError(_("Verification checklists must be key/value maps."))
            keys = {str(key).strip().lower() for key in checklist}
            if keys.intersection(FORBIDDEN_CHECKLIST_KEYS):
                raise ValidationError(_("Secrets and payment credentials are prohibited."))
            if len(json.dumps(checklist, default=str)) > 4096:
                raise ValidationError(_("Verification checklists exceed the safe limit."))

    @api.constrains("assigned_user_id", "campaign_id")
    def _check_assignment(self):
        for profile in self.filtered("assigned_user_id"):
            membership = self.env["cc.campaign.membership"].search(
                [
                    ("user_id", "=", profile.assigned_user_id.id),
                    ("campaign_id", "=", profile.campaign_id.id),
                    ("state", "=", "active"),
                    ("role", "in", ("agent", "senior_agent", "supervisor")),
                ],
                limit=1,
            )
            if not membership:
                raise ValidationError(
                    _("Profile assignment requires an active same-campaign membership.")
                )

    def action_open_leads(self):
        self.ensure_one()
        self.check_access("read")
        return {
            "type": "ir.actions.act_window",
            "name": _("Campaign Leads"),
            "res_model": "crm.lead",
            "view_mode": "list,form",
            "domain": [("cc_customer_profile_id", "=", self.id)],
            "context": {"default_cc_customer_profile_id": self.id},
        }


class CrmLead(models.Model):
    _inherit = "crm.lead"

    campaign_id = fields.Many2one(
        "cc.campaign",
        string="Campaign Workspace",
        ondelete="restrict",
        index=True,
        tracking=True,
    )
    cc_business_unit_id = fields.Many2one(
        "cc.business.unit",
        related="campaign_id.cc_business_unit_id",
        store=True,
        readonly=True,
        index=True,
    )
    cc_environment = fields.Selection(
        related="campaign_id.environment", store=True, readonly=True, index=True
    )
    cc_scope_version = fields.Integer(
        related="campaign_id.scope_version", store=True, readonly=True
    )
    cc_customer_profile_id = fields.Many2one(
        "cc.customer.profile", ondelete="restrict", index=True, tracking=True
    )
    cc_contact_center_record = fields.Boolean(default=False, index=True, tracking=True)
    cc_source_list_key = fields.Char(index=True)
    cc_consent_state = fields.Selection(
        [
            ("unknown", "Unknown"),
            ("captured", "Captured"),
            ("revoked", "Revoked"),
            ("suppressed", "Suppressed"),
        ],
        required=True,
        default="unknown",
        index=True,
    )

    @api.model_create_multi
    def create(self, values_list):
        prepared = []
        for original in values_list:
            values = dict(original)
            profile = self.env["cc.customer.profile"]
            if values.get("cc_customer_profile_id"):
                profile = self.env["cc.customer.profile"].browse(
                    values["cc_customer_profile_id"]
                ).exists()
                if not profile:
                    raise ValidationError(_("The campaign customer profile does not exist."))
                profile.check_access("read")
            governed = bool(
                values.get("cc_contact_center_record")
                or values.get("campaign_id")
                or profile
            )
            if governed:
                campaign = _profile_campaign(
                    self.env, values.get("campaign_id"), profile=profile
                )
                if not str(values.get("cc_source_list_key") or "").strip():
                    raise ValidationError(
                        _("Campaign CRM records require an explicit source-list key.")
                    )
                values.update(
                    {
                        "campaign_id": campaign.id,
                        "cc_contact_center_record": True,
                        "call_center_campaign_id": campaign.legacy_campaign_id.id,
                        "is_codestra_call_center_lead": True,
                        "business_unit_id": campaign.cc_business_unit_id.legacy_business_unit_id.id,
                    }
                )
                if (
                    _is_operational(self.env.user)
                    and not _is_supervisor(self.env.user)
                    and not _is_global_admin(self.env.user)
                ):
                    requested_user_id = values.get("user_id")
                    if requested_user_id and requested_user_id != self.env.user.id:
                        raise AccessError(_("Agents may create leads only for themselves."))
                    values["user_id"] = self.env.user.id
                if values.get("user_id"):
                    target_user = self.env["res.users"].browse(values["user_id"]).exists()
                    if not target_user:
                        raise ValidationError(_("The assigned agent does not exist."))
                    _validate_agent_assignment(self.env, campaign, target_user)
            prepared.append(values)
        return super().create(prepared)

    def write(self, values):
        governed = self.filtered("cc_contact_center_record")
        if {
            "campaign_id",
            "cc_customer_profile_id",
            "cc_source_list_key",
        }.intersection(values):
            if self.env.context.get(
                "_cc_crm_scope_capability"
            ) is not CRM_SCOPE_MIGRATION_CAPABILITY:
                raise AccessError(_("CRM campaign ownership is immutable."))

        if governed and "active" in values:
            if _is_global_admin(self.env.user):
                pass
            elif _is_supervisor(self.env.user):
                if values["active"] is not False:
                    raise AccessError(
                        _("Supervisors may archive, but only the Call Center Super User may restore leads.")
                    )
                supervised_ids = set(self.env.user.cc_supervised_campaign_ids.ids)
                if any(lead.campaign_id.id not in supervised_ids for lead in governed):
                    raise AccessError(_("Supervisors may archive only their supervised campaigns."))
            elif _is_operational(self.env.user):
                raise AccessError(_("Agents cannot archive call-center leads."))

        reassignment_fields = {
            "user_id",
            "assigned_agent_profile_id",
            "call_center_supervisor_id",
            "codestra_supervisor_id",
        }
        if (
            _is_operational(self.env.user)
            and not _is_supervisor(self.env.user)
            and reassignment_fields.intersection(values)
        ):
            raise AccessError(_("Agents cannot reassign campaign CRM ownership."))
        if (
            _is_operational(self.env.user)
            and not _is_supervisor(self.env.user)
            and "team_id" in values
        ):
            raise AccessError(_("Agents cannot change the CRM team."))

        if governed and "user_id" in values and values.get("user_id"):
            target_user = self.env["res.users"].browse(values["user_id"]).exists()
            if not target_user:
                raise ValidationError(_("The assigned agent does not exist."))
            for lead in governed:
                _validate_agent_assignment(self.env, lead.campaign_id, target_user)

        if governed and _is_supervisor(self.env.user) and "user_id" in values:
            supervised_ids = set(self.env.user.cc_supervised_campaign_ids.ids)
            if any(lead.campaign_id.id not in supervised_ids for lead in governed):
                raise AccessError(_("Supervisors may reassign only their supervised campaigns."))

        if _is_operational(self.env.user) and {
            "codestra_workflow_id",
            "codestra_current_status_id",
            "codestra_previous_status_id",
            "status_entered_at",
        }.intersection(values) and self.env.context.get(
            "_cc_crm_transition_capability"
        ) is not CRM_TRANSITION_CAPABILITY:
            raise AccessError(_("Campaign CRM status requires the governed transition."))
        if _is_operational(self.env.user) and {
            "partner_id",
            "business_unit_id",
            "call_center_campaign_id",
            "is_codestra_call_center_lead",
        }.intersection(values):
            raise AccessError(_("Operational users cannot bypass the customer profile."))
        return super().write(values)

    def action_callcenter_assign_agent(self, target_user_id):
        if not (_is_supervisor(self.env.user) or _is_global_admin(self.env.user)):
            raise AccessError(_("Only a supervisor or Call Center Super User may assign leads."))
        target_user = self.env["res.users"].browse(target_user_id).exists()
        if not target_user:
            raise ValidationError(_("The assigned agent does not exist."))
        for lead in self.filtered("cc_contact_center_record"):
            _validate_agent_assignment(self.env, lead.campaign_id, target_user)
        return self.write({"user_id": target_user.id})

    def action_callcenter_transfer_campaign(
        self,
        target_campaign_id,
        target_user_id=False,
        target_profile_id=False,
        target_team_id=False,
    ):
        if not _is_global_admin(self.env.user):
            raise AccessError(
                _("Only the Call Center Super User may transfer leads between campaigns.")
            )
        self.ensure_one()
        if not self.cc_contact_center_record:
            raise ValidationError(_("Only governed call-center leads use campaign transfer."))

        target_campaign = (
            self.env["cc.campaign"]
            .with_context(active_test=False)
            .browse(target_campaign_id)
            .exists()
        )
        if not target_campaign:
            raise ValidationError(_("The target campaign does not exist."))

        target_user = self.env["res.users"]
        if target_user_id:
            target_user = self.env["res.users"].browse(target_user_id).exists()
            if not target_user:
                raise ValidationError(_("The target agent does not exist."))
            _validate_agent_assignment(self.env, target_campaign, target_user)

        target_profile = self.env["cc.customer.profile"]
        if target_profile_id:
            target_profile = (
                self.env["cc.customer.profile"]
                .with_context(active_test=False)
                .browse(target_profile_id)
                .exists()
            )
            if not target_profile or target_profile.campaign_id != target_campaign:
                raise ValidationError(
                    _("The target customer profile must belong to the target campaign.")
                )
        elif self.cc_customer_profile_id:
            raise ValidationError(
                _("A target-campaign customer profile is required for this lead.")
            )

        target_team = self.env["crm.team"]
        if target_team_id:
            target_team = self.env["crm.team"].browse(target_team_id).exists()
            if (
                not target_team
                or target_team not in target_campaign.legacy_campaign_id.team_ids
            ):
                raise ValidationError(_("The CRM team is not assigned to the target campaign."))

        values = {
            "campaign_id": target_campaign.id,
            "call_center_campaign_id": target_campaign.legacy_campaign_id.id,
            "business_unit_id": target_campaign.cc_business_unit_id.legacy_business_unit_id.id,
            "cc_customer_profile_id": target_profile.id if target_profile else False,
            "user_id": target_user.id if target_user else False,
            "team_id": target_team.id if target_team else False,
        }
        for field_name in (
            "assigned_agent_profile_id",
            "call_center_supervisor_id",
            "codestra_supervisor_id",
            "codestra_workflow_id",
            "codestra_current_status_id",
            "codestra_previous_status_id",
            "status_entered_at",
        ):
            if field_name in self._fields:
                values[field_name] = False

        self.with_context(
            _cc_crm_scope_capability=CRM_SCOPE_MIGRATION_CAPABILITY
        ).write(values)
        return True

    def action_codestra_transition(
        self,
        status_id,
        values=None,
        override_reason=None,
        actor_type="HUMAN",
        automation_id=None,
        model_or_service=None,
    ):
        governed = self.with_context(
            _cc_crm_transition_capability=CRM_TRANSITION_CAPABILITY
        )
        return super(CrmLead, governed).action_codestra_transition(
            status_id,
            values=values,
            override_reason=override_reason,
            actor_type=actor_type,
            automation_id=automation_id,
            model_or_service=model_or_service,
        )

    def copy(self, default=None):
        if any(self.mapped("cc_contact_center_record")):
            raise AccessError(_("Campaign CRM leads cannot be copied."))
        return super().copy(default)

    def unlink(self):
        if any(self.mapped("cc_contact_center_record")):
            maintenance_allowed = (
                self.env.context.get("_cc_crm_maintenance_delete_capability")
                is CRM_MAINTENANCE_DELETE_CAPABILITY
                and self.env.user.has_group("base.group_system")
            )
            if not maintenance_allowed:
                raise AccessError(
                    _("Campaign CRM leads are retained, not deleted; archive them instead.")
                )
        return super().unlink()

    def _callcenter_maintenance_unlink(self):
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Technical CRM deletion requires Odoo system administration."))
        return self.sudo().with_context(
            _cc_crm_maintenance_delete_capability=CRM_MAINTENANCE_DELETE_CAPABILITY
        ).unlink()

    def export_data(self, fields_to_export, raw_data=False):
        if _is_operational(self.env.user) and self.filtered(
            "cc_contact_center_record"
        ):
            raise UserError(_("Agent and supervisor bulk export is disabled."))
        return super().export_data(fields_to_export, raw_data=raw_data)

    @api.constrains(
        "campaign_id",
        "cc_customer_profile_id",
        "cc_contact_center_record",
        "call_center_campaign_id",
        "business_unit_id",
        "user_id",
        "codestra_workflow_id",
        "codestra_current_status_id",
        "assigned_agent_profile_id",
    )
    def _check_cc_campaign_scope(self):
        for lead in self:
            if lead.cc_contact_center_record and not lead.campaign_id:
                raise ValidationError(_("Campaign CRM records require a workspace."))
            if lead.cc_contact_center_record and not (
                lead.cc_source_list_key or ""
            ).strip():
                raise ValidationError(_("Campaign CRM records require a source list."))
            if lead.cc_customer_profile_id and (
                lead.cc_customer_profile_id.campaign_id != lead.campaign_id
            ):
                raise ValidationError(_("Lead and customer profile campaigns differ."))
            if lead.campaign_id and (
                lead.call_center_campaign_id != lead.campaign_id.legacy_campaign_id
                or lead.business_unit_id
                != lead.campaign_id.cc_business_unit_id.legacy_business_unit_id
            ):
                raise ValidationError(_("Canonical and legacy CRM ownership differ."))
            if lead.campaign_id and lead.user_id:
                membership = self.env["cc.campaign.membership"].search(
                    [
                        ("user_id", "=", lead.user_id.id),
                        ("campaign_id", "=", lead.campaign_id.id),
                        ("state", "=", "active"),
                        ("role", "in", ("agent", "senior_agent")),
                    ],
                    limit=1,
                )
                if not membership:
                    raise ValidationError(
                        _("CRM assignment requires an active same-campaign membership.")
                    )
            if lead.codestra_workflow_id and (
                lead.codestra_workflow_id.campaign_id != lead.call_center_campaign_id
            ):
                raise ValidationError(_("CRM workflow and campaign ownership differ."))
            if lead.codestra_current_status_id and (
                lead.codestra_current_status_id.workflow_id != lead.codestra_workflow_id
            ):
                raise ValidationError(_("CRM status and workflow ownership differ."))
            if lead.assigned_agent_profile_id and (
                lead.call_center_campaign_id
                not in lead.assigned_agent_profile_id.campaign_ids
            ):
                raise ValidationError(_("CRM agent profile is outside the campaign."))