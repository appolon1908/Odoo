from datetime import timedelta

from odoo import fields
from odoo.tools import SQL
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestCampaignSecurity(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Campaign = cls.env["cc.campaign"].with_context(active_test=False)
        cls.Membership = cls.env["cc.campaign.membership"]
        cls.identity_managed = "cc.identity.outbox" in cls.env
        cls.Campaign.env["cc.business.unit"]._adopt_legacy_records()
        cls.campaign_a = cls.Campaign.search(
            [("code", "=", "COD-WEB-OUT")], limit=1
        )
        cls.campaign_b = cls.Campaign.search(
            [("id", "!=", cls.campaign_a.id)], limit=1
        )
        cls.requester = cls._create_user(
            "Access Requester",
            "access-requester@example.invalid",
            "codestra_cc_security.group_cc_global_administrator",
        )
        cls.approver = cls._create_user(
            "Access Approver",
            "access-approver@example.invalid",
            "codestra_cc_security.group_cc_global_administrator",
        )
        cls.agent = cls._create_user(
            "Scoped Agent",
            "authority-agent@example.invalid",
            "codestra_cc_security.group_cc_campaign_agent",
        )
        cls.other_agent = cls._create_user(
            "Other Agent",
            "authority-other-agent@example.invalid",
            "codestra_cc_security.group_cc_campaign_agent",
        )
        cls.supervisor = cls._create_user(
            "Scoped Supervisor",
            "authority-supervisor@example.invalid",
            "codestra_cc_security.group_cc_campaign_supervisor",
        )
        cls.qa_analyst = cls._create_user(
            "Scoped QA Analyst",
            "authority-qa@example.invalid",
            "codestra_cc_security.group_cc_quality_analyst",
        )
        cls.technical = cls._create_user(
            "Technical Administrator",
            "authority-technical@example.invalid",
            "codestra_cc_security.group_cc_technical_administrator",
        )
        cls.identity_service = False
        if cls.identity_managed:
            cls.identity_service = cls.env["res.users"].create(
                {
                    "name": "Campaign Security Identity Service",
                    "login": "campaign-security-identity-service@example.invalid",
                    "group_ids": [
                        (
                            6,
                            0,
                            [
                                cls.env.ref("base.group_user").id,
                                cls.env.ref(
                                    "codestra_identity_provisioning.group_provisioning_service"
                                ).id,
                            ],
                        )
                    ],
                }
            )
        cls.agent_employee = cls._create_employee(cls.agent)
        cls.other_employee = cls._create_employee(cls.other_agent)
        cls.supervisor_employee = cls._create_employee(cls.supervisor)
        cls.qa_employee = cls._create_employee(cls.qa_analyst)
        cls.agent_membership = cls._activate_membership(
            cls.agent, cls.agent_employee, cls.campaign_a, "agent"
        )
        cls.other_membership = cls._activate_membership(
            cls.other_agent, cls.other_employee, cls.campaign_b, "agent"
        )
        cls.supervisor_membership = cls._activate_membership(
            cls.supervisor,
            cls.supervisor_employee,
            cls.campaign_a,
            "supervisor",
            primary=True,
        )

    @classmethod
    def _create_user(cls, name, login, group_xmlid):
        return cls.env["res.users"].create(
            {
                "name": name,
                "login": login,
                "group_ids": [(6, 0, cls.env.ref(group_xmlid).ids)],
            }
        )

    @classmethod
    def _create_employee(cls, user):
        return cls.env["hr.employee"].create(
            {"name": user.name, "user_id": user.id, "company_id": cls.env.company.id}
        )

    @classmethod
    def _activate_membership(cls, user, employee, campaign, role, primary=False):
        membership = cls.Membership.with_user(cls.requester).create(
            cls._identity_safe_values(
                {
                "user_id": user.id,
                "employee_id": employee.id,
                "campaign_id": campaign.id,
                "role": role,
                "state": "pending_sync",
                "is_primary_supervisor": primary,
                "requested_by_id": cls.requester.id,
                "source_ticket": f"SEC-{user.id}",
                "starts_at": fields.Datetime.now(),
                "last_sync_status": "matched",
                "read_back_evidence": "synthetic staging read-back matched",
                }
            )
        )
        cls._synchronize_identity(membership)
        membership.with_user(cls.approver).action_activate()
        return membership

    @classmethod
    def _identity_safe_values(cls, values):
        values = dict(values)
        if cls.identity_managed:
            values["state"] = "draft"
            values.pop("last_sync_status", None)
            values.pop("read_back_evidence", None)
        return values

    @classmethod
    def _synchronize_identity(cls, membership):
        if not cls.identity_managed:
            return False
        membership.with_user(cls.requester).action_submit_identity()
        operation = membership.with_user(cls.approver).action_approve_identity()
        operation.with_user(cls.identity_service).action_record_readback(
            {
                target: {"status": "matched", "evidence_hash": "a" * 64}
                for target in operation.required_targets
            },
            "staging://security-suite/readback",
        )
        return operation

    def test_stable_role_catalog_is_available(self):
        roles = {
            "group_cc_campaign_agent",
            "group_cc_senior_agent",
            "group_cc_campaign_supervisor",
            "group_cc_quality_analyst",
            "group_cc_workforce_analyst",
            "group_cc_compliance_officer",
            "group_cc_campaign_configuration_manager",
            "group_cc_global_administrator",
            "group_cc_technical_administrator",
            "group_cc_auditor",
        }
        for role in roles:
            self.assertTrue(self.env.ref(f"codestra_cc_security.{role}").exists())
        self.assertFalse(
            self.agent.has_group("call_center_core.group_call_center_user")
        )
        self.assertFalse(
            self.requester.has_group("call_center_core.group_call_center_admin")
        )
        self.assertEqual(
            self.env.ref("callcenter.group_callcenter_agent"),
            self.env.ref("codestra_cc_security.group_cc_campaign_agent"),
        )
        self.assertEqual(
            self.env.ref("callcenter.group_callcenter_supervisor"),
            self.env.ref("codestra_cc_security.group_cc_campaign_supervisor"),
        )
        self.assertEqual(
            self.env.ref("callcenter.group_callcenter_superuser"),
            self.env.ref("codestra_cc_security.group_cc_global_administrator"),
        )
        self.assertFalse(
            self.requester.has_group("base.group_system"),
            "Operational Call Center Super User must not imply Odoo system administration.",
        )

    def test_partial_unique_indexes_are_installed(self):
        rows = self.env.execute_query(
            SQL(
                """
            SELECT indexname, indexdef
              FROM pg_indexes
             WHERE schemaname = current_schema()
               AND tablename = 'cc_campaign_membership'
               AND indexname LIKE 'cc_campaign_membership_%%active%%'
            ORDER BY indexname
                """
            )
        )
        definitions = "\n".join(indexdef for _name, indexdef in rows)
        self.assertIn("= ANY", definitions)
        self.assertIn("is_primary_supervisor", definitions)
        self.assertIn("state", definitions)
        self.assertGreaterEqual(len(rows), 4)

    def test_agent_search_and_direct_id_are_campaign_scoped(self):
        visible = self.Campaign.with_user(self.agent).search([])
        self.assertEqual(visible, self.campaign_a)
        self.assertFalse(
            self.Campaign.with_user(self.agent).search(
                [("id", "=", self.campaign_b.id)]
            )
        )
        with self.assertRaises(AccessError):
            self.Campaign.with_user(self.agent).browse(self.campaign_b.id).read(["name"])

    def test_agent_query_surfaces_do_not_reveal_other_campaign(self):
        Campaign = self.Campaign.with_user(self.agent)
        self.assertFalse(
            Campaign.name_search(self.campaign_b.name, operator="=", limit=10)
        )
        grouped = Campaign.read_group(
            [], ["record_count:count(id)"], ["environment"]
        )
        self.assertEqual(sum(item["record_count"] for item in grouped), 1)
        with self.assertRaises(UserError):
            Campaign.search([]).export_data(["code"])

    def test_agent_cannot_mutate_campaign_or_copy_cross_scope(self):
        with self.assertRaises(AccessError):
            self.campaign_a.with_user(self.agent).write({"name": "Forbidden"})
        with self.assertRaises(AccessError):
            self.campaign_a.with_user(self.agent).copy()
        with self.assertRaises(AccessError):
            self.Campaign.with_user(self.agent).create(
                {
                    "name": "Forbidden",
                    "code": "FORBIDDEN-CAMPAIGN",
                    "cc_business_unit_id": self.campaign_a.cc_business_unit_id.id,
                }
            )

    def test_membership_self_and_supervisor_visibility(self):
        agent_visible = self.Membership.with_user(self.agent).search([])
        self.assertEqual(agent_visible, self.agent_membership)
        supervisor_visible = self.Membership.with_user(self.supervisor).search([])
        self.assertEqual(
            set(supervisor_visible.ids),
            {self.agent_membership.id, self.supervisor_membership.id},
        )
        self.assertNotIn(self.other_membership, supervisor_visible)


    def test_backup_supervisor_has_supervised_campaign_visibility(self):
        backup = self._create_user(
            "Backup Supervisor",
            "backup-supervisor@example.invalid",
            "codestra_cc_security.group_cc_campaign_supervisor",
        )
        backup_employee = self._create_employee(backup)
        backup_membership = self._activate_membership(
            backup,
            backup_employee,
            self.campaign_a,
            "supervisor",
            primary=False,
        )
        self.assertFalse(backup_membership.is_primary_supervisor)
        self.assertEqual(backup.cc_supervised_campaign_ids, self.campaign_a)
        self.assertEqual(
            self.campaign_a.primary_supervisor_membership_id,
            self.supervisor_membership,
        )
        visible = self.Membership.with_user(backup).search([])
        self.assertIn(self.agent_membership, visible)
        self.assertIn(self.supervisor_membership, visible)
        self.assertIn(backup_membership, visible)
        self.assertNotIn(self.other_membership, visible)

    def test_exact_one_operational_membership_is_enforced(self):
        second = self.Membership.with_user(self.requester).create(
            self._identity_safe_values(
                {
                "user_id": self.agent.id,
                "employee_id": self.agent_employee.id,
                "campaign_id": self.campaign_b.id,
                "role": "supervisor",
                "state": "pending_sync",
                "is_primary_supervisor": True,
                "requested_by_id": self.requester.id,
                "source_ticket": "SEC-SECOND",
                "starts_at": fields.Datetime.now(),
                "last_sync_status": "matched",
                "read_back_evidence": "synthetic staging read-back matched",
                }
            )
        )
        self._synchronize_identity(second)
        with self.assertRaises(ValidationError):
            second.with_user(self.approver).action_activate()

    def test_supervisor_is_exactly_one_primary_campaign(self):
        self.assertEqual(
            self.campaign_a.primary_supervisor_membership_id,
            self.supervisor_membership,
        )
        other_supervisor = self._create_user(
            "Duplicate Supervisor",
            "duplicate-supervisor@example.invalid",
            "codestra_cc_security.group_cc_campaign_supervisor",
        )
        other_employee = self._create_employee(other_supervisor)
        duplicate = self.Membership.with_user(self.requester).create(
            self._identity_safe_values(
                {
                "user_id": other_supervisor.id,
                "employee_id": other_employee.id,
                "campaign_id": self.campaign_a.id,
                "role": "supervisor",
                "state": "pending_sync",
                "is_primary_supervisor": True,
                "requested_by_id": self.requester.id,
                "source_ticket": "SEC-DUPLICATE-SUPERVISOR",
                "starts_at": fields.Datetime.now(),
                "last_sync_status": "matched",
                "read_back_evidence": "synthetic staging read-back matched",
                }
            )
        )
        self._synchronize_identity(duplicate)
        with self.assertRaises(ValidationError):
            duplicate.with_user(self.approver).action_activate()

    def test_technical_admin_is_denied_without_break_glass(self):
        self.assertFalse(self.Campaign.with_user(self.technical).search([]))
        with self.assertRaises(AccessError):
            self.Campaign.with_user(self.technical).browse(self.campaign_a.id).read(["name"])

    def test_break_glass_is_separately_approved_and_revocable(self):
        now = fields.Datetime.now()
        grant = self.env["cc.break.glass.grant"].with_user(self.technical).create(
            {
                "user_id": self.technical.id,
                "reason": "Synthetic staging security verification",
                "source_ticket": "INC-STAGING-SECURITY",
                "starts_at": now - timedelta(minutes=1),
                "ends_at": now + timedelta(hours=1),
            }
        )
        grant.with_user(self.technical).action_submit()
        grant.with_user(self.approver).action_activate()
        self.assertTrue(
            self.Campaign.with_user(self.technical).search(
                [("id", "=", self.campaign_a.id)]
            )
        )
        grant.with_user(self.technical).action_revoke()
        self.assertFalse(self.Campaign.with_user(self.technical).search([]))

    def test_membership_cannot_be_reassigned_or_deleted(self):
        with self.assertRaises(AccessError):
            self.agent_membership.with_user(self.approver).write(
                {"campaign_id": self.campaign_b.id}
            )
        with self.assertRaises(AccessError):
            self.agent_membership.with_user(self.approver).unlink()

    def test_active_membership_requires_separate_approval_and_read_back(self):
        membership = self.Membership.with_user(self.requester).create(
            {
                "user_id": self.qa_analyst.id,
                "employee_id": self.qa_employee.id,
                "campaign_id": self.campaign_a.id,
                "role": "qa",
                "state": "pending_approval",
                "requested_by_id": self.requester.id,
                "source_ticket": "SEC-NO-READBACK",
            }
        )
        with self.assertRaises(AccessError):
            membership.with_user(self.requester).action_activate()
        with self.assertRaises(ValidationError):
            membership.with_user(self.approver).action_activate()

        wrong_role = self.Membership.with_user(self.requester).create(
            self._identity_safe_values(
                {
                "user_id": self.other_agent.id,
                "employee_id": self.other_employee.id,
                "campaign_id": self.campaign_a.id,
                "role": "qa",
                "state": "pending_sync",
                "requested_by_id": self.requester.id,
                "source_ticket": "SEC-WRONG-ROLE",
                "starts_at": fields.Datetime.now(),
                "last_sync_status": "matched",
                "read_back_evidence": "synthetic staging read-back matched",
                }
            )
        )
        self._synchronize_identity(wrong_role)
        with self.assertRaises(ValidationError):
            wrong_role.with_user(self.approver).action_activate()