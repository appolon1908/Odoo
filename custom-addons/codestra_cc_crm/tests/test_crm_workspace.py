import base64

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from ..models.crm_workspace import (
    CRM_QUEUE_SYSTEM_CAPABILITY,
    CRM_SCOPE_MIGRATION_CAPABILITY,
    CRM_TRANSITION_CAPABILITY,
)


@tagged("post_install", "-at_install")
class TestCampaignCrmWorkspace(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["cc.business.unit"]._adopt_legacy_records()
        cls.Campaign = cls.env["cc.campaign"].with_context(active_test=False)
        cls.Profile = cls.env["cc.customer.profile"]
        cls.Lead = cls.env["crm.lead"]
        cls.campaign_a = cls.Campaign.search([("code", "=", "COD-WEB-OUT")], limit=1)
        cls.campaign_b = cls.Campaign.search(
            [("id", "!=", cls.campaign_a.id)], limit=1
        )
        cls.requester = cls._create_user(
            "CRM Requester",
            "cc-crm-requester@example.invalid",
            ["codestra_cc_security.group_cc_global_administrator"],
        )
        cls.approver = cls._create_user(
            "CRM Approver",
            "cc-crm-approver@example.invalid",
            ["codestra_cc_security.group_cc_global_administrator"],
        )
        cls.call_center_superuser = cls._create_user(
            "CRM Saved Lead Super User",
            "cc-crm-saved-lead-superuser@example.invalid",
            ["codestra_cc_security.group_cc_call_center_superuser"],
        )
        cls.technical_admin = cls._create_user(
            "CRM Technical Administrator",
            "cc-crm-technical@example.invalid",
            ["codestra_cc_security.group_cc_technical_administrator"],
        )
        cls.service = cls._create_user(
            "CRM Identity Service",
            "cc-crm-service@example.invalid",
            [
                "base.group_user",
                "codestra_identity_provisioning.group_provisioning_service",
                "codestra_cc_crm.group_cc_crm_service",
            ],
        )
        cls.agent_a = cls._create_user(
            "CRM Agent A",
            "cc-crm-agent-a@example.invalid",
            ["codestra_cc_security.group_cc_campaign_agent"],
        )
        cls.agent_a2 = cls._create_user(
            "CRM Agent A2",
            "cc-crm-agent-a2@example.invalid",
            ["codestra_cc_security.group_cc_campaign_agent"],
        )
        cls.agent_b = cls._create_user(
            "CRM Agent B",
            "cc-crm-agent-b@example.invalid",
            ["codestra_cc_security.group_cc_campaign_agent"],
        )
        cls.supervisor_a = cls._create_user(
            "CRM Supervisor A",
            "cc-crm-supervisor-a@example.invalid",
            ["codestra_cc_security.group_cc_campaign_supervisor"],
        )
        cls._activate_membership(cls.agent_a, cls.campaign_a, "CRM-MEMBER-A")
        cls._activate_membership(cls.agent_a2, cls.campaign_a, "CRM-MEMBER-A2")
        cls._activate_membership(cls.agent_b, cls.campaign_b, "CRM-MEMBER-B")
        cls._activate_membership(
            cls.supervisor_a,
            cls.campaign_a,
            "CRM-SUPERVISOR-A",
            role="supervisor",
            primary=True,
        )
        cls.partner_a = cls.env["res.partner"].create(
            {
                "name": "Synthetic CRM Customer A",
                "email": "customer-a@example.invalid",
                "phone": "+1 555 000 1001",
            }
        )
        cls.partner_b = cls.env["res.partner"].create(
            {
                "name": "Synthetic CRM Customer B",
                "email": "customer-b@example.invalid",
                "phone": "+1 555 000 2002",
            }
        )
        cls.profile_a = cls.Profile.with_user(cls.requester).create_from_partner(
            cls.partner_a, cls.campaign_a, "crm-profile-a"
        )
        cls.profile_b = cls.Profile.with_user(cls.requester).create_from_partner(
            cls.partner_b, cls.campaign_b, "crm-profile-b"
        )
        cls.profile_a.with_user(cls.requester).write(
            {"assigned_user_id": cls.agent_a.id}
        )
        cls.profile_b.with_user(cls.requester).write(
            {"assigned_user_id": cls.agent_b.id}
        )
        cls.lead_a = cls.Lead.with_user(cls.requester).create(
            {
                "name": "Campaign A CRM lead",
                "cc_customer_profile_id": cls.profile_a.id,
                "user_id": cls.agent_a.id,
                "cc_source_list_key": "synthetic-list-a",
            }
        )
        cls.lead_b = cls.Lead.with_user(cls.requester).create(
            {
                "name": "Campaign B CRM lead",
                "cc_customer_profile_id": cls.profile_b.id,
                "user_id": cls.agent_b.id,
                "cc_source_list_key": "synthetic-list-b",
            }
        )
        cls.unassigned_a = cls.Lead.with_user(cls.requester).create(
            {
                "name": "Campaign A unassigned lead",
                "campaign_id": cls.campaign_a.id,
                "user_id": False,
                "queue_state": "available",
                "cc_source_list_key": "synthetic-unassigned-a",
            }
        )

    @classmethod
    def _create_user(cls, name, login, group_xmlids):
        groups = cls.env["res.groups"].browse(
            [cls.env.ref(xmlid).id for xmlid in group_xmlids]
        )
        return cls.env["res.users"].create(
            {"name": name, "login": login, "group_ids": [(6, 0, groups.ids)]}
        )

    @classmethod
    def _activate_membership(
        cls, user, campaign, ticket, role="agent", primary=False
    ):
        employee = cls.env["hr.employee"].create(
            {"name": user.name, "user_id": user.id, "company_id": cls.env.company.id}
        )
        membership = cls.env["cc.campaign.membership"].with_user(
            cls.requester
        ).create(
            {
                "user_id": user.id,
                "employee_id": employee.id,
                "campaign_id": campaign.id,
                "role": role,
                "is_primary_supervisor": primary,
                "requested_by_id": cls.requester.id,
                "source_ticket": ticket,
                "starts_at": fields.Datetime.now(),
            }
        )
        membership.with_user(cls.requester).action_submit_identity()
        operation = membership.with_user(cls.approver).action_approve_identity()
        operation.with_user(cls.service).action_record_readback(
            {
                target: {"status": "matched", "evidence_hash": "a" * 64}
                for target in operation.required_targets
            },
            f"staging://crm/{ticket.lower()}",
        )
        membership.with_user(cls.approver).action_activate()
        return membership

    def _other_company_campaign(self):
        company = self.env["res.company"].create({"name": "Queue Other Company"})
        unit = self.env["cc.business.unit"].sudo().create(
            {"name": "Queue Other Unit", "code": "QUEUE-OTHER", "company_id": company.id}
        )
        campaign = self.Campaign.sudo().create(
            {
                "name": "Queue Other Campaign",
                "code": "QUEUE-OTHER-OUT",
                "cc_business_unit_id": unit.id,
                "design_automation_enabled": False,
            }
        )
        return company, campaign

    def test_profile_masks_contact_and_restricts_authoritative_link(self):
        self.assertEqual(self.profile_a.email_masked, "c***@example.invalid")
        self.assertEqual(self.profile_a.phone_masked, "***1001")
        self.assertEqual(len(self.profile_a.partner_reference_hash), 64)
        with self.assertRaises(AccessError):
            self.profile_a.with_user(self.agent_a).read(["partner_id"])
        with self.assertRaises(UserError):
            self.profile_a.with_user(self.agent_a).export_data(["name"])
        with self.assertRaises(AccessError):
            self.profile_a.with_user(self.requester).write(
                {"campaign_id": self.campaign_b.id}
            )
        with self.assertRaises(AccessError):
            self.profile_a.with_user(self.requester).with_context(
                _cc_crm_scope_capability=True
            ).write({"campaign_id": self.campaign_b.id})
        with self.assertRaises(AccessError):
            self.profile_a.with_user(self.requester).with_context(
                _cc_profile_write_capability=True
            ).write({"name": "Forged customer identity"})
        with self.assertRaises(ValidationError):
            self.profile_a.with_user(self.agent_a).write(
                {"verification_checklist": {"security_code": "prohibited"}}
            )
        self.profile_a.with_user(self.agent_a).write(
            {
                "verification_state": "verified",
                "verification_checklist": {"identity_confirmed": True},
            }
        )
        self.assertEqual(self.profile_a.verification_state, "verified")

    def test_agent_query_surfaces_are_campaign_and_assignment_scoped(self):
        ProfileA = self.Profile.with_user(self.agent_a)
        self.assertEqual(ProfileA.search([]), self.profile_a)
        self.assertFalse(ProfileA.search([("id", "=", self.profile_b.id)]))
        self.assertFalse(
            ProfileA.name_search(self.profile_b.name, operator="=", limit=10)
        )
        grouped = ProfileA._read_group([], ["state"], ["__count"])
        self.assertEqual(sum(count for _state, count in grouped), 1)
        with self.assertRaises(AccessError):
            self.profile_b.with_user(self.agent_a).read(["display_name"])
        with self.assertRaises(AccessError):
            self.profile_b.with_user(self.agent_a).copy()

    def test_crm_campaign_is_derived_and_cannot_be_switched_or_exported(self):
        created = self.Lead.with_user(self.agent_a).create(
            {
                "name": "Agent-created campaign lead",
                "cc_customer_profile_id": self.profile_a.id,
                "cc_source_list_key": "manual-agent-a",
            }
        )
        self.assertEqual(created.campaign_id, self.campaign_a)
        self.assertEqual(created.call_center_campaign_id, self.campaign_a.legacy_campaign_id)
        self.assertEqual(created.cc_business_unit_id, self.campaign_a.cc_business_unit_id)
        self.assertEqual(created.user_id, self.agent_a)
        with self.assertRaises(AccessError):
            created.with_user(self.agent_a).write({"campaign_id": self.campaign_b.id})
        with self.assertRaises(AccessError):
            created.with_user(self.agent_a).write({"user_id": self.agent_b.id})
        with self.assertRaises(AccessError):
            created.with_user(self.agent_a).write({"codestra_workflow_id": False})
        with self.assertRaises(AccessError):
            created.with_user(self.agent_a).with_context(
                _cc_crm_transition_capability=True
            ).write({"codestra_workflow_id": False})
        with self.assertRaises(AccessError):
            created.with_user(self.agent_a).with_context(
                _cc_crm_scope_capability=True
            ).write({"campaign_id": self.campaign_b.id})
        with self.assertRaises(AccessError):
            created.with_user(self.agent_a).write(
                {"cc_source_list_key": "forged-list"}
            )
        with self.assertRaises(UserError):
            self.lead_a.with_user(self.agent_a).export_data(["name"])
        with self.assertRaises(AccessError):
            self.lead_b.with_user(self.agent_a).copy()
        with self.assertRaises(AccessError):
            self.Lead.with_user(self.agent_a).create(
                {
                    "name": "Forged cross-campaign lead",
                    "cc_customer_profile_id": self.profile_b.id,
                }
            )
        manual = self.Lead.with_user(self.agent_a).create(
            {
                "name": "Manual source defaults",
                "cc_customer_profile_id": self.profile_a.id,
            }
        )
        self.assertEqual(
            manual.cc_source_list_key, f"manual:odoo:user:{self.agent_a.id}"
        )
        with self.assertRaises(AccessError):
            self.lead_a.with_user(self.requester).unlink()

    def test_agent_creation_defaults_and_saved_lead_lock(self):
        created = self.Lead.with_user(self.agent_a).create(
            {"name": "Agent-created locked lead"}
        )
        self.assertEqual(created.user_id, self.agent_a)
        self.assertEqual(created.campaign_id, self.campaign_a)
        self.assertEqual(
            created.call_center_campaign_id, self.campaign_a.legacy_campaign_id
        )
        self.assertEqual(created.create_uid, self.agent_a)
        self.assertTrue(created.create_date)
        self.assertEqual(
            created.cc_source_list_key, f"manual:odoo:user:{self.agent_a.id}"
        )
        self.assertTrue(created.cc_contact_center_record)
        self.assertEqual(created.queue_state, "assigned")

        with self.assertRaises(AccessError):
            self.Lead.with_user(self.agent_a).create(
                {
                    "name": "Cross-campaign spoof",
                    "campaign_id": self.campaign_b.id,
                }
            )
        with self.assertRaises(AccessError):
            self.Lead.with_user(self.agent_a).create(
                {
                    "name": "Cross-agent spoof",
                    "user_id": self.agent_b.id,
                }
            )

        supervisor_created = self.Lead.with_user(self.supervisor_a).create(
            {
                "name": "Supervisor-created lead",
                "user_id": self.agent_a.id,
            }
        )
        self.assertEqual(supervisor_created.campaign_id, self.campaign_a)
        self.assertEqual(supervisor_created.user_id, self.agent_a)

        for actor in (
            self.agent_a,
            self.supervisor_a,
            self.service,
            self.technical_admin,
            self.requester,
        ):
            with self.subTest(actor=actor.login):
                with self.assertRaises(AccessError):
                    created.with_user(actor).write(
                        {"name": f"Forbidden edit by {actor.login}"}
                    )

        with self.assertRaises(AccessError):
            created.with_user(self.agent_a).write({"active": False})

        created.with_user(self.call_center_superuser).write(
            {
                "name": "Super User corrected lead",
                "active": False,
            }
        )
        self.assertEqual(created.name, "Super User corrected lead")
        self.assertFalse(created.active)

    def test_supervisor_creation_requires_an_agent_or_uses_the_unassigned_pool(self):
        SupervisorLead = self.Lead.with_user(self.supervisor_a)
        for assignment in ({}, {"user_id": False}):
            with self.subTest(assignment=assignment):
                lead = SupervisorLead.create(
                    {"name": "Supervisor-created pool lead", **assignment}
                )
                self.assertEqual(lead.campaign_id, self.campaign_a)
                self.assertEqual(lead.create_uid, self.supervisor_a)
                self.assertEqual(
                    lead.cc_source_list_key,
                    f"manual:odoo:user:{self.supervisor_a.id}",
                )
                self.assertFalse(lead.user_id)
                self.assertEqual(lead.queue_state, "available")
                self.assertFalse(
                    self.Lead.with_user(self.agent_a).search([("id", "=", lead.id)])
                )

        assigned = SupervisorLead.create(
            {"name": "Supervisor-selected agent", "user_id": self.agent_a2.id}
        )
        self.assertEqual(assigned.user_id, self.agent_a2)
        self.assertEqual(assigned.campaign_id, self.campaign_a)
        self.assertEqual(assigned.queue_state, "assigned")
        history = self.env["callcenter.lead.assignment"].sudo().search(
            [("lead_id", "=", assigned.id)]
        )
        self.assertEqual(len(history), 1)
        self.assertEqual(history.assignment_source, "manual_supervisor_creation")
        self.assertEqual(history.assigned_by_id, self.supervisor_a)
        self.assertEqual(history.agent_id, self.agent_a2)
        for invalid_owner in (self.supervisor_a, self.agent_b):
            with self.subTest(invalid_owner=invalid_owner.login):
                with self.assertRaises(ValidationError):
                    SupervisorLead.create(
                        {"name": "Invalid campaign agent", "user_id": invalid_owner.id}
                    )

    def test_private_queue_operations_preserve_saved_source_and_campaign_scope(self):
        lead = self.unassigned_a.with_user(self.agent_a2).sudo()
        source_fields = ["name", "campaign_id", "cc_source_list_key", "active"]
        source_before = lead.read(source_fields)
        with self.assertRaises(ValidationError):
            lead._callcenter_system_assign(self.agent_b)
        self.assertFalse(lead.user_id)

        lead._callcenter_system_assign(self.agent_a2)
        self.assertEqual(lead.user_id, self.agent_a2)
        self.assertEqual(lead.queue_state, "assigned")
        occurred_at = fields.Datetime.now()
        lead._callcenter_record_attempt(occurred_at=occurred_at)
        self.assertEqual(lead.call_attempt_count, 1)
        self.assertEqual(lead.last_call_at, occurred_at)
        lead._callcenter_apply_disposition("available", next_eligible_at=occurred_at)
        self.assertFalse(lead.user_id)
        self.assertEqual(lead.queue_state, "available")
        self.assertEqual(lead.next_eligible_at, occurred_at)
        self.assertEqual(lead.read(source_fields), source_before)
        history = self.env["callcenter.lead.assignment"].sudo().search(
            [("lead_id", "=", lead.id), ("assignment_source", "=", "queue")]
        )
        self.assertEqual(len(history), 1)
        self.assertEqual(history.agent_id, self.agent_a2)
        self.assertTrue(history.released_at)

    def test_saved_lead_queue_capability_is_private_elevated_and_field_bounded(self):
        lead = self.lead_a.with_user(self.agent_a)
        with self.assertRaises(AccessError):
            lead.with_context(
                _cc_lead_queue_capability=CRM_QUEUE_SYSTEM_CAPABILITY
            ).write({"queue_state": "callback"})
        for forged in (None, True, "CRM_QUEUE_SYSTEM_CAPABILITY", object()):
            for elevated in (False, True):
                with self.subTest(forged=forged, elevated=elevated):
                    with self.assertRaises(AccessError):
                        lead.sudo(elevated).with_context(
                            _cc_lead_queue_capability=forged
                        ).write({"queue_state": "callback"})
        for capability in (
            {"_cc_lead_queue_capability": CRM_QUEUE_SYSTEM_CAPABILITY},
            {"_cc_crm_scope_capability": CRM_SCOPE_MIGRATION_CAPABILITY},
            {"_cc_crm_transition_capability": CRM_TRANSITION_CAPABILITY},
        ):
            with self.subTest(capability=capability):
                with self.assertRaises(AccessError):
                    lead.sudo().with_context(**capability).write(
                        {"name": "Forbidden source edit", "queue_state": "callback"}
                    )
        self.assertEqual(lead.name, "Campaign A CRM lead")
        self.assertEqual(lead.queue_state, "assigned")

    def test_superuser_cannot_clear_saved_lead_campaign(self):
        with self.assertRaises(ValidationError):
            self.lead_a.with_user(self.call_center_superuser).write(
                {"campaign_id": False}
            )
        self.assertEqual(self.lead_a.campaign_id, self.campaign_a)

    def test_saved_lead_governance_cannot_be_removed_even_by_superuser(self):
        for changes in (
            {"cc_contact_center_record": False},
            {"cc_contact_center_record": False, "cc_source_list_key": False},
            {"cc_contact_center_record": False, "campaign_id": self.campaign_a.id},
            {"cc_contact_center_record": False, "campaign_id": False},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(AccessError), self.env.cr.savepoint():
                    self.lead_a.with_user(self.call_center_superuser).write(changes)
                self.assertTrue(self.lead_a.cc_contact_center_record)
                self.assertEqual(self.lead_a.campaign_id, self.campaign_a)
                self.assertEqual(self.lead_a.cc_source_list_key, "synthetic-list-a")
        with self.assertRaises(AccessError):
            self.lead_a.with_user(self.agent_a).write({"name": "Unprotected edit"})
        self.lead_a.with_user(self.call_center_superuser).write(
            {"cc_contact_center_record": True}
        )

    def test_superuser_source_correction_requires_nonempty_provenance(self):
        lead = self.lead_a.with_user(self.call_center_superuser)
        for source in (False, "", " \t\n"):
            with self.subTest(source=source):
                with self.assertRaises(ValidationError), self.env.cr.savepoint():
                    lead.write({"cc_source_list_key": source})
                self.assertEqual(lead.cc_source_list_key, "synthetic-list-a")
        lead.write({"cc_source_list_key": "corrected-source-list"})
        self.assertEqual(lead.cc_source_list_key, "corrected-source-list")

    def test_queue_release_preflights_all_record_access_before_elevation(self):
        company, campaign = self._other_company_campaign()
        foreign_lead = self.Lead.sudo().with_company(company).create(
            {
                "name": "Foreign company callback lead",
                "company_id": company.id,
                "campaign_id": campaign.id,
                "cc_source_list_key": "foreign-company-list",
                "user_id": False,
                "queue_state": "callback",
            }
        )
        foreign_lead = self.Lead.browse(foreign_lead.id)
        actor = self.call_center_superuser
        self.assertNotIn(company, actor.company_ids)
        with self.assertRaises(AccessError):
            foreign_lead.with_user(actor).read(["name"])
        history = self.env["callcenter.lead.assignment"].sudo().search(
            [("lead_id", "=", self.lead_a.id), ("released_at", "=", False)]
        )
        self.assertEqual(len(history), 1)
        for targets in (foreign_lead, self.lead_a | foreign_lead):
            with self.subTest(targets=targets.ids):
                with self.assertRaises(AccessError):
                    targets.with_user(actor).action_callcenter_release_to_queue()
                self.assertEqual(foreign_lead.queue_state, "callback")
                self.assertEqual(self.lead_a.queue_state, "assigned")
                self.assertEqual(self.lead_a.user_id, self.agent_a)
                self.assertFalse(history.released_at)
        self.lead_a.with_user(actor).action_callcenter_release_to_queue()
        self.assertEqual(self.lead_a.queue_state, "available")
        self.assertFalse(self.lead_a.user_id)
        self.assertTrue(history.released_at)

    def test_queue_release_rejects_ordinary_crm_records_before_any_mutation(self):
        ordinary = self.Lead.sudo().create(
            {"name": "Ordinary CRM record", "user_id": False, "queue_state": "callback"}
        )
        self.assertFalse(ordinary.cc_contact_center_record)
        with self.assertRaises(ValidationError):
            (self.lead_a | ordinary).with_user(
                self.call_center_superuser
            ).action_callcenter_release_to_queue()
        self.assertEqual(ordinary.queue_state, "callback")
        self.assertEqual(self.lead_a.queue_state, "assigned")
        self.assertEqual(self.lead_a.user_id, self.agent_a)

    def test_creation_cannot_spoof_import_batch_provenance(self):
        batch = self.env["callcenter.lead.import.batch"].with_user(
            self.call_center_superuser
        ).create(
            {
                "name": "Protected import provenance",
                "campaign_id": self.campaign_a.id,
                "filename": "provenance.csv",
                "upload_file": base64.b64encode(b"name,phone\nSynthetic,+15550001234\n"),
            }
        )
        for actor in (self.agent_a, self.supervisor_a, self.requester, self.service):
            with self.subTest(actor=actor.login):
                values = {
                    "name": "Spoofed imported lead",
                    "campaign_id": self.campaign_a.id,
                    "cc_source_list_key": "forged-import-source",
                }
                with self.assertRaises(AccessError):
                    self.Lead.with_user(actor).create({**values, "source_batch_id": batch.id})
                with self.assertRaises(AccessError):
                    self.Lead.with_user(actor).with_context(
                        default_source_batch_id=batch.id
                    ).create(values)

    def test_cross_campaign_duplicate_indicator_is_confined_to_source_company(self):
        company, campaign = self._other_company_campaign()
        contacts = (
            ("phone", "+15550008101", "+15550008102"),
            ("email_from", "foreign-contact@example.invalid", "same-company@example.invalid"),
        )
        for field_name, foreign_value, _same_company_value in contacts:
            self.Lead.sudo().with_company(company).create(
                {
                    "name": "Foreign-company contact",
                    "campaign_id": campaign.id,
                    "company_id": company.id,
                    "cc_source_list_key": "foreign-duplicate-indicator",
                    "user_id": False,
                    field_name: foreign_value,
                }
            )
        batch = self.env["callcenter.lead.import.batch"].with_user(
            self.call_center_superuser
        ).create(
            {
                "name": "Company-scoped duplicate check",
                "campaign_id": self.campaign_a.id,
                "filename": "company-duplicates.csv",
                "upload_file": base64.b64encode(
                    b"name,phone,email\nPhone contact,+15550008101,\n"
                    b"Email contact,,foreign-contact@example.invalid\n"
                ),
            }
        )
        batch.action_validate()
        self.assertEqual(len(batch.line_ids), 2)
        self.assertTrue(all(line.status == "valid" for line in batch.line_ids))
        self.assertFalse(any(batch.line_ids.mapped("cross_campaign_duplicate")))

        self.assertEqual(
            self.campaign_a.cc_business_unit_id.company_id,
            self.campaign_b.cc_business_unit_id.company_id,
        )
        for field_name, foreign_value, same_company_value in contacts:
            with self.subTest(field=field_name):
                local = self.Lead.with_user(self.agent_a).create(
                    {"name": "No foreign-company signal", field_name: foreign_value}
                )
                self.assertFalse(local.cross_campaign_duplicate)
                self.Lead.with_user(self.requester).create(
                    {
                        "name": "Same-company matching contact",
                        "campaign_id": self.campaign_b.id,
                        "cc_source_list_key": "same-company-indicator",
                        "user_id": False,
                        field_name: same_company_value,
                    }
                )
                matching = self.Lead.with_user(self.agent_a).create(
                    {"name": "Same-company signal retained", field_name: same_company_value}
                )
                self.assertTrue(matching.cross_campaign_duplicate)

        foreign_batch = self.env["callcenter.lead.import.batch"].with_user(
            self.call_center_superuser
        ).sudo().create(
            {
                "name": "Inaccessible source campaign",
                "campaign_id": campaign.id,
                "filename": "foreign-company.csv",
                "upload_file": base64.b64encode(
                    b"name,phone\nForeign existing contact,+15550008101\n"
                ),
            }
        )
        scoped_foreign_batch = foreign_batch.with_user(self.call_center_superuser)
        with self.assertRaises(AccessError):
            scoped_foreign_batch.action_validate()
        self.assertEqual(foreign_batch.state, "draft")
        self.assertFalse(foreign_batch.line_ids)
        with self.assertRaises(AccessError):
            scoped_foreign_batch.action_import()

    def test_import_and_assignment_audit_reads_follow_active_company_scope(self):
        company, campaign = self._other_company_campaign()
        Batch = self.env["callcenter.lead.import.batch"].with_user(self.call_center_superuser)
        payload = base64.b64encode(b"name,phone\nPrivate customer,+15550008201\n")
        batch_values = {"name": "Private import", "filename": "private.csv", "upload_file": payload}
        local_batch = Batch.create({**batch_values, "campaign_id": self.campaign_a.id})
        foreign_batch = Batch.sudo().create({**batch_values, "campaign_id": campaign.id})
        Line = self.env["callcenter.lead.import.line"].sudo()
        line_values = {
            "row_number": 2,
            "raw_data": {"name": "Private customer", "phone": "+15550008201"},
            "normalized_phone": "15550008201",
        }
        local_line = Line.create({**line_values, "batch_id": local_batch.id})
        foreign_line = Line.create({**line_values, "batch_id": foreign_batch.id})
        foreign_lead = self.Lead.sudo().with_company(company).create(
            {
                "name": "Foreign assignment audit fixture",
                "campaign_id": campaign.id,
                "company_id": company.id,
                "cc_source_list_key": "foreign-assignment-audit",
                "user_id": False,
            }
        )
        Assignment = self.env["callcenter.lead.assignment"].sudo()
        local_assignment = Assignment.search([("lead_id", "=", self.lead_a.id)], limit=1)
        self.assertTrue(local_assignment)
        foreign_assignment = Assignment.create(
            {
                "lead_id": foreign_lead.id,
                "campaign_id": campaign.id,
                "agent_id": self.agent_a.id,
                "assigned_by_id": self.call_center_superuser.id,
                "assignment_source": "admin",
                "reason": "Private foreign assignment history",
            }
        )
        cases = (
            (local_batch, foreign_batch, ["name", "upload_file"], "campaign_id", "name"),
            (local_line, foreign_line, ["raw_data", "normalized_phone"], "batch_id", "normalized_phone"),
            (local_assignment, foreign_assignment, ["reason", "agent_id"], "campaign_id", "reason"),
        )
        for actor in (self.call_center_superuser, self.requester):
            self.assertNotIn(company, actor.company_ids)
            for local, foreign, private_fields, group_field, export_field in cases:
                with self.subTest(actor=actor.login, model=local._name):
                    Model = self.env[local._name].with_user(actor)
                    self.assertFalse(Model.env.su)
                    local_scoped = Model.browse(local.id)
                    foreign_scoped = Model.browse(foreign.id)
                    self.assertEqual(local_scoped.read(private_fields)[0]["id"], local.id)
                    self.assertEqual(len(local_scoped.export_data([export_field])["datas"]), 1)
                    self.assertFalse(Model.search([("id", "=", foreign.id)]))
                    rows = Model.search_read([("id", "in", [local.id, foreign.id])], private_fields)
                    self.assertEqual([row["id"] for row in rows], [local.id])
                    self.assertFalse(Model.read_group(
                        [("id", "=", foreign.id)], ["record_count:count(id)"], [group_field]
                    ))
                    grouped = Model.read_group(
                        [("id", "in", [local.id, foreign.id])],
                        ["record_count:count(id)"], [group_field],
                    )
                    self.assertEqual(sum(row["record_count"] for row in grouped), 1)
                    with self.assertRaises(AccessError):
                        foreign_scoped.read(private_fields)
                    with self.assertRaises(AccessError):
                        foreign_scoped.export_data([export_field])
                    with self.assertRaises(AccessError):
                        (local_scoped | foreign_scoped).export_data([export_field])

        # Membership in two companies does not expose the inactive company.
        self.call_center_superuser.sudo().write({"company_ids": [(4, company.id)]})
        for _local, foreign, private_fields, _group_field, _export_field in cases:
            Model = self.env[foreign._name].with_user(self.call_center_superuser)
            self.assertFalse(Model.with_context(
                allowed_company_ids=[self.env.company.id]
            ).search([("id", "=", foreign.id)]))
            visible = Model.with_context(
                allowed_company_ids=[self.env.company.id, company.id]
            ).browse(foreign.id)
            self.assertEqual(visible.read(private_fields)[0]["id"], foreign.id)

        # The writable batch model cannot create or move data outside scope.
        restricted = Batch.with_context(allowed_company_ids=[self.env.company.id])
        restricted.browse(local_batch.id).write({"name": "Updated local import"})
        self.assertEqual(local_batch.name, "Updated local import")
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            restricted.create({**batch_values, "campaign_id": campaign.id})
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            restricted.browse(foreign_batch.id).write({"name": "Forbidden rewrite"})
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            restricted.browse(local_batch.id).write({"campaign_id": campaign.id})
        self.assertEqual(local_batch.campaign_id, self.campaign_a)
        self.assertEqual(foreign_batch.name, "Private import")

        # Audit rows retain their read-only ACL and append-only behavior.
        for actor in (self.call_center_superuser, self.requester):
            with self.subTest(actor=actor.login):
                with self.assertRaises(AccessError):
                    local_line.with_user(actor).write({"normalized_phone": "forged"})
                with self.assertRaises(AccessError):
                    Line.with_user(actor).create({**line_values, "batch_id": local_batch.id})
                with self.assertRaises(AccessError):
                    local_assignment.with_user(actor).write({"reason": "forged"})
                with self.assertRaises(AccessError):
                    Assignment.with_user(actor).create(
                        {
                            "lead_id": self.lead_a.id,
                            "campaign_id": self.campaign_a.id,
                            "agent_id": self.agent_a.id,
                            "assigned_by_id": actor.id,
                            "assignment_source": "admin",
                        }
                    )

    def test_explicit_import_provenance_requires_readable_matching_batch(self):
        company, campaign = self._other_company_campaign()
        Batch = self.env["callcenter.lead.import.batch"].with_user(self.call_center_superuser)
        batch_values = {
            "name": "Provenance scope",
            "filename": "provenance-scope.csv",
            "upload_file": base64.b64encode(b"name,phone\nSynthetic,+15550008301\n"),
        }
        local_batch = Batch.create({**batch_values, "campaign_id": self.campaign_a.id})
        other_batch = Batch.create({**batch_values, "campaign_id": self.campaign_b.id})
        foreign_batch = Batch.sudo().create({**batch_values, "campaign_id": campaign.id})
        Lead = self.Lead.with_user(self.call_center_superuser)
        values = {
            "name": "Validated import provenance",
            "campaign_id": self.campaign_a.id,
            "cc_source_list_key": "import-provenance-scope",
            "user_id": False,
        }
        for batch, error in ((foreign_batch, AccessError), (other_batch, ValidationError)):
            with self.subTest(batch=batch.id):
                with self.assertRaises(error), self.env.cr.savepoint():
                    Lead.create({**values, "source_batch_id": batch.id})
                with self.assertRaises(error), self.env.cr.savepoint():
                    Lead.with_context(default_source_batch_id=batch.id).create(values)
                with self.assertRaises(error), self.env.cr.savepoint():
                    self.unassigned_a.with_user(self.call_center_superuser).write(
                        {"source_batch_id": batch.id}
                    )
        self.assertFalse(self.unassigned_a.source_batch_id)
        created = Lead.create({**values, "source_batch_id": local_batch.id})
        self.assertEqual(created.source_batch_id, local_batch)
        self.unassigned_a.with_user(self.call_center_superuser).write(
            {"source_batch_id": local_batch.id}
        )
        self.assertEqual(self.unassigned_a.source_batch_id, local_batch)
        created.action_callcenter_transfer_campaign(self.campaign_b.id)
        self.assertEqual(created.campaign_id, self.campaign_b)
        self.assertEqual(created.source_batch_id, local_batch)

    def test_crm_search_name_group_and_direct_id_do_not_leak(self):
        LeadA = self.Lead.with_user(self.agent_a)
        self.assertIn(self.lead_a, LeadA.search([]))
        self.assertNotIn(self.lead_b, LeadA.search([]))
        self.assertFalse(LeadA.search([("id", "=", self.lead_b.id)]))
        self.assertFalse(LeadA.name_search(self.lead_b.name, operator="=", limit=10))
        grouped = LeadA._read_group([], ["campaign_id"], ["__count"])
        self.assertEqual(sum(count for _campaign, count in grouped), 1)
        with self.assertRaises(AccessError):
            self.lead_b.with_user(self.agent_a).read(["name"])


    def test_agent_does_not_see_unassigned_but_supervisor_sees_campaign(self):
        agent_leads = self.Lead.with_user(self.agent_a).search([])
        self.assertIn(self.lead_a, agent_leads)
        self.assertNotIn(self.unassigned_a, agent_leads)
        self.assertFalse(
            self.Lead.with_user(self.agent_a).search(
                [("id", "=", self.unassigned_a.id)]
            )
        )
        supervisor_leads = self.Lead.with_user(self.supervisor_a).search(
            [("campaign_id", "=", self.campaign_a.id)]
        )
        self.assertIn(self.lead_a, supervisor_leads)
        self.assertIn(self.unassigned_a, supervisor_leads)
        self.assertNotIn(self.lead_b, supervisor_leads)

    def test_saved_lead_reassignment_is_superuser_only(self):
        with self.assertRaises(AccessError):
            self.lead_a.with_user(self.requester).action_callcenter_assign_agent(
                self.agent_a2.id
            )
        with self.assertRaises(AccessError):
            self.lead_a.with_user(self.supervisor_a).action_callcenter_assign_agent(
                self.agent_a2.id
            )
        self.lead_a.with_user(self.call_center_superuser).action_callcenter_assign_agent(
            self.agent_a2.id
        )
        self.assertEqual(self.lead_a.user_id, self.agent_a2)
        history = self.env["callcenter.lead.assignment"].sudo().search(
            [("lead_id", "=", self.lead_a.id)],
            order="assigned_at desc, id desc",
            limit=1,
        )
        self.assertEqual(history.agent_id, self.agent_a2)
        self.assertEqual(history.assignment_source, "transfer")
        with self.assertRaises(ValidationError):
            self.lead_a.with_user(self.call_center_superuser).action_callcenter_assign_agent(
                self.agent_b.id
            )
        with self.assertRaises(UserError):
            self.lead_a.with_user(self.supervisor_a).export_data(["name"])

    def test_archive_and_delete_policy_is_role_enforced(self):
        with self.assertRaises(AccessError):
            self.lead_a.with_user(self.agent_a).action_archive()
        with self.assertRaises(AccessError):
            self.lead_a.with_user(self.supervisor_a).action_archive()
        self.lead_a.with_user(self.call_center_superuser).action_archive()
        self.assertFalse(self.lead_a.active)
        self.lead_a.with_user(self.call_center_superuser).action_unarchive()
        self.assertTrue(self.lead_a.active)
        with self.assertRaises(AccessError):
            self.lead_a.with_user(self.requester).unlink()

    def test_superuser_cross_campaign_transfer_is_governed(self):
        target_profile = self.Profile.with_user(self.requester).create_from_partner(
            self.partner_a, self.campaign_b, "crm-profile-a-campaign-b"
        )
        with self.assertRaises(AccessError):
            self.lead_a.with_user(self.supervisor_a).action_callcenter_transfer_campaign(
                self.campaign_b.id,
                target_user_id=self.agent_b.id,
                target_profile_id=target_profile.id,
            )
        self.lead_a.with_user(self.call_center_superuser).action_callcenter_transfer_campaign(
            self.campaign_b.id,
            target_user_id=self.agent_b.id,
            target_profile_id=target_profile.id,
        )
        self.assertEqual(self.lead_a.campaign_id, self.campaign_b)
        self.assertEqual(self.lead_a.user_id, self.agent_b)
        self.assertEqual(self.lead_a.cc_customer_profile_id, target_profile)

    def test_call_center_superuser_can_read_delegated_campaign_catalog(self):
        user = self.call_center_superuser
        self.assertTrue(user.has_group("codestra_cc_security.group_cc_global_administrator"))
        self.assertFalse(user.has_group("base.group_system"))
        Campaign = self.Campaign.with_user(user)
        for campaign in (self.campaign_a, self.campaign_b):
            with self.subTest(campaign=campaign.id):
                self.assertIn(
                    campaign,
                    Campaign.search([("id", "=", campaign.id)]),
                )
                self.assertEqual(
                    campaign.with_user(user).legacy_campaign_id,
                    campaign.legacy_campaign_id,
                )
                self.assertEqual(
                    campaign.legacy_campaign_id.with_user(user).code,
                    campaign.code,
                )
                self.assertEqual(
                    campaign.cc_business_unit_id.with_user(user).code,
                    campaign.cc_business_unit_id.code,
                )

        # Catalog reads do not authorize legacy campaign configuration writes.
        for actor in (self.requester, user):
            with self.subTest(actor=actor.login):
                with self.assertRaises(AccessError):
                    self.campaign_a.legacy_campaign_id.with_user(actor).write(
                        {"name": "Forbidden legacy configuration change"}
                    )

    def test_superuser_catalog_reads_remain_company_scoped(self):
        other_company = self.env["res.company"].create(
            {"name": "CRM Catalog Other Company"}
        )
        unit = self.env["cc.business.unit"].create(
            {
                "name": "CRM Catalog Other Company Unit",
                "code": "CRM-OTHER-COMPANY",
                "company_id": other_company.id,
            }
        )
        campaign = self.Campaign.create(
            {
                "name": "CRM Catalog Other Company Campaign",
                "code": "CRM-OTHER-COMPANY-OUT",
                "cc_business_unit_id": unit.id,
                "design_automation_enabled": False,
            }
        )
        user = self.call_center_superuser
        self.assertNotIn(other_company, user.company_ids)
        for record in (unit, unit.legacy_business_unit_id, campaign, campaign.legacy_campaign_id):
            with self.subTest(model=record._name):
                scoped = record.with_user(user).with_context(active_test=False)
                self.assertFalse(scoped.search([("id", "=", record.id)]))
                with self.assertRaises(AccessError):
                    scoped.read(["name"])

        with self.assertRaises(AccessError):
            self.unassigned_a.with_user(user).action_callcenter_transfer_campaign(
                campaign.id
            )
        self.assertEqual(self.unassigned_a.campaign_id, self.campaign_a)

    def test_manual_agent_creation_is_assigned_and_locked_after_save(self):
        created = self.Lead.with_user(self.agent_a2).create(
            {
                "name": "Manual agent-created locked lead",
                "phone": "+1 809 555 8899",
            }
        )
        self.assertEqual(created.user_id, self.agent_a2)
        self.assertEqual(created.campaign_id, self.campaign_a)
        self.assertEqual(created.queue_state, "assigned")
        self.assertEqual(
            created.cc_source_list_key, f"manual:odoo:user:{self.agent_a2.id}"
        )
        self.assertEqual(created.create_uid, self.agent_a2)

        for actor in (self.agent_a2, self.supervisor_a, self.service, self.requester, self.technical_admin):
            with self.subTest(actor=actor.login):
                with self.assertRaises(AccessError):
                    created.with_user(actor).write(
                        {"name": f"Forbidden edit by {actor.login}"}
                    )

        created.with_user(self.call_center_superuser).write(
            {"name": "Super User corrected lead"}
        )
        self.assertEqual(created.name, "Super User corrected lead")

    def test_get_next_lead_uses_priority_locking_and_one_working_lead(self):
        # Prepare staging state as the fixture owner: active is delegated to
        # the legacy campaign, which the requester deliberately cannot write.
        self.campaign_a.sudo().write(
            {"lifecycle_state": "staging_ready", "active": True}
        )
        high = self.Lead.with_user(self.requester).create(
            {
                "name": "High priority queue lead",
                "campaign_id": self.campaign_a.id,
                "cc_source_list_key": "queue-test-high",
                "queue_state": "available",
                "user_id": False,
                "priority": "3",
            }
        )
        low = self.Lead.with_user(self.requester).create(
            {
                "name": "Lower priority queue lead",
                "campaign_id": self.campaign_a.id,
                "cc_source_list_key": "queue-test-low",
                "queue_state": "available",
                "user_id": False,
                "priority": "1",
            }
        )

        action = self.Lead.with_user(self.agent_a2).action_get_next_lead()
        self.assertEqual(action["res_id"], high.id)
        self.assertEqual(high.user_id, self.agent_a2)
        self.assertEqual(high.queue_state, "assigned")
        with self.assertRaises(UserError):
            self.Lead.with_user(self.agent_a2).action_get_next_lead()

        high.sudo()._callcenter_apply_disposition(
            "callback", reason="Synthetic callback disposition"
        )
        action = self.Lead.with_user(self.agent_a2).action_get_next_lead()
        self.assertEqual(action["res_id"], low.id)
        history = self.env["callcenter.lead.assignment"].sudo().search(
            [
                ("lead_id", "=", high.id),
                ("assignment_source", "=", "queue"),
            ],
            limit=1,
        )
        self.assertTrue(history)
        self.assertTrue(history.released_at)

    def test_csv_import_deduplicates_audits_and_requires_superuser(self):
        self.Lead.with_user(self.requester).create(
            {
                "name": "Cross-campaign existing customer",
                "phone": "+1 (809) 555-0100",
                "campaign_id": self.campaign_b.id,
                "cc_source_list_key": "cross-campaign-fixture",
                "queue_state": "available",
                "user_id": False,
            }
        )
        payload = base64.b64encode(
            (
                "Name,Phone,External Lead ID,Email,Company,Priority,Source,Notes\n"
                "Cross Campaign,+1 (809) 555-0100,EXT-100,cross@example.invalid,ACME,High,Vendor A,first\n"
                "Duplicate Row,+1 809 555 0100,EXT-101,duplicate@example.invalid,ACME,Low,Vendor A,second\n"
                ",+1 809 555 0200,EXT-102,error@example.invalid,ACME,Low,Vendor A,missing name\n"
            ).encode()
        )
        Batch = self.env["callcenter.lead.import.batch"]
        with self.assertRaises(AccessError):
            Batch.with_user(self.agent_a2).create(
                {
                    "name": "Forbidden import",
                    "campaign_id": self.campaign_a.id,
                    "filename": "leads.csv",
                    "upload_file": payload,
                }
            )

        batch = Batch.with_user(self.call_center_superuser).create(
            {
                "name": "Synthetic CSV import",
                "campaign_id": self.campaign_a.id,
                "filename": "leads.csv",
                "upload_file": payload,
            }
        )
        batch.with_user(self.call_center_superuser).action_validate()
        self.assertEqual(batch.state, "validated")
        self.assertEqual(batch.total_rows, 3)
        self.assertEqual(batch.duplicate_count, 1)
        self.assertEqual(batch.error_count, 1)
        valid_line = batch.line_ids.filtered(lambda line: line.status == "valid")
        self.assertEqual(len(valid_line), 1)
        self.assertTrue(valid_line.cross_campaign_duplicate)

        batch.with_user(self.call_center_superuser).action_import()
        self.assertEqual(batch.state, "imported")
        self.assertEqual(batch.created_count, 1)
        imported = batch.line_ids.filtered(lambda line: line.status == "imported").lead_id
        self.assertEqual(imported.campaign_id, self.campaign_a)
        self.assertFalse(imported.user_id)
        self.assertEqual(imported.queue_state, "available")
        self.assertTrue(imported.cross_campaign_duplicate)

        duplicate_file = Batch.with_user(self.call_center_superuser).create(
            {
                "name": "Repeated file",
                "campaign_id": self.campaign_a.id,
                "filename": "leads.csv",
                "upload_file": payload,
            }
        )
        with self.assertRaises(UserError):
            duplicate_file.with_user(self.call_center_superuser).action_validate()
        duplicate_file.with_user(self.call_center_superuser).write(
            {"override_duplicate_file": True}
        )
        duplicate_file.with_user(self.call_center_superuser).action_validate()
        self.assertEqual(duplicate_file.state, "validated")

    def test_profile_chatter_activity_and_attachment_inherit_campaign_scope(self):
        message = self.profile_a.message_post(body="Synthetic campaign A update")
        activity = self.env["mail.activity"].create(
            {
                "res_model_id": self.env["ir.model"]._get_id(self.profile_a._name),
                "res_id": self.profile_a.id,
                "activity_type_id": self.env.ref("mail.mail_activity_data_todo").id,
                "summary": "Synthetic campaign A follow-up",
                "user_id": self.agent_a.id,
                "date_deadline": fields.Date.today(),
            }
        )
        attachment = self.env["ir.attachment"].with_user(self.requester).create(
            {
                "name": "safe-profile-a.txt",
                "datas": base64.b64encode(b"safe profile fixture"),
                "mimetype": "text/plain",
                "res_model": self.profile_a._name,
                "res_id": self.profile_a.id,
                "cc_scan_state": "clean",
                "cc_scan_evidence_hash": "b" * 64,
                "cc_content_hash": "c" * 64,
            }
        )
        self.assertEqual(message.cc_campaign_id, self.campaign_a)
        self.assertEqual(activity.cc_campaign_id, self.campaign_a)
        self.assertEqual(attachment.cc_campaign_id, self.campaign_a)
        self.assertFalse(
            self.env["mail.message"].with_user(self.agent_b).search(
                [("id", "=", message.id)]
            )
        )
        self.assertFalse(
            self.env["mail.activity"].with_user(self.agent_b).search(
                [("id", "=", activity.id)]
            )
        )
        self.assertFalse(
            self.env["ir.attachment"].with_user(self.agent_b).search(
                [("id", "=", attachment.id)]
            )
        )
