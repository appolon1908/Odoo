from __future__ import annotations

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class CrmLead(models.Model):
    _inherit = "crm.lead"

    codestra_leads_id = fields.Char(
        string="Leads Workstation ID", index=True, copy=False, readonly=True
    )
    codestra_leads_version = fields.Integer(
        string="Leads Workstation Version", default=0, copy=False, readonly=True
    )
    codestra_leads_status = fields.Char(
        string="Leads Lifecycle Status", copy=False, readonly=True
    )
    codestra_leads_campaign_id = fields.Char(
        string="Leads Campaign ID", index=True, copy=False, readonly=True
    )
    codestra_leads_verification_status = fields.Char(
        string="Leads Verification", copy=False, readonly=True
    )
    codestra_leads_suppressed = fields.Boolean(
        string="Suppressed", copy=False, readonly=True
    )
    codestra_leads_do_not_contact = fields.Boolean(
        string="Do Not Contact", copy=False, readonly=True
    )
    codestra_leads_last_sync_at = fields.Datetime(
        string="Leads Last Sync", copy=False, readonly=True
    )

    _codestra_leads_id_unique = models.Constraint(
        "UNIQUE(codestra_leads_id)",
        "Leads Workstation IDs must be unique in Odoo CRM.",
    )

    @api.model
    def apply_leads_workstation_projection(self, payload):
        lead_id = str(payload.get("lead_id") or "").strip()
        if not lead_id:
            raise ValidationError("lead_id is required")
        try:
            version = int(payload.get("version"))
        except (TypeError, ValueError) as exc:
            raise ValidationError("version must be an integer") from exc
        if version < 1:
            raise ValidationError("version must be positive")

        record = self.search([("codestra_leads_id", "=", lead_id)], limit=1)
        if record and version < record.codestra_leads_version:
            raise ValidationError("stale Leads Workstation version")
        if record and version == record.codestra_leads_version:
            return record, False

        vals = {
            "codestra_leads_id": lead_id,
            "codestra_leads_version": version,
            "codestra_leads_status": str(payload.get("status") or ""),
            "codestra_leads_campaign_id": str(payload.get("campaign_id") or ""),
            "codestra_leads_verification_status": str(
                payload.get("verification_status") or ""
            ),
            "codestra_leads_suppressed": bool(payload.get("suppressed")),
            "codestra_leads_do_not_contact": bool(payload.get("do_not_contact")),
            "codestra_leads_last_sync_at": fields.Datetime.now(),
        }
        mapping = {
            "business_name": "name",
            "contact_name": "contact_name",
            "email": "email_from",
            "phone": "phone",
        }
        for source, target in mapping.items():
            if source in payload and payload[source] is not None:
                vals[target] = payload[source]

        created = not bool(record)
        if created:
            vals.setdefault("name", payload.get("contact_name") or lead_id)
            record = self.create(vals)
        else:
            record.write(vals)
        return record, created
