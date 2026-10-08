import threading
from concurrent.futures import ThreadPoolExecutor
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
            group_superuser = env.ref("callcenter_crm.group_callcenter_superuser")

            cls.ops = Users.create({
                "name": "Concurrent Queue Ops",
                "login": "concurrent.queue.ops@example.test",
                "email": "concurrent.queue.ops@example.test",
                "group_ids": [Command.link(group_superuser.id)],
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
                "primary_supervisor_id": False,
                "start_at": now - timedelta(hours=1),
                "end_at": now + timedelta(days=1),
            })
            # Ready requires a primary supervisor. Promote the ops user only for the
            # campaign-specific supervisor role while preserving its operational
            # superuser role and no technical-admin implication.
            group_supervisor = env.ref("callcenter_crm.group_callcenter_supervisor")
            cls.ops.sudo().write({"group_ids": [Command.link(group_supervisor.id)]})
            campaign.with_user(cls.ops).write({"primary_supervisor_id": cls.ops.id})
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
            cls.ops_id = cls.ops.id
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
            env["res.users"].browse([cls.ops_id] + cls.agent_ids).unlink()
        super().tearDownClass()

    def test_simultaneous_get_next_lead_never_returns_same_record(self):
        barrier = threading.Barrier(2)

        def claim(user_id):
            with self.registry.cursor() as cr:
                env = api.Environment(cr, user_id, {})
                barrier.wait(timeout=10)
                action = env["crm.lead"].action_cc_get_next_lead()
                return action and action.get("res_id")

        with ThreadPoolExecutor(max_workers=2) as executor:
            future_1 = executor.submit(claim, self.agent_ids[0])
            future_2 = executor.submit(claim, self.agent_ids[1])
            lead_1 = future_1.result(timeout=15)
            lead_2 = future_2.result(timeout=15)

        self.assertTrue(lead_1)
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
