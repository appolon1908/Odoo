import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timedelta, timezone

from odoo.tests import HttpCase, tagged
from odoo.tools import html2plaintext


@tagged("post_install", "-at_install")
class TestMiddlewareCrmIntakeHttp(HttpCase):
    secret = "synthetic-middleware-crm-secret"
    tenant = "synthetic-tenant"
    route = "/codestra/middleware/v1/commands/crm.lead.upsert"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.unit = cls.env["call.center.business.unit"].create({
            "name": "Synthetic Middleware Unit",
            "code": f"MW-{uuid.uuid4().hex[:8]}",
            "company_id": cls.env.company.id,
        })
        service_group = cls.env.ref(
            "codestra_middleware_bridge.group_codestra_crm_api"
        )
        cls.service_user = cls.env["res.users"].create({
            "name": "Synthetic Middleware CRM Service",
            "login": f"middleware-crm-{uuid.uuid4()}@example.invalid",
            "company_id": cls.env.company.id,
            "company_ids": [(6, 0, cls.env.company.ids)],
            "call_center_business_unit_ids": [(6, 0, cls.unit.ids)],
            "call_center_default_business_unit_id": cls.unit.id,
            "call_center_primary_role": "integration_service",
            "group_ids": [(6, 0, [cls.env.ref("base.group_user").id, service_group.id])],
        })
        cls.source = cls.env["utm.source"].create({"name": "synthetic-form"})
        cls.tag = cls.env["crm.tag"].create({"name": "synthetic-intake"})
        params = cls.env["ir.config_parameter"].sudo()
        params.set_param("codestra.middleware.tenant_id", cls.tenant)
        params.set_param("codestra.middleware.inbound_hmac_secret", cls.secret)
        params.set_param("codestra.crm.tenant_ids", cls.tenant)
        params.set_param("codestra.crm.service_user_id", cls.service_user.id)
        cls.env["cc.business.unit"]._adopt_legacy_records()
        cls.canonical_unit = cls.env["cc.business.unit"].with_context(
            active_test=False
        ).search([("legacy_business_unit_id", "=", cls.unit.id)], limit=1)
        cls.campaign_code = f"MWCAMP-{uuid.uuid4().hex[:8].upper()}"
        legacy_campaign = cls.env["call.center.campaign"].create({
            "name": "Synthetic Middleware Campaign",
            "code": cls.campaign_code,
            "business_unit_id": cls.unit.id,
            "active": True,
        })
        cls.campaign = cls.env["cc.campaign"].with_context(
            active_test=False
        ).search([("legacy_campaign_id", "=", legacy_campaign.id)], limit=1)
        # The service identity must stay narrow: it carries the internal-user
        # baseline and its own explicit ACLs, never the broad call centre or
        # all-leads sales roles.
        for forbidden in (
            "call_center_core.group_call_center_user",
            "sales_team.group_sale_salesman_all_leads",
            "call_center_core.group_call_center_manager",
            "call_center_core.group_call_center_admin",
        ):
            if cls.service_user.has_group(forbidden):
                raise AssertionError(
                    f"CRM service user must not hold {forbidden}"
                )
        if not cls.service_user.has_group("base.group_user"):
            raise AssertionError("CRM service user must remain an internal user")
        if not cls.service_user.has_group(
            "call_center_core.group_call_center_integration_service"
        ):
            raise AssertionError("CRM service user is missing integration scope")

    def command(
        self,
        *,
        consent_status="granted",
        allow_contact=True,
        review=False,
        campaign_code=None,
        source_record_id=None,
        captured_at=None,
    ):
        command_id = str(uuid.uuid4())
        return {
            "command_id": command_id,
            "command_type": "crm.lead.upsert",
            "command_version": "1.0",
            "target": "odoo-19",
            "tenant_id": self.tenant,
            "requested_by": "synthetic-middleware-test",
            "correlation_id": f"correlation-{command_id}",
            "idempotency_key": f"idempotency-{command_id}",
            "capability": "ODOO_WRITE",
            "payload": {
                "lead_source": self.source.name,
                "source_record_id": source_record_id or f"source-{command_id}",
                "initial_stage": "review_pending" if review else "new",
                "review_required": review,
                "allow_external_contact": allow_contact,
                "provenance": {
                    "method": "submitted_by_person",
                    "captured_by": "synthetic-form-service",
                    "source_reference": f"synthetic://form/{command_id}",
                    "legal_basis": "consent",
                    "content_digest": "a" * 64,
                },
                "consent": {
                    "status": consent_status,
                    "captured_at": (
                        captured_at or datetime.now(timezone.utc)
                    ).isoformat(),
                    "policy_version": "synthetic-v1",
                    "channels": {
                        "email": consent_status == "granted",
                        "sms": False,
                        "phone": consent_status == "granted",
                    },
                },
                "lead": {
                    "name": "Synthetic Middleware Lead",
                    "description": "Synthetic end-to-end intake evidence.",
                    "contact": {
                        "name": "Synthetic Contact",
                        "email": "synthetic@example.invalid",
                        "phone": "+18095550199",
                        "preferred_language": "en",
                    },
                    "company": {
                        "name": "Synthetic Company",
                        "domain": "example.invalid",
                        "industry": "Testing",
                    },
                    "campaign_code": campaign_code,
                    "tags": [self.tag.name],
                },
            },
        }

    def post(self, command, *, correlation_id=None, secret=None, tenant=None):
        raw = json.dumps(command, separators=(",", ":")).encode()
        timestamp = str(int(time.time()))
        event_id = command["command_id"]
        header_tenant = tenant or command["tenant_id"]
        header_correlation = correlation_id or command["correlation_id"]
        header_idempotency = command["idempotency_key"]
        canonical = b"\n".join((
            timestamp.encode(),
            event_id.encode(),
            b"POST",
            self.route.encode(),
            header_tenant.encode(),
            header_correlation.encode(),
            header_idempotency.encode(),
            raw,
        ))
        signature = hmac.new(
            (secret or self.secret).encode(), canonical, hashlib.sha256
        ).hexdigest()
        return self.url_open(
            self.route,
            data=raw,
            headers={
                "Content-Type": "application/json",
                "X-Codestra-Timestamp": timestamp,
                "X-Codestra-Event-ID": event_id,
                "X-Codestra-Signature": f"sha256={signature}",
                "X-Tenant-ID": header_tenant,
                "X-Correlation-ID": header_correlation,
                "Idempotency-Key": header_idempotency,
            },
            timeout=20,
        )

    def lead_for(self, command):
        self.env.invalidate_all()
        return self.env["crm.lead"].search([
            ("external_source_id", "=", command["payload"]["source_record_id"])
        ])

    def test_granted_intake_creates_real_lead_and_consent_ledger(self):
        command = self.command()
        response = self.post(command)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["outcome"], "created")
        lead = self.lead_for(command)
        self.assertEqual(len(lead), 1)
        self.assertEqual(lead.type, "lead")
        self.assertEqual(lead.business_unit_id, self.unit)
        self.assertEqual(lead.consent_status, "granted")
        self.assertFalse(lead.do_not_call)
        self.assertTrue(lead.codestra_allow_external_contact)
        self.assertEqual(lead.contact_eligibility, "eligible")
        self.assertEqual(set(lead.consent_ids.mapped("channel")), {"email", "phone"})
        self.assertEqual(set(lead.consent_ids.mapped("status")), {"granted"})

        duplicate = self.post(command)
        self.assertEqual(duplicate.status_code, 200, duplicate.text)
        self.assertTrue(duplicate.json()["duplicate"])
        self.assertEqual(len(self.lead_for(command)), 1)

    def test_denied_intake_creates_hashed_suppression_and_blocks_contact(self):
        command = self.command(consent_status="denied", allow_contact=False)
        response = self.post(command)
        self.assertEqual(response.status_code, 201, response.text)
        lead = self.lead_for(command)
        self.assertEqual(lead.consent_status, "denied")
        self.assertTrue(lead.do_not_call)
        self.assertEqual(lead.preferred_contact_method, "none")
        self.assertEqual(lead.contact_eligibility, "blocked")
        suppressions = self.env["call.center.suppression"].sudo().search([
            ("business_unit_id", "=", self.unit.id),
            ("source", "=", "synthetic-form-service"),
        ])
        self.assertEqual(
            set(suppressions.mapped("identifier_type")),
            {"phone", "email", "external_id"},
        )
        self.assertNotIn(lead.phone.lower(), suppressions.mapped("identifier_hash"))
        self.assertTrue(all(len(value) == 64 for value in suppressions.mapped("identifier_hash")))

    def test_review_pending_is_blocked_without_fabricating_suppression(self):
        command = self.command(
            consent_status="unknown", allow_contact=False, review=True
        )
        response = self.post(command)
        self.assertEqual(response.status_code, 201, response.text)
        lead = self.lead_for(command)
        self.assertTrue(lead.codestra_review_required)
        self.assertEqual(lead.codestra_initial_stage, "review_pending")
        self.assertEqual(lead.preferred_contact_method, "none")
        self.assertEqual(lead.contact_eligibility, "blocked")
        self.assertFalse(self.env["call.center.suppression"].sudo().search([
            ("business_unit_id", "=", self.unit.id),
            ("identifier_hash", "=", self.env["call.center.suppression"].sudo().hash_identifier(lead.phone)),
        ]))

    def test_command_and_signed_headers_must_match(self):
        command = self.command()
        response = self.post(command, correlation_id="different-correlation")
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["error"], "command_header_mismatch")
        self.assertFalse(self.lead_for(command))

    def test_campaign_code_binds_the_governed_campaign_workspace(self):
        command = self.command(campaign_code=self.campaign_code)
        response = self.post(command)
        self.assertEqual(response.status_code, 201, response.text)
        lead = self.lead_for(command)
        self.assertEqual(lead.campaign_id, self.campaign)
        self.assertEqual(lead.business_unit_id, self.unit)
        self.assertTrue(lead.cc_contact_center_record)
        self.assertTrue((lead.cc_source_list_key or "").strip())
        self.assertEqual(response.json()["campaign"], self.campaign.name)

    def test_unknown_campaign_code_is_rejected_deterministically(self):
        command = self.command(campaign_code="MWCAMP-DOESNOTEXIST")
        response = self.post(command)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["error"], "unknown_campaign")
        self.assertFalse(self.lead_for(command))

    def test_campaign_rebinding_is_a_conflict_not_a_server_error(self):
        first = self.command()
        self.assertEqual(self.post(first).status_code, 201)
        rebind = self.command(
            source_record_id=first["payload"]["source_record_id"],
            campaign_code=self.campaign_code,
        )
        response = self.post(rebind)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"], "campaign_binding_immutable")

    def test_saved_campaign_lead_cannot_be_updated_through_upsert_api(self):
        first = self.command(campaign_code=self.campaign_code)
        self.assertEqual(self.post(first).status_code, 201)
        original = self.lead_for(first)
        original_name = original.name

        update = self.command(
            source_record_id=first["payload"]["source_record_id"],
            campaign_code=self.campaign_code,
        )
        update["payload"]["lead"]["name"] = "Forbidden Middleware Rewrite"
        response = self.post(update)
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json()["error"], "saved_lead_immutable")

        self.env.invalidate_all()
        original = self.lead_for(first)
        self.assertEqual(original.name, original_name)

    def test_unknown_consent_cannot_authorize_external_contact(self):
        command = self.command(consent_status="unknown", allow_contact=True)
        response = self.post(command)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(
            response.json()["error"], "consent_does_not_permit_contact"
        )
        self.assertFalse(self.lead_for(command))

    def test_granted_consent_without_channels_cannot_authorize_contact(self):
        command = self.command(consent_status="granted", allow_contact=True)
        command["payload"]["consent"]["channels"] = {
            "email": False, "sms": False, "phone": False,
        }
        response = self.post(command)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(
            response.json()["error"], "consent_does_not_permit_contact"
        )
        self.assertFalse(self.lead_for(command))

    def test_older_command_cannot_overwrite_newer_consent(self):
        now = datetime.now(timezone.utc)
        first = self.command(captured_at=now)
        self.assertEqual(self.post(first).status_code, 201)
        stale = self.command(
            source_record_id=first["payload"]["source_record_id"],
            captured_at=now - timedelta(hours=1),
            consent_status="denied",
            allow_contact=False,
        )
        response = self.post(stale)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"], "stale_command")
        lead = self.lead_for(first)
        self.assertEqual(lead.consent_status, "granted")
        self.assertFalse(lead.do_not_call)

    def test_tenant_secret_is_not_shared_between_tenants(self):
        params = self.env["ir.config_parameter"].sudo()
        other_tenant = "synthetic-tenant-b"
        params.set_param(
            "codestra.crm.tenant_ids", f"{self.tenant},{other_tenant}"
        )
        params.set_param(
            f"codestra.middleware.tenant.{other_tenant}.inbound_hmac_secret",
            "synthetic-tenant-b-secret",
        )
        command = self.command()
        command["tenant_id"] = other_tenant
        borrowed = self.post(command, tenant=other_tenant)
        self.assertEqual(borrowed.status_code, 401, borrowed.text)
        self.assertEqual(borrowed.json()["error"], "invalid_signature")
        self.assertFalse(self.lead_for(command))

    def test_unlisted_tenant_is_rejected(self):
        command = self.command()
        command["tenant_id"] = "rogue-tenant"
        response = self.post(command, tenant="rogue-tenant")
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json()["error"], "tenant_rejected")
        self.assertFalse(self.lead_for(command))

    def configure_second_tenant(self, *, secret=None, user=None):
        tenant = "synthetic-tenant-b"
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("codestra.crm.tenant_ids", f"{self.tenant},{tenant}")
        if secret is not None:
            params.set_param(f"codestra.middleware.tenant.{tenant}.inbound_hmac_secret", secret)
        if user is not None:
            params.set_param(f"codestra.middleware.tenant.{tenant}.codestra.crm.service_user_id", user)
        return tenant

    def test_allowlist_alone_cannot_borrow_default_tenant_credentials(self):
        tenant = self.configure_second_tenant()
        command = self.command()
        command["tenant_id"] = tenant
        response = self.post(command, tenant=tenant)
        self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(response.json()["error"], "invalid_signature")
        self.assertFalse(self.lead_for(command))

    def test_secondary_tenant_needs_explicit_service_identity(self):
        secret = "synthetic-secondary-secret"
        tenant = self.configure_second_tenant(secret=secret)
        command = self.command()
        command["tenant_id"] = tenant
        response = self.post(command, tenant=tenant, secret=secret)
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json()["error"], "service_identity_rejected")
        self.assertFalse(self.lead_for(command))

    def test_secondary_tenant_explicit_credentials_preserve_authorized_intake(self):
        secret = "synthetic-secondary-secret"
        tenant = self.configure_second_tenant(secret=secret, user=self.service_user.id)
        command = self.command()
        command["tenant_id"] = tenant
        response = self.post(command, tenant=tenant, secret=secret)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertTrue(self.lead_for(command))

    def test_secondary_tenant_cannot_replay_default_tenant_response(self):
        command = self.command()
        self.assertEqual(self.post(command).status_code, 201)
        secret = "synthetic-secondary-secret"
        tenant = self.configure_second_tenant(secret=secret, user=self.service_user.id)
        # The signed header belongs to B; the repeated raw body belongs to A.
        # The global ledger collision must not return A's stored response.
        response = self.post(command, tenant=tenant, secret=secret)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"], "replayed_event_id")
        self.assertNotIn("lead_id", response.json())

    def test_cross_tenant_event_collision_is_controlled_before_business_write(self):
        original = self.command()
        self.assertEqual(self.post(original).status_code, 201)
        secret = "synthetic-secondary-secret"
        tenant = self.configure_second_tenant(secret=secret, user=self.service_user.id)
        command = self.command()
        command["command_id"] = original["command_id"]
        command["tenant_id"] = tenant
        response = self.post(command, tenant=tenant, secret=secret)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"], "replayed_event_id")
        self.assertFalse(self.lead_for(command))

    def test_invalid_tenant_service_user_fails_closed(self):
        secret = "synthetic-secondary-secret"
        tenant = self.configure_second_tenant(secret=secret, user="invalid-user-id")
        command = self.command()
        command["tenant_id"] = tenant
        response = self.post(command, tenant=tenant, secret=secret)
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json()["error"], "service_identity_rejected")
        self.assertFalse(self.lead_for(command))

    def get(self, path):
        timestamp = str(int(time.time()))
        event_id = str(uuid.uuid4())
        correlation = f"correlation-{event_id}"
        idempotency = f"idempotency-{event_id}"
        canonical = b"\n".join((
            timestamp.encode(),
            event_id.encode(),
            b"GET",
            path.encode(),
            self.tenant.encode(),
            correlation.encode(),
            idempotency.encode(),
            b"",
        ))
        signature = hmac.new(
            self.secret.encode(), canonical, hashlib.sha256
        ).hexdigest()
        return self.url_open(
            path,
            headers={
                "X-Codestra-Timestamp": timestamp,
                "X-Codestra-Event-ID": event_id,
                "X-Codestra-Signature": f"sha256={signature}",
                "X-Tenant-ID": self.tenant,
                "X-Correlation-ID": correlation,
                "Idempotency-Key": idempotency,
            },
            timeout=20,
        )

    def test_contract_subject_fields_are_persisted_not_discarded(self):
        command = self.command()
        response = self.post(command)
        self.assertEqual(response.status_code, 201, response.text)
        lead = self.lead_for(command)
        self.assertEqual(lead.codestra_preferred_language, "en")
        self.assertEqual(lead.codestra_company_domain, "example.invalid")
        self.assertEqual(lead.codestra_company_industry, "Testing")
        body = response.json()
        self.assertEqual(body["preferred_language"], "en")
        self.assertEqual(body["company_domain"], "example.invalid")
        self.assertEqual(body["company_industry"], "Testing")

    def test_malformed_nested_value_is_rejected_deterministically(self):
        command = self.command()
        command["payload"]["lead"]["contact"]["email"] = {"unexpected": "object"}
        response = self.post(command)
        self.assertEqual(response.status_code, 422, response.text)
        body = response.json()
        self.assertEqual(body["error"], "invalid_lead_subject_value")
        self.assertEqual(body["section"], "contact")
        self.assertEqual(body["field"], "email")
        self.assertFalse(self.lead_for(command))

    def test_oversized_nested_value_is_rejected_deterministically(self):
        command = self.command()
        command["payload"]["lead"]["company"]["domain"] = "d" * 257
        response = self.post(command)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["error"], "invalid_lead_subject_value")
        self.assertFalse(self.lead_for(command))

    def test_command_status_reports_the_recorded_outcome(self):
        command = self.command()
        created = self.post(command)
        self.assertEqual(created.status_code, 201, created.text)
        status = self.get(
            f"/codestra/middleware/v1/commands/{command['command_id']}/status"
        )
        self.assertEqual(status.status_code, 200, status.text)
        body = status.json()
        self.assertEqual(body["command_id"], command["command_id"])
        self.assertEqual(body["operation"], "crm.lead.upsert")
        self.assertEqual(body["result"]["outcome"], "created")
        self.assertEqual(
            body["result"]["external_id"], command["payload"]["source_record_id"]
        )

    def test_unknown_command_status_is_not_found(self):
        status = self.get(
            f"/codestra/middleware/v1/commands/{uuid.uuid4()}/status"
        )
        self.assertEqual(status.status_code, 404, status.text)
        self.assertEqual(status.json()["error"], "command_not_found")

    def test_contract_maximum_lengths_are_accepted(self):
        command = self.command()
        local_part = "a" * (320 - len("@example.invalid"))
        command["payload"]["lead"]["contact"]["email"] = (
            f"{local_part}@example.invalid"
        )
        command["payload"]["lead"]["description"] = "d" * 10000
        response = self.post(command)
        self.assertEqual(response.status_code, 201, response.text)
        lead = self.lead_for(command)
        self.assertEqual(len(lead.email_from), 320)
        self.assertEqual(len(html2plaintext(lead.description)), 10000)

    def test_value_beyond_the_contract_maximum_is_rejected(self):
        command = self.command()
        command["payload"]["lead"]["contact"]["preferred_language"] = "e" * 17
        response = self.post(command)
        self.assertEqual(response.status_code, 422, response.text)
        body = response.json()
        self.assertEqual(body["error"], "invalid_lead_subject_value")
        self.assertEqual(body["field"], "preferred_language")
        self.assertFalse(self.lead_for(command))
