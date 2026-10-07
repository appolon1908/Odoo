from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError


class CrmTeam(models.Model):
    _inherit = "crm.team"

    is_callcenter_campaign = fields.Boolean(
        string="Call Center Campaign",
        default=False,
        index=True,
        tracking=True,
    )
    campaign_code = fields.Char(
        string="Campaign Code",
        index=True,
        copy=False,
        tracking=True,
    )
    primary_supervisor_id = fields.Many2one(
        "res.users",
        string="Primary Supervisor",
        domain=lambda self: [
            ("share", "=", False),
            (
                "all_group_ids",
                "in",
                self.env.ref("callcenter.group_callcenter_supervisor").id,
            ),
        ],
        ondelete="restrict",
        tracking=True,
    )
    backup_supervisor_ids = fields.Many2many(
        "res.users",
        "callcenter_campaign_backup_supervisor_rel",
        "campaign_id",
        "user_id",
        string="Backup Supervisors",
        domain=lambda self: [
            ("share", "=", False),
            (
                "all_group_ids",
                "in",
                self.env.ref("callcenter.group_callcenter_supervisor").id,
            ),
        ],
        tracking=True,
    )
    campaign_status = fields.Selection(
        [
            ("draft", "Draft"),
            ("active", "Active"),
            ("paused", "Paused"),
            ("closed", "Closed"),
        ],
        string="Campaign Status",
        default="draft",
        required=True,
        index=True,
        tracking=True,
    )
    campaign_start_date = fields.Date(string="Start Date", tracking=True)
    campaign_end_date = fields.Date(string="End Date", tracking=True)
    callcenter_assignment_ids = fields.One2many(
        "callcenter.campaign.assignment",
        "campaign_id",
        string="Assignment History",
        readonly=True,
    )

    _campaign_code_unique = models.Constraint(
        "UNIQUE(campaign_code)",
        "Campaign code must be unique.",
    )

    @api.constrains(
        "is_callcenter_campaign",
        "campaign_code",
        "primary_supervisor_id",
        "backup_supervisor_ids",
        "campaign_start_date",
        "campaign_end_date",
    )
    def _check_callcenter_campaign_configuration(self):
        supervisor_group = self.env.ref("callcenter.group_callcenter_supervisor")
        for campaign in self:
            if campaign.is_callcenter_campaign and not campaign.campaign_code:
                raise ValidationError(
                    _("A call-center campaign requires a campaign code.")
                )
            if (
                campaign.primary_supervisor_id
                and supervisor_group
                not in campaign.primary_supervisor_id.sudo().all_group_ids
            ):
                raise ValidationError(
                    _("The primary supervisor must have the Call Center Supervisor role.")
                )
            invalid_backups = campaign.backup_supervisor_ids.filtered(
                lambda user: supervisor_group not in user.sudo().all_group_ids
            )
            if invalid_backups:
                raise ValidationError(
                    _("Every backup supervisor must have the Call Center Supervisor role.")
                )
            if (
                campaign.primary_supervisor_id
                and campaign.primary_supervisor_id in campaign.backup_supervisor_ids
            ):
                raise ValidationError(
                    _("The primary supervisor cannot also be a backup supervisor.")
                )
            if (
                campaign.campaign_end_date
                and campaign.campaign_start_date
                and campaign.campaign_end_date < campaign.campaign_start_date
            ):
                raise ValidationError(
                    _("Campaign end date cannot precede the start date.")
                )

    def _callcenter_can_manage_hierarchy(self):
        return (
            self.env.is_superuser()
            or self.env.user.has_group("base.group_system")
            or self.env.user.has_group("callcenter.group_callcenter_superuser")
        )

    @api.model_create_multi
    def create(self, vals_list):
        managed_fields = {
            "is_callcenter_campaign",
            "campaign_code",
            "primary_supervisor_id",
            "backup_supervisor_ids",
            "campaign_status",
            "campaign_start_date",
            "campaign_end_date",
        }
        if any(managed_fields.intersection(vals) for vals in vals_list):
            if not self._callcenter_can_manage_hierarchy():
                raise AccessError(
                    _(
                        "Only the Call Center Super User or a technical administrator "
                        "may manage campaign hierarchy."
                    )
                )
        campaigns = super().create(vals_list)
        for campaign in campaigns.filtered("is_callcenter_campaign"):
            campaign._sync_callcenter_supervisor_assignments()
        if campaigns:
            self.env["ir.access"]._clear_caches()
        return campaigns

    def write(self, vals):
        managed_fields = {
            "is_callcenter_campaign",
            "campaign_code",
            "primary_supervisor_id",
            "backup_supervisor_ids",
            "campaign_status",
            "campaign_start_date",
            "campaign_end_date",
        }
        hierarchy_change = bool(managed_fields.intersection(vals))
        if hierarchy_change and not self._callcenter_can_manage_hierarchy():
            raise AccessError(
                _(
                    "Only the Call Center Super User or a technical administrator "
                    "may manage campaign hierarchy."
                )
            )
        result = super().write(vals)
        if hierarchy_change and not self.env.context.get(
            "skip_callcenter_assignment_sync"
        ):
            for campaign in self:
                campaign._sync_callcenter_supervisor_assignments()
            self.env["ir.access"]._clear_caches()
        return result

    def _sync_callcenter_supervisor_assignments(self):
        Assignment = self.env["callcenter.campaign.assignment"].sudo()
        now = fields.Datetime.now()

        for campaign in self:
            desired = {}
            if campaign.is_callcenter_campaign:
                if campaign.primary_supervisor_id:
                    desired[campaign.primary_supervisor_id.id] = True
                for supervisor in campaign.backup_supervisor_ids:
                    desired[supervisor.id] = False

            active = Assignment.search(
                [
                    ("campaign_id", "=", campaign.id),
                    ("role", "=", "supervisor"),
                    ("active", "=", True),
                ]
            )

            for assignment in active:
                expected_primary = desired.get(assignment.user_id.id)
                if (
                    expected_primary is None
                    or expected_primary != assignment.is_primary
                ):
                    assignment.action_close(now)

            for user_id, is_primary in desired.items():
                exists = Assignment.search_count(
                    [
                        ("campaign_id", "=", campaign.id),
                        ("user_id", "=", user_id),
                        ("role", "=", "supervisor"),
                        ("is_primary", "=", is_primary),
                        ("active", "=", True),
                    ]
                )
                if not exists:
                    Assignment.create(
                        {
                            "campaign_id": campaign.id,
                            "user_id": user_id,
                            "role": "supervisor",
                            "is_primary": is_primary,
                            "date_from": now,
                            "assigned_by_id": self.env.user.id,
                        }
                    )
