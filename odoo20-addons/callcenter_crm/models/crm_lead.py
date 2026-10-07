import re
from psycopg2 import IntegrityError

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError
from odoo.tools import SQL


_PHONE_RE = re.compile(r"\D+")


class CrmLead(models.Model):
    _inherit = "crm.lead"

    cc_campaign_id = fields.Many2one(
        "callcenter.campaign", string="Call Center Campaign", index=True, ondelete="restrict"
    )
    source_batch_id = fields.Many2one(
        "callcenter.lead.import.batch", index=True, readonly=True, ondelete="restrict"
    )
    source_external_id = fields.Char(index=True, copy=False)
    queue_state = fields.Selection(
        [("available", "Available"), ("assigned", "Assigned"), ("callback", "Callback"),
         ("completed", "Completed"), ("blocked", "Blocked")],
        default="available", required=True, index=True,
    )
    next_eligible_at = fields.Datetime(index=True)
    call_attempt_count = fields.Integer(default=0)
    last_call_at = fields.Datetime()
    cross_campaign_duplicate = fields.Boolean(default=False, index=True, readonly=True)
    cc_phone_key = fields.Char(index=True, readonly=True, copy=False)
    cc_email_key = fields.Char(index=True, readonly=True, copy=False)
    cc_source_text = fields.Char(string="Imported Source")
    cc_assignment_history_ids = fields.One2many(
        "callcenter.lead.assignment", "lead_id", readonly=True
    )

    _unique_campaign_phone = models.UniqueIndex(
        "(cc_campaign_id, cc_phone_key) "
        "WHERE cc_campaign_id IS NOT NULL AND cc_phone_key IS NOT NULL",
        "The same phone number cannot appear twice inside one campaign.",
    )
    _unique_campaign_email_without_phone = models.UniqueIndex(
        "(cc_campaign_id, cc_email_key) "
        "WHERE cc_campaign_id IS NOT NULL AND cc_phone_key IS NULL AND cc_email_key IS NOT NULL",
        "The same email cannot appear twice inside one campaign when no phone is available.",
    )
    _unique_campaign_external_id = models.UniqueIndex(
        "(cc_campaign_id, source_external_id) "
        "WHERE cc_campaign_id IS NOT NULL AND source_external_id IS NOT NULL",
        "The same external ID cannot appear twice inside one campaign.",
    )
    _one_current_lead_per_agent = models.UniqueIndex(
        "(user_id) WHERE cc_campaign_id IS NOT NULL AND queue_state = 'assigned' AND active",
        "An agent may have only one currently assigned call-center lead.",
    )

    @api.model
    def _cc_phone_normalize(self, value):
        digits = _PHONE_RE.sub("", value or "")
        return digits or False

    @api.model
    def _cc_email_normalize(self, value):
        value = (value or "").strip().lower()
        return value or False

    @api.model
    def _cc_duplicate_domain(self, campaign_id, phone_key, email_key, external_id, exclude_ids=None):
        exclude_ids = exclude_ids or []
        checks = []
        if phone_key:
            checks.append(("cc_phone_key", "=", phone_key))
        elif email_key:
            checks.append(("cc_email_key", "=", email_key))
        if external_id:
            checks.append(("source_external_id", "=", external_id))
        return checks, [
            ("cc_campaign_id", "=", campaign_id),
            ("id", "not in", exclude_ids),
        ]

    @api.model
    def _cc_find_duplicate(self, campaign_id, phone_key=False, email_key=False, external_id=False, exclude_ids=None):
        checks, base = self._cc_duplicate_domain(
            campaign_id, phone_key, email_key, external_id, exclude_ids
        )
        Lead = self.sudo().with_context(active_test=False)
        for field, op, value in checks:
            match = Lead.search(base + [(field, op, value)], limit=1)
            if match:
                return match
        return Lead.browse()

    @api.model
    def _cc_has_cross_campaign_duplicate(self, campaign_id, phone_key=False, email_key=False, external_id=False, exclude_ids=None):
        exclude_ids = exclude_ids or []
        domain = [
            ("cc_campaign_id", "!=", False),
            ("cc_campaign_id", "!=", campaign_id),
            ("id", "not in", exclude_ids),
        ]
        Lead = self.sudo().with_context(active_test=False)
        candidates = []
        if phone_key:
            candidates.append(("cc_phone_key", "=", phone_key))
        elif email_key:
            candidates.append(("cc_email_key", "=", email_key))
        if external_id:
            candidates.append(("source_external_id", "=", external_id))
        return any(Lead.search_count(domain + [leaf], limit=1) for leaf in candidates)

    def _cc_is_superuser(self):
        return (
            self.env.is_superuser()
            or self.env.user.has_group("base.group_system")
            or self.env.user.has_group("callcenter_crm.group_callcenter_superuser")
        )

    @api.model
    def _cc_prepare_create_values(self, incoming):
        vals = dict(incoming)
        campaign_id = vals.get("cc_campaign_id")
        if not campaign_id:
            active_assignment = self.env["callcenter.campaign.assignment"].sudo().search([
                ("user_id", "=", self.env.user.id),
                ("role", "=", "agent"),
                ("active", "=", True),
            ], limit=1)
            if active_assignment and self.env.user.has_group("callcenter_crm.group_callcenter_agent"):
                campaign_id = active_assignment.campaign_id.id
                vals["cc_campaign_id"] = campaign_id
        if not campaign_id:
            return vals, False, False

        campaign = self.env["callcenter.campaign"].sudo().browse(campaign_id).exists()
        if not campaign:
            raise ValidationError(_("The selected call-center campaign does not exist."))
        vals["team_id"] = campaign.crm_team_id.id
        vals["source_external_id"] = (vals.get("source_external_id") or "").strip() or False
        phone_key = self._cc_phone_normalize(vals.get("phone") or vals.get("mobile"))
        email_key = self._cc_email_normalize(vals.get("email_from"))
        vals["cc_phone_key"] = phone_key
        vals["cc_email_key"] = email_key

        duplicate = self._cc_find_duplicate(
            campaign.id, phone_key, email_key, vals["source_external_id"]
        )
        if duplicate:
            raise ValidationError(
                _("Duplicate call-center lead in campaign %(campaign)s: %(lead)s",
                  campaign=campaign.display_name, lead=duplicate.display_name)
            )
        vals["cross_campaign_duplicate"] = self._cc_has_cross_campaign_duplicate(
            campaign.id, phone_key, email_key, vals["source_external_id"]
        )

        source = False
        if not self._cc_is_superuser():
            if campaign.state != "active":
                raise AccessError(_("Agents and supervisors may create leads only in an active campaign."))
            supervisor = self.env["callcenter.campaign.assignment"].sudo().search([
                ("campaign_id", "=", campaign.id), ("user_id", "=", self.env.user.id),
                ("role", "=", "supervisor"), ("active", "=", True),
            ], limit=1)
            agent = self.env["callcenter.campaign.assignment"].sudo().search([
                ("campaign_id", "=", campaign.id), ("user_id", "=", self.env.user.id),
                ("role", "=", "agent"), ("active", "=", True),
            ], limit=1)
            if supervisor:
                vals["user_id"] = False
                vals["queue_state"] = "available"
            elif agent:
                vals["user_id"] = self.env.user.id
                vals["queue_state"] = "assigned"
                source = "manual_agent_creation"
            else:
                raise AccessError(_("You are not assigned to the selected call-center campaign."))
            vals.pop("team_id", None)
            vals["team_id"] = campaign.crm_team_id.id
        else:
            vals.setdefault("queue_state", "assigned" if vals.get("user_id") else "available")
        return vals, campaign, source

    @api.model_create_multi
    def create(self, vals_list):
        prepared = []
        metadata = []
        for incoming in vals_list:
            vals, campaign, source = self._cc_prepare_create_values(incoming)
            prepared.append(vals)
            metadata.append((campaign, source))
        records = super().create(prepared)
        for lead, (campaign, source) in zip(records, metadata):
            if campaign and lead.user_id:
                lead._cc_open_assignment_history(source or "admin", _("Lead created with agent assignment"))
        return records

    def write(self, vals):
        callcenter_records = self.filtered("cc_campaign_id")
        if callcenter_records and not self._cc_is_superuser():
            raise AccessError(_("Saved call-center leads may only be edited by a Call Center Super User."))
        vals = dict(vals)
        old_users = {lead.id: lead.user_id.id for lead in self}
        old_campaigns = {lead.id: lead.cc_campaign_id.id for lead in self}

        if callcenter_records:
            for lead in callcenter_records:
                campaign_id = vals.get("cc_campaign_id", lead.cc_campaign_id.id)
                campaign = self.env["callcenter.campaign"].sudo().browse(campaign_id)
                vals["team_id"] = campaign.crm_team_id.id
                phone = vals.get("phone", lead.phone)
                mobile = vals.get("mobile", lead.mobile)
                email = vals.get("email_from", lead.email_from)
                external_id = (vals.get("source_external_id", lead.source_external_id) or "").strip() or False
                phone_key = self._cc_phone_normalize(phone or mobile)
                email_key = self._cc_email_normalize(email)
                duplicate = self._cc_find_duplicate(
                    campaign_id, phone_key, email_key, external_id, exclude_ids=self.ids
                )
                if duplicate:
                    raise ValidationError(_("Duplicate call-center lead inside the selected campaign."))
                vals["cc_phone_key"] = phone_key
                vals["cc_email_key"] = email_key
                vals["source_external_id"] = external_id
                vals["cross_campaign_duplicate"] = self._cc_has_cross_campaign_duplicate(
                    campaign_id, phone_key, email_key, external_id, exclude_ids=self.ids
                )
                break

        result = super().write(vals)
        if callcenter_records and ("user_id" in vals or "cc_campaign_id" in vals):
            for lead in self:
                if old_users.get(lead.id) != lead.user_id.id or old_campaigns.get(lead.id) != lead.cc_campaign_id.id:
                    lead._cc_sync_assignment_history("admin", _("Call Center Super User reassignment"))
        return result

    def unlink(self):
        if self.filtered("cc_campaign_id") and not (
            self.env.is_superuser() or self.env.user.has_group("base.group_system")
        ):
            raise AccessError(_("Call-center leads are archived, not physically deleted."))
        return super().unlink()

    def export_data(self, fields_to_export):
        if self.filtered("cc_campaign_id") and not self._cc_is_superuser():
            raise AccessError(_("Agents and supervisors may not export call-center lead data."))
        return super().export_data(fields_to_export)

    @api.model
    def load(self, fields_to_import, data):
        if (
            self.env.user.has_group("callcenter_crm.group_callcenter_agent")
            and not self._cc_is_superuser()
        ):
            raise AccessError(_("Use the controlled Campaign Lead Import workflow for call-center lead lists."))
        return super().load(fields_to_import, data)

    def _cc_open_assignment_history(self, source, reason=False, actor_id=None):
        History = self.env["callcenter.lead.assignment"].sudo()
        actor_id = actor_id or self.env.user.id
        for lead in self.filtered(lambda l: l.cc_campaign_id and l.user_id):
            History.create({
                "lead_id": lead.id,
                "campaign_id": lead.cc_campaign_id.id,
                "agent_id": lead.user_id.id,
                "assigned_at": fields.Datetime.now(),
                "assignment_source": source,
                "assigned_by_id": actor_id,
                "reason": reason or False,
            })

    def _cc_sync_assignment_history(self, source, reason=False, actor_id=None):
        History = self.env["callcenter.lead.assignment"].sudo()
        actor_id = actor_id or self.env.user.id
        for lead in self:
            open_rows = History.search([("lead_id", "=", lead.id), ("released_at", "=", False)])
            if open_rows:
                open_rows._release()
            if lead.cc_campaign_id and lead.user_id:
                lead._cc_open_assignment_history(source, reason, actor_id=actor_id)

    def _cc_system_update(self, values, assignment_source="queue", reason=False, actor_id=None):
        allowed = {
            "user_id", "team_id", "queue_state", "next_eligible_at",
            "call_attempt_count", "last_call_at",
        }
        if set(values) - allowed:
            raise AccessError(_("Invalid system lead update."))
        if not self or self.filtered(lambda l: not l.cc_campaign_id):
            raise ValidationError(_("System call-center updates require call-center leads."))
        old_users = {lead.id: lead.user_id.id for lead in self}
        result = super(CrmLead, self.sudo()).write(values)
        if "user_id" in values:
            for lead in self:
                if old_users[lead.id] != lead.user_id.id:
                    lead._cc_sync_assignment_history(
                        assignment_source, reason, actor_id=actor_id or self.env.user.id
                    )
        return result

    @api.model
    def action_cc_get_next_lead(self):
        user = self.env.user
        assignment = self.env["callcenter.campaign.assignment"].sudo().search([
            ("user_id", "=", user.id), ("role", "=", "agent"), ("active", "=", True)
        ], limit=1)
        if not assignment:
            raise AccessError(_("You do not have an active agent campaign assignment."))
        campaign = assignment.campaign_id
        if campaign.state != "active" or not campaign.active:
            raise ValidationError(_("Your campaign is not active."))

        current = self.sudo().search([
            ("cc_campaign_id", "!=", False), ("user_id", "=", user.id),
            ("queue_state", "=", "assigned"), ("active", "=", True),
        ], limit=1)
        if current:
            raise ValidationError(_("Finish or release the current lead before requesting another."))

        now = fields.Datetime.now()
        self.env.cr.execute(SQL(
            """
            SELECT id
              FROM crm_lead
             WHERE cc_campaign_id = %s
               AND active IS TRUE
               AND queue_state = 'available'
               AND user_id IS NULL
               AND (next_eligible_at IS NULL OR next_eligible_at <= %s)
             ORDER BY priority DESC NULLS LAST, create_date ASC, id ASC
             FOR UPDATE SKIP LOCKED
             LIMIT 1
            """,
            campaign.id, now,
        ))
        row = self.env.cr.fetchone()
        if not row:
            return False
        lead = self.sudo().browse(row[0])
        try:
            with self.env.cr.savepoint():
                lead._cc_system_update(
                    {"user_id": user.id, "team_id": campaign.crm_team_id.id, "queue_state": "assigned"},
                    assignment_source="queue",
                    reason=_("Get Next Lead"),
                    actor_id=user.id,
                )
        except IntegrityError as exc:
            raise ValidationError(_("A lead is already assigned to this agent.")) from exc
        return {
            "type": "ir.actions.act_window",
            "name": _("Assigned Lead"),
            "res_model": "crm.lead",
            "res_id": lead.id,
            "view_mode": "form",
            "target": "current",
        }
