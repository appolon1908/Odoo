from odoo import api, fields, models, _
from odoo.exceptions import AccessError


class CrmTeam(models.Model):
    _inherit = "crm.team"

    cc_campaign_id = fields.Many2one(
        "callcenter.campaign", string="Call Center Campaign",
        readonly=True, index=True, ondelete="restrict",
    )

    _one_callcenter_campaign = models.UniqueIndex(
        "(cc_campaign_id) WHERE cc_campaign_id IS NOT NULL",
        "A native CRM team can belong to only one call-center campaign.",
    )

    def _cc_is_technical(self):
        return self.env.is_superuser() or self.env.user.has_group("base.group_system")

    @api.model_create_multi
    def create(self, vals_list):
        if any(vals.get("cc_campaign_id") for vals in vals_list) and not self._cc_is_technical():
            raise AccessError(_("Call-center CRM team links are system-managed."))
        return super().create(vals_list)

    def write(self, vals):
        protected = {"cc_campaign_id", "user_id", "member_ids", "crm_team_member_ids",
                     "assignment_optout", "assignment_enabled"}
        if self.filtered("cc_campaign_id") and protected.intersection(vals) and not self._cc_is_technical():
            raise AccessError(_("Call-center CRM teams are synchronized from the Call Center application."))
        if self.filtered("cc_campaign_id") and vals.get("assignment_optout") is False:
            raise AccessError(_("Native automatic assignment must remain disabled for call-center teams."))
        return super().write(vals)

    def unlink(self):
        if self.filtered("cc_campaign_id") and not self._cc_is_technical():
            raise AccessError(_("Linked call-center CRM teams cannot be deleted."))
        return super().unlink()


class CrmTeamMember(models.Model):
    _inherit = "crm.team.member"

    def _cc_is_technical(self):
        return self.env.is_superuser() or self.env.user.has_group("base.group_system")

    @api.model_create_multi
    def create(self, vals_list):
        if not self._cc_is_technical():
            team_ids = [vals.get("crm_team_id") for vals in vals_list if vals.get("crm_team_id")]
            if team_ids and self.env["crm.team"].sudo().browse(team_ids).filtered("cc_campaign_id"):
                raise AccessError(_("Call-center team membership is synchronized from campaign assignments."))
        return super().create(vals_list)

    def write(self, vals):
        if self.filtered(lambda m: m.crm_team_id.cc_campaign_id) and not self._cc_is_technical():
            raise AccessError(_("Call-center team membership is synchronized from campaign assignments."))
        return super().write(vals)

    def unlink(self):
        if self.filtered(lambda m: m.crm_team_id.cc_campaign_id) and not self._cc_is_technical():
            raise AccessError(_("Call-center team membership history cannot be deleted."))
        return super().unlink()
