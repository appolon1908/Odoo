from datetime import timedelta

from odoo import Command, api, fields
from odoo.modules.registry import Registry
from odoo.tests.common import BaseCase, get_db_name, tagged


@tagged("-at_install", "post_install")
class TestCallCenterCRMQueueConcurrency(BaseCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.registry = Registry(get_db_name())
        with cls.registry.cursor() as cr:
            env = api.Environment(cr, api.SUPERUSER_ID, {})
            Users = env["res.users"].with_context(no_reset_password=True)
            group_agent = env.ref("callcenter_crm.group_callcenter_agent")
            group_supervisor = env.ref("callcenter_crm.group_callcenter_supervisor")
            group_superuser = env.ref("callcenter_crm.group_callcenter_superuser")

            cls.ops = Users.create({
                "name": "Concurrent Queue Ops",
                "login": "concurrent.queue.ops@example.test",
                "email": "concurrent.queue.ops@example.test",
                "group_ids": [
                    Command.link(group_superuser.id),
                    Command.link(group_supervisor.id),
                ],
            })
            cls.agent_1 = Users.create({
                "name": "Concurrent Agent One",
                "login": "concurrent.agent.one@example.test",
                "email": "concurrent.agent.one@example.test",
                "group_ids": [Command.link(group_agent.id)],
            })
            cls.agent_2 = Users.create({
                "name": "Concurrent Agent Two",
                "login": "concurrent.agent.two@example.test",
                "email": "concurrent.agent.two@example.test",
                "group_ids": [Command.link(group_agent.id)],
            })

            now = fields.Datetime.now()
            campaign = env["callcenter.campaign"].with_user(cls.ops).create({
                "name": "Concurrent Queue Campaign",
                "code": "CONCURRENCY-QUEUE-1",
                "campaign_type": "outbound",
                "primary_supervisor_id": cls.ops.id,
                "start_at": now - timedelta(hours=1),
                "end_at": now + timedelta(days=1),
            })
            campaign.with_user(cls.ops).action_assign_agent(cls.agent_1)
            campaign.with_user(cls.ops).action_assign_agent(cls.agent_2)
            campaign.with_user(cls.ops).action_ready()
            campaign.with_user(cls.ops).action_activate()

            leads = env["crm.lead"].with_user(cls.ops).create([
                {
                    "name": "Concurrent Lead A",
                    "phone": "8095559101",
                    "cc_campaign_id": campaign.id,
                    "user_id": False,
                    "queue_state": "available",
                },
                {
                    "name": "Concurrent Lead B",
                    "phone": "8095559102",
                    "cc_campaign_id": campaign.id,
                    "user_id": False,
                    "queue_state": "available",
                },
            ])
            cls.campaign_id = campaign.id
            cls.team_id = campaign.crm_team_id.id
            cls.lead_ids = leads.ids
            cls.agent_ids = [cls.agent_1.id, cls.agent_2.id]

    @classmethod
    def tearDownClass(cls):
        with cls.registry.cursor() as cr:
            env = api.Environment(cr, api.SUPERUSER_ID, {})
            env["callcenter.lead.assignment"].search([
                ("lead_id", "in", cls.lead_ids)
            ]).unlink()
            env["crm.lead"].with_context(active_test=False).browse(cls.lead_ids).unlink()
            env["callcenter.campaign.state.log"].search([
                ("campaign_id", "=", cls.campaign_id)
            ]).unlink()
            env["callcenter.campaign.assignment"].with_context(active_test=False).search([
                ("campaign_id", "=", cls.campaign_id)
            ]).unlink()
            campaign = env["callcenter.campaign"].with_context(active_test=False).browse(cls.campaign_id)
            team = env["crm.team"].with_context(active_test=False).browse(cls.team_id)
            if team.exists():
                team.write({"cc_campaign_id": False})
            if campaign.exists():
                campaign.unlink()
            if team.exists():
                team.unlink()
        super().tearDownClass()

    def test_two_open_transactions_get_distinct_next_leads(self):
        # Transaction 1 claims its lead and remains open, deliberately holding
        # the selected crm_lead row lock. Transaction 2 then executes the same
        # public queue action before transaction 1 commits. PostgreSQL
        # FOR UPDATE SKIP LOCKED must force transaction 2 onto the other lead.
        with self.registry.cursor() as cr1:
            env1 = api.Environment(cr1, self.agent_ids[0], {})
            action_1 = env1["crm.lead"].action_cc_get_next_lead()
            lead_1 = action_1 and action_1.get("res_id")
            self.assertTrue(lead_1)

            with self.registry.cursor() as cr2:
                env2 = api.Environment(cr2, self.agent_ids[1], {})
                action_2 = env2["crm.lead"].action_cc_get_next_lead()
                lead_2 = action_2 and action_2.get("res_id")
                self.assertTrue(lead_2)
                self.assertNotEqual(lead_1, lead_2)
                self.assertEqual({lead_1, lead_2}, set(self.lead_ids))

        with self.registry.cursor() as cr:
            env = api.Environment(cr, api.SUPERUSER_ID, {})
            assigned = env["crm.lead"].browse(self.lead_ids)
            self.assertEqual(set(assigned.mapped("user_id").ids), set(self.agent_ids))
            histories = env["callcenter.lead.assignment"].search([
                ("lead_id", "in", self.lead_ids),
                ("released_at", "=", False),
                ("assignment_source", "=", "queue"),
            ])
            self.assertEqual(len(histories), 2)
