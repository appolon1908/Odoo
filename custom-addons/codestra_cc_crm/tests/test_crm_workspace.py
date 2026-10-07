import base64

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


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
            "CRM Call Center Super User",
            "cc-crm-superuser@example.invalid",
            ["codestra_cc_security.group_cc_call_center_superuser"],
        )
        cls.technical_admin = cls._create_user(
            "CRM Technical Administrator",
            "cc-crm-technical-admin@example.invalid",
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
        cls._activate_membership(cls.agent_b, cls.campaign_b, "CRM-MEMBER-B")
        cls._activate_membership(
            cls.supervisor_a, cls.campaign_a, "CRM-SUPERVISOR-A", role="supervisor"
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

    @classmethod
    def _create_user(cls, name, login, group_xmlids):
        groups = cls.env["res.groups"].browse(
            [cls.env.ref(xmlid).id for xmlid in group_xmlids]
        )
        return cls.env["res.users"].create(
            {"name": name, "login": login, "group_ids": [(6, 0, groups.ids)]}
        )

    @classmethod
    def _activate_membership(cls, user, campaign, ticket, role="agent"):
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
                "is_primary_supervisor": role == "supervisor",
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
        with self.assertRaises(ValidationError):
            self.Lead.with_user(self.agent_a).create(
                {
                    "name": "Missing source list",
                    "cc_customer_profile_id": self.profile_a.id,
                }
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
