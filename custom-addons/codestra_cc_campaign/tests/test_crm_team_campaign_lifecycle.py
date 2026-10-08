from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestCrmTeamCampaignLifecycle(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Team = cls.env["crm.team"]
        cls.Assignment = cls.env["callcenter.campaign.assignment"]
        cls.superuser = cls._user(
            "Campaign Super User",
            "campaign-superuser@example.invalid",
            ["codestra_cc_security.group_cc_global_administrator"],
        )
        cls.primary = cls._user(
            "Primary Supervisor",
            "campaign-primary@example.invalid",
            ["codestra_cc_security.group_cc_campaign_supervisor"],
        )
        cls.backup = cls._user(
            "Backup Supervisor",
            "campaign-backup@example.invalid",
            ["codestra_cc_security.group_cc_campaign_supervisor"],
        )
        cls.agent_a = cls._user(
            "Campaign Agent A",
            "campaign-agent-a@example.invalid",
            ["codestra_cc_security.group_cc_campaign_agent"],
        )
        cls.agent_b = cls._user(
            "Campaign Agent B",
            "campaign-agent-b@example.invalid",
            ["codestra_cc_security.group_cc_campaign_agent"],
        )

    @classmethod
    def _user(cls, name, login, groups):
        group_ids = [cls.env.ref(xmlid).id for xmlid in groups]
        return cls.env["res.users"].create(
            {
                "name": name,
                "login": login,
                "group_ids": [(6, 0, group_ids)],
            }
        )

    def _campaign(self, suffix="A"):
        return self.Team.with_user(self.superuser).create(
            {
                "name": f"Health Insurance {suffix}",
                "is_callcenter_campaign": True,
                "campaign_code": f"HINS-2026-{suffix}",
                "campaign_type": "outbound",
                "user_id": self.primary.id,
                "backup_supervisor_ids": [(6, 0, self.backup.ids)],
                "start_at": fields.Datetime.to_datetime("2026-10-01 00:00:00"),
                "end_at": fields.Datetime.to_datetime("2026-10-31 23:59:59"),
            }
        )

    def _assign(self, campaign, user):
        return self.Assignment.with_user(self.superuser).create(
            {
                "campaign_id": campaign.id,
                "user_id": user.id,
                "is_primary": True,
                "active": True,
            }
        )

    def test_only_superuser_can_create_or_modify_campaign_configuration(self):
        with self.assertRaises(AccessError):
            self.Team.with_user(self.agent_a).create(
                {
                    "name": "Forbidden",
                    "is_callcenter_campaign": True,
                    "campaign_code": "FORBIDDEN-2026-10",
                }
            )
        campaign = self._campaign()
        with self.assertRaises(AccessError):
            campaign.with_user(self.primary).write({"name": "Forbidden change"})
        campaign.with_user(self.superuser).write({"description": "Approved change"})
        self.assertEqual(campaign.description, "Approved change")

    def test_assignment_history_synchronizes_native_crm_membership(self):
        campaign = self._campaign()
        assignment = self._assign(campaign, self.agent_a)
        native = self.env["crm.team.member"].with_context(active_test=False).search(
            [
                ("crm_team_id", "=", campaign.id),
                ("user_id", "=", self.agent_a.id),
            ],
            limit=1,
        )
        self.assertTrue(native.active)
        self.assertIn(self.agent_a, campaign.member_ids)
        with self.assertRaises(AccessError):
            campaign.with_user(self.superuser).write(
                {"member_ids": [(4, self.agent_b.id)]}
            )
        assignment.with_user(self.superuser).action_end_assignment()
        self.assertFalse(assignment.active)
        self.assertTrue(assignment.date_to)
        self.assertGreater(assignment.date_to, assignment.date_from)
        self.assertFalse(native.active)
        self.assertNotIn(self.agent_a, campaign.member_ids)
        with self.assertRaises(AccessError):
            assignment.with_user(self.superuser).unlink()

    def test_ready_requires_supervisor_agent_and_valid_dates(self):
        campaign = self._campaign()
        with self.assertRaises(ValidationError):
            campaign.with_user(self.superuser).action_callcenter_ready()
        self._assign(campaign, self.agent_a)
        campaign.with_user(self.superuser).action_callcenter_ready()
        self.assertEqual(campaign.campaign_state, "ready")
        self.assertEqual(
            campaign.campaign_state_log_ids[:1].to_state,
            "ready",
        )

    def test_lifecycle_and_archive_are_server_side_and_audited(self):
        campaign = self._campaign()
        self._assign(campaign, self.agent_a)
        with self.assertRaises(AccessError):
            campaign.with_user(self.primary).write({"campaign_state": "active"})
        campaign.with_user(self.superuser).action_callcenter_ready()
        campaign.with_user(self.superuser).action_callcenter_activate()
        self.assertTrue(campaign.callcenter_operational)
        with self.assertRaises(ValidationError):
            campaign.with_user(self.superuser).action_callcenter_close()
        campaign.with_user(self.superuser).action_callcenter_pause(
            "Client requested temporary stop"
        )
        self.assertEqual(campaign.campaign_state, "paused")
        campaign.with_user(self.superuser).action_callcenter_resume()
        campaign.with_user(self.superuser).action_callcenter_close(
            "October campaign completed"
        )
        self.assertEqual(campaign.campaign_state, "closed")
        self.assertFalse(campaign.campaign_assignment_ids.filtered("active"))
        with self.assertRaises(ValidationError):
            campaign.with_user(self.superuser).action_callcenter_activate()
        campaign.with_user(self.superuser).action_callcenter_archive(
            "Reporting period sealed"
        )
        self.assertFalse(campaign.active)
        logs = campaign.with_context(active_test=False).campaign_state_log_ids
        self.assertEqual(
            set(logs.mapped("to_state")),
            {"ready", "active", "paused", "closed", "archived"},
        )

    def test_draft_and_paused_preload_stays_unassigned(self):
        campaign = self._campaign("PRELOAD")
        self._assign(campaign, self.agent_a)
        lead = self.env["crm.lead"].with_user(self.superuser).create(
            {
                "name": "Draft preload",
                "team_id": campaign.id,
            }
        )
        self.assertFalse(lead.user_id)
        with self.assertRaises(AccessError):
            self.env["crm.lead"].with_user(self.superuser).create(
                {
                    "name": "Forbidden assigned preload",
                    "team_id": campaign.id,
                    "user_id": self.agent_a.id,
                }
            )
        campaign.with_user(self.superuser).action_callcenter_ready()
        campaign.with_user(self.superuser).action_callcenter_activate()
        lead.with_user(self.superuser).write({"user_id": self.agent_a.id})
        self.assertEqual(lead.user_id, self.agent_a)
        campaign.with_user(self.superuser).action_callcenter_pause("Client pause")
        with self.assertRaises(AccessError):
            lead.with_user(self.superuser).write({"user_id": self.agent_a.id})

    def test_reason_wizard_executes_audited_pause(self):
        campaign = self._campaign("WIZARD")
        self._assign(campaign, self.agent_a)
        campaign.with_user(self.superuser).action_callcenter_ready()
        campaign.with_user(self.superuser).action_callcenter_activate()
        wizard = self.env["callcenter.campaign.transition.wizard"].with_user(
            self.superuser
        ).create(
            {
                "campaign_id": campaign.id,
                "target_state": "paused",
                "reason": "Customer requested a temporary pause",
            }
        )
        wizard.action_confirm()
        self.assertEqual(campaign.campaign_state, "paused")
        log = campaign.campaign_state_log_ids.filtered(
            lambda row: row.to_state == "paused"
        )[:1]
        self.assertEqual(log.reason, "Customer requested a temporary pause")

    def test_removed_supervisor_keeps_historical_visibility(self):
        campaign = self._campaign("HISTORY")
        self.assertIn(self.primary, campaign.supervisor_history_ids)
        self.assertIn(self.backup, campaign.supervisor_history_ids)
        campaign.with_user(self.superuser).write(
            {
                "user_id": False,
                "backup_supervisor_ids": [(5, 0, 0)],
            }
        )
        self.assertIn(self.primary, campaign.supervisor_history_ids)
        self.assertIn(self.backup, campaign.supervisor_history_ids)
        self.assertEqual(
            self.Team.with_user(self.backup).search([("id", "=", campaign.id)]),
            campaign,
        )

    def test_paused_campaign_blocks_new_call_records(self):
        campaign = self._campaign()
        self._assign(campaign, self.agent_a)
        campaign.with_user(self.superuser).action_callcenter_ready()
        campaign.with_user(self.superuser).action_callcenter_activate()
        lead = self.env["crm.lead"].with_user(self.superuser).create(
            {
                "name": "Lifecycle dial target",
                "team_id": campaign.id,
            }
        )
        # Call reservation creation belongs to the telephony service, not
        # to the operational Call Center Super User's general ORM ACL.
        call = self.env["codestra.vicidial.call"].sudo().create(
            {
                "name": "Allowed active campaign call",
                "uniqueid": "campaign-active-call-test",
                "crm_lead_id": lead.id,
                "duration_seconds": 0,
                "billable_seconds": 0,
            }
        )
        self.assertTrue(call)
        campaign.with_user(self.superuser).action_callcenter_pause(
            "Temporary stop"
        )
        with self.assertRaises(AccessError):
            self.env["codestra.vicidial.call"].sudo().create(
                {
                    "name": "Blocked paused campaign call",
                    "uniqueid": "campaign-paused-call-test",
                    "crm_lead_id": lead.id,
                    "duration_seconds": 0,
                    "billable_seconds": 0,
                }
            )

    def test_closed_campaign_duplicate_starts_clean_draft(self):
        campaign = self._campaign()
        self._assign(campaign, self.agent_a)
        campaign.with_user(self.superuser).action_callcenter_ready()
        campaign.with_user(self.superuser).action_callcenter_activate()
        campaign.with_user(self.superuser).action_callcenter_close("Completed")
        duplicate = campaign.with_user(
            self.superuser
        ).action_duplicate_callcenter_campaign("HINS-2026-B")
        self.assertTrue(duplicate.active)
        self.assertEqual(duplicate.campaign_state, "draft")
        self.assertEqual(duplicate.campaign_code, "HINS-2026-B")
        self.assertFalse(duplicate.user_id)
        self.assertFalse(duplicate.campaign_assignment_ids)
