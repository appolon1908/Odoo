import base64
import io
from datetime import timedelta

from openpyxl import Workbook

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import TransactionCase


class TestCallCenterCRMPhase1(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        cls.group_agent = cls.env.ref("callcenter_crm.group_callcenter_agent")
        cls.group_supervisor = cls.env.ref("callcenter_crm.group_callcenter_supervisor")
        cls.group_superuser = cls.env.ref("callcenter_crm.group_callcenter_superuser")
        cls.group_sales_manager = cls.env.ref("sales_team.group_sale_manager")

        def user(login, group):
            return Users.create({
                "name": login,
                "login": login + "@example.test",
                "email": login + "@example.test",
                "group_ids": [Command.link(group.id)],
            })

        cls.ops = user("cc.ops", cls.group_superuser)
        cls.sup_a = user("sup.a", cls.group_supervisor)
        cls.sup_b = user("sup.b", cls.group_supervisor)
        cls.agent_a1 = user("agent.a1", cls.group_agent)
        cls.agent_a2 = user("agent.a2", cls.group_agent)
        cls.agent_b1 = user("agent.b1", cls.group_agent)
        cls.sales_manager = user("sales.manager", cls.group_sales_manager)

        now = fields.Datetime.now()
        Campaign = cls.env["callcenter.campaign"].with_user(cls.ops)
        cls.campaign_a = Campaign.create({
            "name": "Campaign A",
            "code": "camp-a",
            "campaign_type": "outbound",
            "primary_supervisor_id": cls.sup_a.id,
            "start_at": now - timedelta(days=1),
            "end_at": now + timedelta(days=30),
        })
        cls.campaign_b = Campaign.create({
            "name": "Campaign B",
            "code": "camp-b",
            "campaign_type": "blended",
            "primary_supervisor_id": cls.sup_b.id,
            "start_at": now - timedelta(days=1),
            "end_at": now + timedelta(days=30),
        })
        cls.campaign_a.with_user(cls.ops).action_assign_agent(cls.agent_a1)
        cls.campaign_a.with_user(cls.ops).action_assign_agent(cls.agent_a2)
        cls.campaign_b.with_user(cls.ops).action_assign_agent(cls.agent_b1)
        cls.campaign_a.with_user(cls.ops).action_ready()
        cls.campaign_a.with_user(cls.ops).action_activate()
        cls.campaign_b.with_user(cls.ops).action_ready()
        cls.campaign_b.with_user(cls.ops).action_activate()

    def _lead(self, campaign, phone, **extra):
        vals = {"name": "Lead " + phone, "phone": phone, "cc_campaign_id": campaign.id}
        vals.update(extra)
        return self.env["crm.lead"].with_user(self.ops).create(vals)

    def test_01_roles_are_separate_from_technical_admin(self):
        self.assertTrue(self.ops.has_group("callcenter_crm.group_callcenter_superuser"))
        self.assertFalse(self.ops.has_group("base.group_system"))
        self.assertFalse(self.ops.has_group("sales_team.group_sale_manager"))

    def test_02_campaign_is_authoritative_and_native_assignment_is_off(self):
        self.assertEqual(self.campaign_a.code, "CAMP-A")
        self.assertEqual(self.campaign_a.crm_team_id.cc_campaign_id, self.campaign_a)
        self.assertTrue(self.campaign_a.crm_team_id.assignment_optout)
        self.assertEqual(self.campaign_a.crm_team_id.user_id, self.sup_a)
        members = self.env["crm.team.member"].sudo().search([
            ("crm_team_id", "=", self.campaign_a.crm_team_id.id), ("active", "=", True)
        ])
        self.assertEqual(set(members.user_id.ids), {self.agent_a1.id, self.agent_a2.id})
        self.assertNotIn(self.sup_a.id, members.user_id.ids)

    def test_03_regular_sales_manager_cannot_enumerate_callcenter_records(self):
        teams = self.env["crm.team"].with_user(self.sales_manager).search([
            ("id", "=", self.campaign_a.crm_team_id.id)
        ])
        self.assertFalse(teams)
        lead = self._lead(self.campaign_a, "8095550101")
        visible = self.env["crm.lead"].with_user(self.sales_manager).search([("id", "=", lead.id)])
        self.assertFalse(visible)

    def test_04_agent_and_supervisor_isolation(self):
        own = self.env["crm.lead"].with_user(self.agent_a1).create({
            "name": "Agent A1", "phone": "8095550102"
        })
        self.assertEqual(own.cc_campaign_id, self.campaign_a)
        self.assertEqual(own.user_id, self.agent_a1)
        self.assertEqual(own.queue_state, "assigned")
        other = self.env["crm.lead"].with_user(self.agent_a2).search([("id", "=", own.id)])
        self.assertFalse(other)
        supervisor = self.env["crm.lead"].with_user(self.sup_a).search([("id", "=", own.id)])
        self.assertEqual(supervisor, own)

    def test_05_saved_lead_lock_and_delete_export_protection(self):
        lead = self.env["crm.lead"].with_user(self.agent_a1).create({
            "name": "Locked Lead", "phone": "8095550103"
        })
        with self.assertRaises(AccessError):
            lead.with_user(self.agent_a1).write({"name": "Agent Edit"})
        with self.assertRaises(AccessError):
            lead.with_user(self.sup_a).write({"name": "Supervisor Edit"})
        lead.with_user(self.ops).write({"name": "Admin Edit"})
        self.assertEqual(lead.name, "Admin Edit")
        with self.assertRaises(AccessError):
            lead.with_user(self.ops).unlink()
        with self.assertRaises(AccessError):
            lead.with_user(self.agent_a1).export_data(["name", "phone"])
        lead.with_user(self.ops).write({"active": False})
        self.assertFalse(lead.active)

    def test_06_generic_import_is_blocked_for_agent(self):
        with self.assertRaises(AccessError):
            self.env["crm.lead"].with_user(self.agent_a1).load(
                ["name", "phone"], [["Bypass", "8095550104"]]
            )

    def test_07_duplicate_phone_email_external_and_cross_campaign(self):
        self._lead(self.campaign_a, "8095550200", source_external_id="EXT-1")
        with self.assertRaises(ValidationError):
            self._lead(self.campaign_a, "(809) 555-0200")
        with self.assertRaises(ValidationError):
            self._lead(self.campaign_a, "8095550201", source_external_id="EXT-1")
        email_only = self.env["crm.lead"].with_user(self.ops).create({
            "name": "Email Only", "email_from": "Person@Example.com",
            "cc_campaign_id": self.campaign_a.id,
        })
        self.assertTrue(email_only)
        with self.assertRaises(ValidationError):
            self.env["crm.lead"].with_user(self.ops).create({
                "name": "Email Duplicate", "email_from": "person@example.com",
                "cc_campaign_id": self.campaign_a.id,
            })
        cross = self._lead(self.campaign_b, "8095550200")
        self.assertTrue(cross.cross_campaign_duplicate)

    def test_08_one_current_lead_per_agent(self):
        first = self.env["crm.lead"].with_user(self.agent_a1).create({
            "name": "Working", "phone": "8095550300"
        })
        self.assertEqual(first.queue_state, "assigned")
        with self.assertRaises(ValidationError):
            self.env["crm.lead"].with_user(self.agent_a1).create({
                "name": "Second", "phone": "8095550301"
            })

    def test_09_queue_assigns_distinct_leads_and_records_history(self):
        lead1 = self._lead(self.campaign_a, "8095550400")
        lead2 = self._lead(self.campaign_a, "8095550401")
        action1 = self.env["crm.lead"].with_user(self.agent_a1).action_cc_get_next_lead()
        action2 = self.env["crm.lead"].with_user(self.agent_a2).action_cc_get_next_lead()
        self.assertNotEqual(action1["res_id"], action2["res_id"])
        self.assertEqual({action1["res_id"], action2["res_id"]}, {lead1.id, lead2.id})
        history = self.env["callcenter.lead.assignment"].sudo().search([
            ("lead_id", "in", [lead1.id, lead2.id]), ("released_at", "=", False)
        ])
        self.assertEqual(len(history), 2)
        self.assertEqual(set(history.assignment_source), {"queue"})

    def test_10_agent_transfer_preserves_campaign_and_native_membership_history(self):
        old = self.env["callcenter.campaign.assignment"].sudo().search([
            ("user_id", "=", self.agent_a1.id), ("role", "=", "agent"), ("active", "=", True)
        ])
        self.campaign_b.with_user(self.ops).action_assign_agent(self.agent_a1)
        old.invalidate_recordset()
        self.assertFalse(old.active)
        self.assertTrue(old.date_to)
        current = self.env["callcenter.campaign.assignment"].sudo().search([
            ("user_id", "=", self.agent_a1.id), ("campaign_id", "=", self.campaign_b.id),
            ("role", "=", "agent"), ("active", "=", True)
        ])
        self.assertEqual(len(current), 1)
        old_member = self.env["crm.team.member"].sudo().with_context(active_test=False).search([
            ("crm_team_id", "=", self.campaign_a.crm_team_id.id), ("user_id", "=", self.agent_a1.id)
        ], limit=1)
        new_member = self.env["crm.team.member"].sudo().search([
            ("crm_team_id", "=", self.campaign_b.crm_team_id.id), ("user_id", "=", self.agent_a1.id),
            ("active", "=", True)
        ], limit=1)
        self.assertFalse(old_member.active)
        self.assertTrue(new_member)

    def test_11_lifecycle_and_state_audit(self):
        now = fields.Datetime.now()
        campaign = self.env["callcenter.campaign"].with_user(self.ops).create({
            "name": "Lifecycle", "code": "life-1", "campaign_type": "inbound",
            "primary_supervisor_id": self.sup_a.id,
            "start_at": now - timedelta(hours=1), "end_at": now + timedelta(hours=2),
        })
        campaign.action_assign_agent(self.agent_a1)
        campaign.action_ready()
        campaign.action_activate()
        campaign.action_pause()
        campaign.action_resume()
        campaign.action_close()
        self.assertEqual(campaign.state, "closed")
        with self.assertRaises(ValidationError):
            campaign.action_activate()
        campaign.action_archive()
        self.assertFalse(campaign.active)
        logs = self.env["callcenter.campaign.state.log"].sudo().search([
            ("campaign_id", "=", campaign.id)
        ])
        self.assertIn("archived", logs.mapped("to_state"))

    def test_12_direct_state_write_is_blocked(self):
        with self.assertRaises(AccessError):
            self.campaign_a.with_user(self.ops).write({"state": "paused"})

    def test_13_scheduled_end_pauses_not_closes(self):
        self.campaign_a.with_user(self.ops).write({
            "end_at": fields.Datetime.now() - timedelta(minutes=1)
        })
        self.env["callcenter.campaign"].sudo()._cron_pause_expired_campaigns()
        self.campaign_a.invalidate_recordset()
        self.assertEqual(self.campaign_a.state, "paused")
        latest = self.env["callcenter.campaign.state.log"].sudo().search([
            ("campaign_id", "=", self.campaign_a.id)
        ], order="id desc", limit=1)
        self.assertEqual(latest.to_state, "paused")
        self.assertEqual(latest.reason, "Scheduled campaign end reached")

    def test_14_csv_import_preview_import_and_duplicate_file(self):
        content = (
            "Contact Name,Phone,Email,External ID,Company,Priority,Source,Notes\n"
            "Alice,8095550500,alice@example.com,A-1,ACME,High,Web,First row\n"
            "Alice Duplicate,8095550500,alice2@example.com,A-2,ACME,Low,Web,Duplicate\n"
            "Bad Row,,bad@example.com,BAD-1,ACME,Low,Web,Missing phone\n"
        ).encode()
        Wizard = self.env["callcenter.lead.import.wizard"].with_user(self.ops)
        wizard = Wizard.create({
            "campaign_id": self.campaign_a.id,
            "filename": "leads.csv",
            "upload_file": base64.b64encode(content).decode(),
        })
        wizard.action_validate()
        batch = wizard.batch_id.sudo()
        self.assertEqual(batch.total_rows, 3)
        self.assertEqual(batch.duplicate_count, 1)
        self.assertEqual(batch.error_count, 1)
        wizard.action_import()
        self.assertEqual(batch.state, "imported")
        self.assertEqual(batch.created_count, 1)
        imported = self.env["crm.lead"].sudo().search([
            ("source_batch_id", "=", batch.id)
        ])
        self.assertEqual(len(imported), 1)
        self.assertEqual(imported.queue_state, "available")
        self.assertFalse(imported.user_id)

        duplicate = Wizard.create({
            "campaign_id": self.campaign_a.id,
            "filename": "leads.csv",
            "upload_file": base64.b64encode(content).decode(),
        })
        with self.assertRaises(UserError):
            duplicate.action_validate()

    def test_15_xlsx_import_validation(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["Contact Name", "Phone", "External ID"])
        sheet.append(["XLSX Person", "8095550600", "X-1"])
        stream = io.BytesIO()
        workbook.save(stream)
        wizard = self.env["callcenter.lead.import.wizard"].with_user(self.ops).create({
            "campaign_id": self.campaign_a.id,
            "filename": "leads.xlsx",
            "upload_file": base64.b64encode(stream.getvalue()).decode(),
        })
        wizard.action_validate()
        self.assertEqual(wizard.batch_id.total_rows, 1)
        self.assertEqual(wizard.batch_id.error_count, 0)

    def test_16_supervisor_create_is_unassigned_and_saved_locked(self):
        lead = self.env["crm.lead"].with_user(self.sup_a).create({
            "name": "Supervisor Lead", "phone": "8095550700",
            "cc_campaign_id": self.campaign_a.id,
        })
        self.assertFalse(lead.user_id)
        self.assertEqual(lead.queue_state, "available")
        with self.assertRaises(AccessError):
            lead.with_user(self.sup_a).write({"name": "No"})

    def test_17_closed_campaign_blocks_new_operational_activity(self):
        now = fields.Datetime.now()
        campaign = self.env["callcenter.campaign"].with_user(self.ops).create({
            "name": "Close Test", "code": "close-1", "campaign_type": "outbound",
            "primary_supervisor_id": self.sup_a.id,
            "start_at": now - timedelta(hours=1), "end_at": now + timedelta(hours=1),
        })
        campaign.action_assign_agent(self.agent_a1)
        campaign.action_ready()
        campaign.action_activate()
        campaign.action_close()
        with self.assertRaises(ValidationError):
            campaign.action_assign_agent(self.agent_a2)
        with self.assertRaises(AccessError):
            self.env["crm.lead"].with_user(self.ops).create({
                "name": "Closed Lead", "phone": "8095550800", "cc_campaign_id": campaign.id
            })

    def test_18_superuser_cannot_grant_superuser_role(self):
        target = self.agent_a1
        with self.assertRaises(AccessError):
            target.with_user(self.ops).action_grant_callcenter_superuser()
