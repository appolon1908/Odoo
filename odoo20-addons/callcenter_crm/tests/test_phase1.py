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
        cls.agent_b2 = user("agent.b2", cls.group_agent)
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
        cls.campaign_b.with_user(cls.ops).action_assign_agent(cls.agent_b2)
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
        self.assertEqual(self.env.ref("callcenter.group_callcenter_agent"), self.group_agent)
        self.assertEqual(self.env.ref("callcenter.group_callcenter_supervisor"), self.group_supervisor)
        self.assertEqual(self.env.ref("callcenter.group_callcenter_superuser"), self.group_superuser)

    def test_02_campaign_is_authoritative_and_native_assignment_is_off(self):
        self.assertEqual(self.campaign_a.code, "CAMP-A")
        self.assertEqual(self.campaign_a.crm_team_id.cc_campaign_id, self.campaign_a)
        self.assertTrue(self.campaign_a.crm_team_id.assignment_optout)
        self.assertEqual(self.campaign_a.crm_team_id.user_id, self.sup_a)
        self.assertTrue(self.campaign_a.crm_team_id.is_callcenter_campaign)
        self.assertEqual(self.campaign_a.crm_team_id.campaign_code, "CAMP-A")
        self.assertEqual(self.campaign_a.crm_team_id.campaign_type, "outbound")
        self.assertEqual(self.campaign_a.crm_team_id.campaign_state, "active")
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
        other_supervisor = self.env["crm.lead"].with_user(self.sup_b).search([("id", "=", own.id)])
        self.assertFalse(other_supervisor)
        superuser_visible = self.env["crm.lead"].with_user(self.ops).search([("id", "=", own.id)])
        self.assertEqual(superuser_visible, own)

    def test_05_saved_lead_lock_reassignment_delete_export_and_archive(self):
        lead = self.env["crm.lead"].with_user(self.agent_a1).create({
            "name": "Locked Lead", "phone": "8095550103"
        })
        with self.assertRaises(AccessError):
            lead.with_user(self.agent_a1).write({"name": "Agent Edit"})
        with self.assertRaises(AccessError):
            lead.with_user(self.agent_a1).write({"user_id": self.agent_a2.id})
        with self.assertRaises(AccessError):
            lead.with_user(self.sup_a).write({"name": "Supervisor Edit"})
        with self.assertRaises(AccessError):
            lead.with_user(self.sup_a).write({"user_id": self.agent_a2.id})
        with self.assertRaises(AccessError):
            lead.with_user(self.agent_a1).export_data(["name", "phone"])
        with self.assertRaises(AccessError):
            lead.with_user(self.sup_a).export_data(["name", "phone"])
        with self.assertRaises(AccessError):
            lead.with_user(self.ops).unlink()

        lead.with_user(self.ops).write({"name": "Admin Edit"})
        self.assertEqual(lead.name, "Admin Edit")
        lead.with_user(self.ops).write({"active": False})
        self.assertFalse(lead.active)
        lead.with_user(self.ops).write({"active": True})
        self.assertTrue(lead.active)

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
        self.assertEqual(set(history.mapped("assignment_source")), {"queue"})

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

    def test_10b_supervisor_transfer_preserves_history_and_native_leader(self):
        Assignment = self.env["callcenter.campaign.assignment"].sudo().with_context(active_test=False)
        original = Assignment.search([
            ("campaign_id", "=", self.campaign_a.id),
            ("user_id", "=", self.sup_a.id),
            ("role", "=", "supervisor"),
            ("is_primary", "=", True),
            ("active", "=", True),
        ], limit=1)
        self.assertTrue(original)

        self.campaign_a.with_user(self.ops).write({
            "primary_supervisor_id": self.sup_b.id,
            "backup_supervisor_ids": [Command.set([self.sup_a.id])],
        })
        original.invalidate_recordset()
        self.assertFalse(original.active)
        self.assertTrue(original.date_to)

        replacement = Assignment.search([
            ("campaign_id", "=", self.campaign_a.id),
            ("user_id", "=", self.sup_b.id),
            ("role", "=", "supervisor"),
            ("is_primary", "=", True),
            ("active", "=", True),
        ])
        backup = Assignment.search([
            ("campaign_id", "=", self.campaign_a.id),
            ("user_id", "=", self.sup_a.id),
            ("role", "=", "supervisor"),
            ("is_primary", "=", False),
            ("active", "=", True),
        ])
        self.assertEqual(len(replacement), 1)
        self.assertEqual(len(backup), 1)
        self.assertEqual(self.campaign_a.crm_team_id.user_id, self.sup_b)
        native_members = self.env["crm.team.member"].sudo().search([
            ("crm_team_id", "=", self.campaign_a.crm_team_id.id),
            ("active", "=", True),
        ])
        self.assertNotIn(self.sup_b.id, native_members.user_id.ids)
        self.assertNotIn(self.sup_a.id, native_members.user_id.ids)

    def test_10c_duplicate_campaign_code_is_rejected_without_orphan_team(self):
        Team = self.env["crm.team"].sudo().with_context(active_test=False)
        before = Team.search_count([("name", "=", "Duplicate Campaign Code Probe")])
        with self.assertRaises(ValidationError):
            self.env["callcenter.campaign"].with_user(self.ops).create({
                "name": "Duplicate Campaign Code Probe",
                "code": "camp-a",
                "campaign_type": "outbound",
                "primary_supervisor_id": self.sup_a.id,
                "start_at": fields.Datetime.now() - timedelta(hours=1),
                "end_at": fields.Datetime.now() + timedelta(hours=1),
            })
        after = Team.search_count([("name", "=", "Duplicate Campaign Code Probe")])
        self.assertEqual(before, after)



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
        with self.assertRaises(ValidationError):
            campaign.action_pause()
        campaign.action_pause("Client requested temporary stop")
        campaign.action_resume()
        with self.assertRaises(ValidationError):
            campaign.action_close()
        campaign.action_close("Campaign period completed")
        self.assertEqual(campaign.state, "closed")
        with self.assertRaises(ValidationError):
            campaign.action_activate()
        duplicate = campaign.action_duplicate_as_draft("LIFE-2")
        self.assertEqual(duplicate.state, "draft")
        self.assertTrue(duplicate.active)
        self.assertFalse(duplicate.assignment_ids.filtered(lambda a: a.role == "agent"))
        with self.assertRaises(ValidationError):
            campaign.action_archive()
        campaign.action_archive("Reporting period sealed")
        self.assertFalse(campaign.active)
        self.assertFalse(campaign.crm_team_id.active)
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

    def test_16_supervisor_create_is_unassigned_then_locked(self):
        lead = self.env["crm.lead"].with_user(self.sup_a).create({
            "name": "Supervisor Lead", "phone": "8095550700",
            "cc_campaign_id": self.campaign_a.id,
        })
        self.assertFalse(lead.user_id)
        self.assertEqual(lead.queue_state, "available")
        with self.assertRaises(AccessError):
            lead.with_user(self.sup_a).write({"name": "Supervisor Edited"})
        with self.assertRaises(AccessError):
            lead.with_user(self.sup_a).write({"cc_campaign_id": self.campaign_b.id})

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
        campaign.action_close("Completed")
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

    def test_19_technical_administrator_retains_campaign_access(self):
        technical_admin = self.env.ref("base.user_admin")
        self.assertTrue(technical_admin.has_group("base.group_system"))
        visible = self.env["callcenter.campaign"].with_user(technical_admin).search([
            ("id", "=", self.campaign_a.id)
        ])
        self.assertEqual(visible, self.campaign_a)
        native_team = self.env["crm.team"].with_user(technical_admin).search([
            ("id", "=", self.campaign_a.crm_team_id.id)
        ])
        self.assertEqual(native_team, self.campaign_a.crm_team_id)
        with self.assertRaises(AccessError):
            self.campaign_a.with_user(technical_admin).write({"description": "Forbidden operational edit"})
        with self.assertRaises(AccessError):
            native_team.with_user(technical_admin).write({"name": "Forbidden native edit"})

    def test_20_paused_and_closed_campaign_history_is_read_only_but_visible(self):
        lead = self.env["crm.lead"].with_user(self.agent_a1).create({
            "name": "Historical Lead", "phone": "8095550900"
        })
        self.campaign_a.with_user(self.ops).action_pause("Client pause")
        visible_agent = self.env["crm.lead"].with_user(self.agent_a1).search([("id", "=", lead.id)])
        visible_supervisor = self.env["crm.lead"].with_user(self.sup_a).search([("id", "=", lead.id)])
        self.assertEqual(visible_agent, lead)
        self.assertEqual(visible_supervisor, lead)
        with self.assertRaises(AccessError):
            lead.with_user(self.agent_a1).write({"name": "No paused edit"})
        with self.assertRaises(AccessError):
            lead.with_user(self.sup_a).write({"name": "No paused supervisor edit"})
        self.campaign_a.with_user(self.ops).action_close("Campaign complete")
        self.assertEqual(
            self.env["crm.lead"].with_user(self.agent_a1).search([("id", "=", lead.id)]),
            lead,
        )
        self.assertEqual(
            self.env["crm.lead"].with_user(self.sup_a).search([("id", "=", lead.id)]),
            lead,
        )

    def test_21_preloaded_non_active_leads_remain_unassigned(self):
        now = fields.Datetime.now()
        campaign = self.env["callcenter.campaign"].with_user(self.ops).create({
            "name": "Preload", "code": "PRELOAD-1", "campaign_type": "outbound",
            "primary_supervisor_id": self.sup_a.id,
            "start_at": now + timedelta(days=1),
            "end_at": now + timedelta(days=30),
        })
        campaign.action_assign_agent(self.agent_a1)
        lead = self.env["crm.lead"].with_user(self.ops).create({
            "name": "Draft preload", "phone": "8095550999", "cc_campaign_id": campaign.id,
        })
        self.assertFalse(lead.user_id)
        self.assertEqual(lead.queue_state, "available")
        with self.assertRaises(AccessError):
            self.env["crm.lead"].with_user(self.ops).create({
                "name": "Assigned draft preload", "phone": "8095550998",
                "cc_campaign_id": campaign.id, "user_id": self.agent_a1.id,
            })


    def test_22_active_campaign_cannot_lose_last_agent(self):
        now = fields.Datetime.now()
        campaign = self.env["callcenter.campaign"].with_user(self.ops).create({
            "name": "Staffing Guard", "code": "STAFF-1", "campaign_type": "outbound",
            "primary_supervisor_id": self.sup_a.id,
            "start_at": now - timedelta(hours=1),
            "end_at": now + timedelta(days=1),
        })
        campaign.action_assign_agent(self.agent_b1)
        campaign.action_ready()
        campaign.action_activate()
        with self.assertRaises(ValidationError):
            campaign.action_remove_agent(self.agent_b1)

    def test_23_transfer_requeues_current_lead_and_preserves_history(self):
        now = fields.Datetime.now()
        target = self.env["callcenter.campaign"].with_user(self.ops).create({
            "name": "Transfer Target", "code": "TRANSFER-TARGET-1", "campaign_type": "outbound",
            "primary_supervisor_id": self.sup_b.id,
            "start_at": now - timedelta(hours=1),
            "end_at": now + timedelta(days=1),
        })
        target.action_assign_agent(self.agent_b1)
        target.action_ready()
        target.action_activate()

        lead = self.env["crm.lead"].with_user(self.agent_a1).create({
            "name": "Transfer Working Lead", "phone": "8095551111"
        })
        self.campaign_a.with_user(self.ops).action_assign_agent(self.agent_b1)
        target.with_user(self.ops).action_assign_agent(self.agent_a1)

        lead.invalidate_recordset()
        self.assertFalse(lead.user_id)
        self.assertEqual(lead.queue_state, "available")
        history = self.env["callcenter.lead.assignment"].sudo().search([
            ("lead_id", "=", lead.id)
        ], order="id desc", limit=1)
        self.assertTrue(history.released_at)

    def test_24_active_primary_supervisor_and_user_roles_are_protected(self):
        with self.assertRaises(ValidationError):
            self.campaign_a.with_user(self.ops).write({"primary_supervisor_id": False})
        with self.assertRaises(ValidationError):
            self.agent_a1.sudo().write({"active": False})
        with self.assertRaises(ValidationError):
            self.sup_a.sudo().write({
                "group_ids": [Command.unlink(self.group_supervisor.id)]
            })

    def test_25_import_audit_records_are_immutable_to_operational_admin(self):
        batch = self.env["callcenter.lead.import.batch"].sudo().create({
            "campaign_id": self.campaign_a.id,
            "filename": "audit.csv",
            "file_hash": "a" * 64,
            "imported_by_id": self.ops.id,
            "state": "validated",
            "total_rows": 1,
        })
        line = self.env["callcenter.lead.import.line"].sudo().create({
            "batch_id": batch.id,
            "row_number": 2,
            "raw_data": {"name": "Audit"},
            "status": "ready",
        })
        with self.assertRaises(AccessError):
            batch.with_user(self.ops).write({"total_rows": 999})
        with self.assertRaises(AccessError):
            line.with_user(self.ops).write({"status": "error"})
