from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class CallCenterCampaignAssignment(models.Model):
    _name = "callcenter.campaign.assignment"
    _description = "Call Center Campaign Assignment"
    _order = "date_from desc, id desc"

    campaign_id = fields.Many2one(
        "crm.team",
        string="Campaign",
        required=True,
        index=True,
        ondelete="restrict",
        domain=[("is_callcenter_campaign", "=", True)],
    )
    user_id = fields.Many2one(
        "res.users",
        string="User",
        required=True,
        index=True,
        ondelete="restrict",
        domain=[("share", "=", False)],
    )
    role = fields.Selection(
        [
            ("agent", "Agent"),
            ("supervisor", "Supervisor"),
        ],
        required=True,
        index=True,
    )
    is_primary = fields.Boolean(
        string="Primary Assignment",
        default=False,
        index=True,
        help="For supervisors, marks the campaign's primary supervisor. "
        "For agents, the active assignment is always primary in the MVP.",
    )
    date_from = fields.Datetime(
        string="From",
        required=True,
        default=fields.Datetime.now,
        index=True,
    )
    date_to = fields.Datetime(string="To", index=True)
    active = fields.Boolean(default=True, index=True)
    assigned_by_id = fields.Many2one(
        "res.users",
        string="Assigned By",
        required=True,
        readonly=True,
        default=lambda self: self.env.user,
        ondelete="restrict",
    )

    @api.constrains("campaign_id")
    def _check_campaign_type(self):
        for assignment in self:
            if not assignment.campaign_id.is_callcenter_campaign:
                raise ValidationError(
                    _("Assignments may only target CRM teams marked as call-center campaigns.")
                )

    @api.constrains("date_from", "date_to")
    def _check_dates(self):
        for assignment in self:
            if assignment.date_to and assignment.date_to < assignment.date_from:
                raise ValidationError(_("Assignment end time cannot precede its start time."))

    @api.constrains("user_id", "campaign_id", "role", "is_primary", "active")
    def _check_active_invariants(self):
        for assignment in self.filtered("active"):
            if assignment.role == "agent":
                if not assignment.is_primary:
                    raise ValidationError(
                        _("An active agent assignment must be the agent's primary campaign.")
                    )
                duplicate = self.search_count(
                    [
                        ("id", "!=", assignment.id),
                        ("user_id", "=", assignment.user_id.id),
                        ("role", "=", "agent"),
                        ("active", "=", True),
                    ]
                )
                if duplicate:
                    raise ValidationError(
                        _("An agent may have only one active campaign in the MVP.")
                    )

            if assignment.role == "supervisor" and assignment.is_primary:
                duplicate = self.search_count(
                    [
                        ("id", "!=", assignment.id),
                        ("campaign_id", "=", assignment.campaign_id.id),
                        ("role", "=", "supervisor"),
                        ("is_primary", "=", True),
                        ("active", "=", True),
                    ]
                )
                if duplicate:
                    raise ValidationError(
                        _("A campaign may have only one active primary supervisor.")
                    )

    def action_close(self, date_to=None):
        close_at = date_to or fields.Datetime.now()
        for assignment in self.filtered("active"):
            assignment.write(
                {
                    "active": False,
                    "date_to": assignment.date_to or close_at,
                }
            )
        return True
