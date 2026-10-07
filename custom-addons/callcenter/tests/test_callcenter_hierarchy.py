from odoo import Command
from odoo.tests.common import TransactionCase
from odoo.exceptions import AccessError, ValidationError


class TestCallCenterHierarchy(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Users = cls.env["res.users"].with_context(no_reset_password=True)
        cls.superuser_group = cls.env.ref("callcenter.group_callcenter_superuser")
        cls.supervisor_group = cls.env.ref("callcenter.group_callcenter_supervisor")
        cls.agent_group = cls.env.ref("callcenter.group_callcenter_agent")

        cls.ops_superuser = cls.Users.create(
            {
                "name": "Call Center Operations",
                "login": "callcenter.ops@example.test",
                "email": "callcenter.ops@example.test",
                "group_ids": [Command.link(cls.superuser_group.id)],
            }
        )
        cls.supervisor_a = cls.Users.create(
            {
                "name": "Supervisor A",
                "login": "supervisor.a@example.test",
                "email": "supervisor.a@example.test",
                "group_ids": [Command.link(cls.supervisor_group.id)],
            }
        )
        cls.supervisor_b = cls.Users.create(
            {
                "name": "Supervisor B",
                "login": "supervisor.b@example.test",
                "email": "supervisor.b@example.test",
                "group_ids": [Command.link(cls.supervisor_group.id)],
            }
        )
        cls.agent = cls.Users.create(
            {
                "name": "Agent One",
                "login": "agent.one@example.test",
                "email": "agent.one@example.test",
                "group_ids": [Command.link(cls.agent_group.id)],
            }
        )

        cls.Team = cls.env["crm.team"].with_user(cls.ops_superuser)
        cls.campaign_a = cls.Team.create(
            {
                "name": "Campaign A",
                "is_callcenter_campaign": True,
                "campaign_code": "TEST-CAMP-A",
                "campaign_status": "active",
                "primary_supervisor_id": cls.supervisor_a.id,
            }
        )
        cls.campaign_b = cls.Team.create(
            {
                "name": "Campaign B",
                "is_callcenter_campaign": True,
                "campaign_code": "TEST-CAMP-B",
                "campaign_status": "active",
                "primary_supervisor_id": cls.supervisor_b.id,
            }
        )

    def test_operational_superuser_is_not_technical_admin(self):
        self.assertTrue(
            self.ops_superuser.has_group("callcenter.group_callcenter_superuser")
        )
        self.assertFalse(self.ops_superuser.has_group("base.group_system"))
        self.assertFalse(
            self.ops_superuser.has_group("sales_team.group_sale_manager")
        )

    def test_agent_transfer_preserves_assignment_history(self):
        agent = self.agent.with_user(self.ops_superuser)
        agent.action_assign_callcenter_campaign(self.campaign_a)

        active_a = self.env["callcenter.campaign.assignment"].sudo().search(
            [
                ("user_id", "=", self.agent.id),
                ("campaign_id", "=", self.campaign_a.id),
                ("role", "=", "agent"),
                ("active", "=", True),
            ]
        )
        self.assertEqual(len(active_a), 1)

        agent.action_assign_callcenter_campaign(self.campaign_b)

        history_a = self.env["callcenter.campaign.assignment"].sudo().search(
            [
                ("user_id", "=", self.agent.id),
                ("campaign_id", "=", self.campaign_a.id),
                ("role", "=", "agent"),
            ]
        )
        active_b = self.env["callcenter.campaign.assignment"].sudo().search(
            [
                ("user_id", "=", self.agent.id),
                ("campaign_id", "=", self.campaign_b.id),
                ("role", "=", "agent"),
                ("active", "=", True),
            ]
        )

        self.assertEqual(len(history_a), 1)
        self.assertFalse(history_a.active)
        self.assertTrue(history_a.date_to)
        self.assertEqual(len(active_b), 1)
        self.assertEqual(agent.callcenter_primary_campaign_id, self.campaign_b)

    def test_supervisor_change_preserves_history(self):
        assignments = self.env["callcenter.campaign.assignment"].sudo()
        original = assignments.search(
            [
                ("campaign_id", "=", self.campaign_a.id),
                ("user_id", "=", self.supervisor_a.id),
                ("role", "=", "supervisor"),
                ("is_primary", "=", True),
                ("active", "=", True),
            ]
        )
        self.assertEqual(len(original), 1)

        self.campaign_a.with_user(self.ops_superuser).write(
            {
                "primary_supervisor_id": self.supervisor_b.id,
                "backup_supervisor_ids": [Command.set([self.supervisor_a.id])],
            }
        )

        original.invalidate_recordset()
        self.assertFalse(original.active)
        self.assertTrue(original.date_to)

        backup = assignments.search(
            [
                ("campaign_id", "=", self.campaign_a.id),
                ("user_id", "=", self.supervisor_a.id),
                ("role", "=", "supervisor"),
                ("is_primary", "=", False),
                ("active", "=", True),
            ]
        )
        replacement = assignments.search(
            [
                ("campaign_id", "=", self.campaign_a.id),
                ("user_id", "=", self.supervisor_b.id),
                ("role", "=", "supervisor"),
                ("is_primary", "=", True),
                ("active", "=", True),
            ]
        )
        self.assertEqual(len(backup), 1)
        self.assertEqual(len(replacement), 1)

    def test_agent_cannot_transfer_self(self):
        with self.assertRaises(AccessError):
            self.agent.with_user(self.agent).action_assign_callcenter_campaign(
                self.campaign_a
            )

    def test_primary_supervisor_cannot_also_be_backup(self):
        with self.assertRaises(ValidationError):
            self.campaign_a.with_user(self.ops_superuser).write(
                {
                    "backup_supervisor_ids": [
                        Command.set([self.supervisor_a.id])
                    ]
                }
            )

    def test_campaign_lead_visibility_is_scoped(self):
        self.agent.with_user(self.ops_superuser).action_assign_callcenter_campaign(
            self.campaign_a
        )
        Lead = self.env["crm.lead"].with_user(self.ops_superuser)
        lead_a = Lead.create(
            {
                "name": "Campaign A Lead",
                "team_id": self.campaign_a.id,
            }
        )
        lead_b = Lead.create(
            {
                "name": "Campaign B Lead",
                "team_id": self.campaign_b.id,
            }
        )

        visible = self.env["crm.lead"].with_user(self.agent).search(
            [("id", "in", [lead_a.id, lead_b.id])]
        )
        self.assertEqual(visible, lead_a)
