from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.parse
import urllib.request
import uuid
from datetime import timezone
from zoneinfo import ZoneInfo

from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError


class ResPartner(models.Model):
    _inherit = "res.partner"

    codestra_integration_external_id = fields.Char(index=True, copy=False)
    codestra_integration_status = fields.Selection(
        [("test", "Test"), ("active", "Active"), ("inactive", "Inactive")],
        default="test",
        copy=False,
    )

    _codestra_external_unique = models.Constraint(
        "UNIQUE(codestra_integration_external_id)",
        "Middleware external contact IDs must be unique.",
    )


class CrmLead(models.Model):
    _inherit = "crm.lead"

    codestra_form_type = fields.Char(index=True, readonly=True, copy=False)
    codestra_form_version = fields.Char(readonly=True, copy=False)
    codestra_source_site = fields.Char(index=True, readonly=True, copy=False)
    codestra_consent_timestamp = fields.Datetime(readonly=True, copy=False)
    codestra_consent_disclosure_version = fields.Char(readonly=True, copy=False)
    codestra_sms_consent = fields.Boolean(readonly=True, copy=False)
    codestra_email_marketing_consent = fields.Boolean(readonly=True, copy=False)
    codestra_phone_consent = fields.Boolean(readonly=True, copy=False)
    codestra_consent_correlation_id = fields.Char(index=True, readonly=True, copy=False)
    codestra_allow_external_contact = fields.Boolean(readonly=True, copy=False)
    codestra_review_required = fields.Boolean(readonly=True, copy=False)
    codestra_initial_stage = fields.Selection(
        [("new", "New"), ("review_pending", "Review Pending")],
        readonly=True,
        copy=False,
    )
    codestra_requested_by = fields.Char(readonly=True, copy=False)
    codestra_provenance_method = fields.Selection(
        [
            ("submitted_by_person", "Submitted by Person"),
            ("crawler_discovery", "Crawler Discovery"),
            ("scraper_import", "Scraper Import"),
        ],
        readonly=True,
        copy=False,
    )
    codestra_provenance_reference = fields.Char(readonly=True, copy=False)
    codestra_provenance_legal_basis = fields.Selection(
        [
            ("consent", "Consent"),
            ("legitimate_interest_review_required", "Legitimate Interest — Review Required"),
            ("contract_request", "Contract Request"),
            ("unknown_review_required", "Unknown — Review Required"),
        ],
        readonly=True,
        copy=False,
    )
    codestra_provenance_digest = fields.Char(readonly=True, copy=False)
    # Accepted by the Middleware contract; previously parsed and discarded.
    codestra_preferred_language = fields.Char(readonly=True, copy=False)
    codestra_company_domain = fields.Char(index=True, readonly=True, copy=False)
    codestra_company_industry = fields.Char(readonly=True, copy=False)

    consent_status = fields.Selection(
        selection_add=[
            ("denied", "Denied"),
            ("not_applicable", "Not Applicable"),
        ],
        ondelete={"denied": "set default", "not_applicable": "set default"},
    )

    @api.model
    def _codestra_crm_compliance_snapshot_values(
        self, payload, unit, campaign_id=False
    ):
        """Compute the immutable lead compliance snapshot before first save."""
        consent_status = payload.get("consent_status", "unknown")
        allow_external_contact = payload.get("allow_external_contact", False)
        do_not_call = bool(payload.get("do_not_call") or consent_status == "denied")
        channels = {
            "phone": bool(payload.get("phone_consent")),
            "email": bool(payload.get("email_marketing_consent")),
            "sms": bool(payload.get("sms_consent")),
        }
        permitted = consent_status == "granted" and any(channels.values())
        preferred = (
            next(channel for channel in ("phone", "email", "sms") if channels[channel])
            if permitted and allow_external_contact and not do_not_call
            else "none"
        )
        reason = payload.get("suppression_reason", "optout")
        reasons = []
        if do_not_call or preferred == "none":
            reasons.append("do_not_contact")

        suppression_model = self.env["call.center.suppression"]
        for identity_type, identity in (
            ("phone", payload.get("phone")),
            ("email", payload.get("email")),
            ("external_id", payload.get("external_id")),
        ):
            digest = suppression_model.hash_identifier(identity)
            if digest and suppression_model.search_count(
                [
                    ("business_unit_id", "=", unit.id),
                    ("identifier_type", "=", identity_type),
                    ("identifier_hash", "=", digest),
                    ("active", "=", True),
                    "|",
                    ("expires_at", "=", False),
                    ("expires_at", ">", fields.Datetime.now()),
                ]
            ):
                reasons.append(f"suppressed_{identity_type}")

        campaign = (
            self.env["cc.campaign"].browse(campaign_id).exists()
            if campaign_id
            else self.env["cc.campaign"]
        )
        legacy_campaign_id = campaign.legacy_campaign_id.id if campaign else False
        rule = self.env["call.center.compliance.rule"].search(
            [
                ("active", "=", True),
                ("business_unit_id", "=", unit.id),
                "|",
                ("campaign_id", "=", legacy_campaign_id),
                ("campaign_id", "=", False),
            ],
            order="priority",
            limit=1,
        )
        if rule and rule.consent_required and not (
            consent_status == "granted" and channels["phone"]
        ):
            reasons.append("consent_required")

        state = "blocked" if reasons else "eligible"
        if not reasons and rule:
            now_utc = fields.Datetime.now().replace(tzinfo=timezone.utc)
            timezone_name = (
                (campaign and campaign.legacy_campaign_id.timezone)
                or unit.timezone
                or "UTC"
            )
            local_now = now_utc.astimezone(ZoneInfo(timezone_name))
            local_hour = local_now.hour + (local_now.minute / 60)
            if not rule.calling_hour_start <= local_hour < rule.calling_hour_end:
                state = "outside_hours"
                reasons.append("outside_calling_hours")

        return {
            "consent_status": consent_status,
            "do_not_call": do_not_call,
            "do_not_contact_reason": (
                reason
                if do_not_call
                else "middleware_contact_not_allowed"
                if not allow_external_contact
                else False
            ),
            "preferred_contact_method": preferred,
            "contact_eligibility": state,
            "contact_eligibility_reason": ",".join(reasons) or False,
            "contact_eligibility_checked_at": fields.Datetime.now(),
        }

    def _codestra_apply_crm_compliance(self, auth, payload, unit):
        """Create compliance ledgers without mutating an immutable saved lead."""
        self.ensure_one()
        snapshot = self._codestra_crm_compliance_snapshot_values(
            payload, unit, self.campaign_id.id if self.campaign_id else False
        )
        locked = bool(self.cc_contact_center_record)
        if locked:
            for field_name in (
                "consent_status",
                "do_not_call",
                "do_not_contact_reason",
                "preferred_contact_method",
                "contact_eligibility",
                "contact_eligibility_reason",
            ):
                if self[field_name] != snapshot[field_name]:
                    raise AccessError(
                        "Saved call-center lead compliance differs from its immutable creation snapshot."
                    )
        else:
            self.write(snapshot)

        consent_status = snapshot["consent_status"]
        do_not_call = snapshot["do_not_call"]
        channels = {
            "phone": bool(payload.get("phone_consent")),
            "email": bool(payload.get("email_marketing_consent")),
            "sms": bool(payload.get("sms_consent")),
        }
        reason = payload.get("suppression_reason", "optout")
        user = auth["user"]
        if consent_status in {"granted", "denied"}:
            consent_model = self.env["call.center.consent"].with_user(user)
            evidence = (
                payload.get("consent_evidence_reference")
                or payload.get("consent_correlation_id")
                or auth["correlation_id"]
            )
            selected = (
                [channel for channel, granted in channels.items() if granted]
                if consent_status == "granted"
                else list(channels)
            )
            for channel in selected:
                consent_model.create(
                    {
                        "lead_id": self.id,
                        "business_unit_id": unit.id,
                        "channel": channel,
                        "status": consent_status,
                        "consented_at": payload.get("consent_timestamp")
                        or fields.Datetime.now(),
                        "source": payload.get("consent_source")
                        or "codestra-middleware",
                        "evidence_reference": evidence,
                    }
                )

        identifiers = []
        if do_not_call or consent_status == "denied":
            identifiers.append(("phone", self.phone))
        if consent_status == "denied":
            identifiers.extend(
                (
                    ("email", self.email_from),
                    ("external_id", self.external_source_id),
                )
            )
        suppression_model = self.env["call.center.suppression"].with_user(user)
        for identity_type, identity in identifiers:
            digest = suppression_model.hash_identifier(identity)
            if not digest:
                continue
            domain = [
                ("business_unit_id", "=", unit.id),
                ("identifier_type", "=", identity_type),
                ("identifier_hash", "=", digest),
            ]
            suppression = suppression_model.search(domain, limit=1)
            values = {
                "reason": reason,
                "source": payload.get("consent_source") or "codestra-middleware",
                "active": True,
            }
            if suppression:
                suppression.write(values)
            else:
                suppression_model.create(
                    {
                        **values,
                        "business_unit_id": unit.id,
                        "identifier_type": identity_type,
                        "identifier_hash": digest,
                    }
                )

        if locked:
            self.env["call.center.audit.event"].sudo().create(
                {
                    "business_unit_id": self.business_unit_id.id,
                    "event_type": "lead.contact_eligibility.checked",
                    "model_name": self._name,
                    "record_id": self.id,
                    "new_values_json": {
                        "state": self.contact_eligibility,
                        "reasons": (
                            self.contact_eligibility_reason.split(",")
                            if self.contact_eligibility_reason
                            else []
                        ),
                    },
                }
            )
        else:
            self.action_check_contact_eligibility()



class CallCenterConsent(models.Model):
    _inherit = "call.center.consent"

    status = fields.Selection(
        selection_add=[("denied", "Denied")],
        ondelete={"denied": "set default"},
    )

class MiddlewareRequest(models.Model):
    _name = "codestra.middleware.request"
    _description = "Codestra Middleware Idempotency Record"
    _order = "id desc"

    event_id = fields.Char(required=True, index=True, readonly=True)
    idempotency_key = fields.Char(required=True, index=True, readonly=True)
    request_hash = fields.Char(required=True, readonly=True)
    operation = fields.Char(required=True, readonly=True)
    correlation_id = fields.Char(required=True, index=True, readonly=True)
    tenant_id = fields.Char(required=True, index=True, readonly=True)
    response_json = fields.Text(required=True, readonly=True)
    partner_id = fields.Many2one("res.partner", ondelete="restrict", readonly=True)
    created_at = fields.Datetime(default=fields.Datetime.now, required=True, readonly=True)

    _event_unique = models.Constraint("UNIQUE(event_id)", "Middleware event IDs cannot be replayed.")
    _idempotency_unique = models.Constraint(
        "UNIQUE(tenant_id,idempotency_key)",
        "Middleware idempotency keys are tenant scoped and unique.",
    )

    def write(self, values):
        raise AccessError("Middleware request evidence is immutable.")

    def unlink(self):
        raise AccessError("Middleware request evidence is immutable.")


class EmailDeliveryStatus(models.Model):
    _name = "codestra.email.delivery.status"
    _description = "Codestra Email Delivery Status"
    _order = "occurred_at desc, id desc"

    event_id = fields.Char(required=True, index=True, readonly=True)
    correlation_id = fields.Char(required=True, index=True, readonly=True)
    message_id = fields.Char(required=True, index=True, readonly=True)
    tenant_id = fields.Char(required=True, index=True, readonly=True)
    customer_id = fields.Char(index=True, readonly=True)
    provider = fields.Char(required=True, readonly=True, default="klyrow")
    event_type = fields.Char(required=True, readonly=True)
    status = fields.Selection(
        [
            ("accepted", "Accepted"), ("queued", "Queued"),
            ("provider_accepted", "Provider Accepted"),
            ("delivered", "Delivered"), ("deferred", "Deferred"),
            ("bounced", "Bounced"), ("complained", "Complained"),
            ("suppressed", "Suppressed"), ("rejected", "Rejected"),
            ("failed", "Failed"),
        ],
        required=True, readonly=True, index=True,
    )
    occurred_at = fields.Datetime(required=True, readonly=True)

    _event_unique = models.Constraint("UNIQUE(event_id)", "Email delivery event IDs must be unique.")

    def write(self, values):
        raise AccessError("Email delivery evidence is immutable.")

    def unlink(self):
        raise AccessError("Email delivery evidence is immutable.")


class CrmExternalMapping(models.Model):
    _name = "codestra.crm.external.mapping"
    _description = "Codestra CRM External Object Mapping"
    _order = "id desc"

    customer_key = fields.Char(required=True, index=True, readonly=True)
    external_id = fields.Char(required=True, index=True, readonly=True)
    middleware_id = fields.Char(required=True, index=True, readonly=True)
    model = fields.Selection([("crm.lead", "CRM Lead")], required=True, readonly=True)
    record_id = fields.Integer(required=True, index=True, readonly=True)
    company_id = fields.Many2one("res.company", required=True, readonly=True)
    business_unit_id = fields.Many2one("call.center.business.unit", required=True, readonly=True)
    service_user_id = fields.Many2one("res.users", required=True, readonly=True)
    created_at = fields.Datetime(default=fields.Datetime.now, required=True, readonly=True)

    _customer_external_unique = models.Constraint(
        "UNIQUE(customer_key,external_id,model)", "External CRM object IDs are tenant scoped and unique."
    )
    _middleware_unique = models.Constraint("UNIQUE(middleware_id)", "Middleware object IDs are unique.")

    def write(self, values):
        raise AccessError("CRM mappings are immutable.")

    def unlink(self):
        raise AccessError("CRM mappings are immutable.")


class MiddlewareOutbound(models.AbstractModel):
    _name = "codestra.middleware.outbound"
    _description = "Odoo to Codestra Middleware Event Client"

    @api.model
    def _validated_target(self, value):
        parsed = urllib.parse.urlsplit(value or "")
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValidationError(
                "Middleware event URL must be a credential-free HTTPS endpoint."
            )
        return value

    @api.model
    def emit_synthetic_event(self, event_type, correlation_id, idempotency_key, payload):
        if not str(event_type).startswith(("customer.", "campaign.")):
            raise ValidationError("Unsupported Odoo middleware event type.")
        if not payload.get("synthetic_test"):
            raise ValidationError("This method is restricted to tagged synthetic events.")
        params = self.env["ir.config_parameter"].sudo()
        target = params.get_param("codestra.middleware.event_url")
        api_key = params.get_param("codestra.middleware.api_key")
        secret = params.get_param("codestra.middleware.webhook_secret")
        tenant = params.get_param("codestra.middleware.tenant_id")
        if not all((target, api_key, secret, tenant)):
            raise ValidationError("Middleware outbound identity is not configured.")
        target = self._validated_target(target)
        event_id = "odoo-" + uuid.uuid4().hex
        envelope = {
            "event_type": event_type,
            "event_version": "1.0",
            "occurred_at": fields.Datetime.now().isoformat() + "Z",
            "tenant_id": tenant,
            "customer_id": payload.get("customer_id"),
            "correlation_id": correlation_id,
            "idempotency_key": idempotency_key,
            "payload": payload,
            "metadata": {"source": "odoo", "synthetic_test": True},
        }
        raw = json.dumps(envelope, separators=(",", ":")).encode()
        stamp = str(int(time.time()))
        canonical = b"\n".join((stamp.encode(), event_id.encode(), b"odoo", raw))
        signature = hmac.new(secret.encode(), canonical, hashlib.sha256).hexdigest()
        request = urllib.request.Request(target, raw, {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + api_key,
            "X-Event-Id": event_id,
            "X-Timestamp": stamp,
            "X-Signature": "sha256=" + signature,
        }, method="POST")
        # _validated_target rejects non-HTTPS and credential-bearing authorities.
        with urllib.request.urlopen(request, timeout=5) as response:  # nosec B310
            result = json.loads(response.read())
            status = response.status
        event = self.env["codestra.integration.event"].register_event(
            event_type, "odoo", "middleware", payload,
            correlation_id=correlation_id, idempotency_key=idempotency_key,
        )
        self.env["codestra.integration.audit"]._append(
            event, "middleware.delivery", "success",
            {"http_status": status, "event_id": event_id},
        )
        return {"http_status": status, "event_id": event_id, "result": result}
