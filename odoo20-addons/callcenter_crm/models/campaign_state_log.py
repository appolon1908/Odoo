from odoo import api, fields, models, _
from odoo.exceptions import AccessError


class CallCenterCampaignStateLog(models.Model):
    _name = "callcenter.campaign.state.log"
    _description = "Call Center Campaign State History"
    _order = "changed_at desc, id desc"

    campaign_id = fields.Many2one("callcenter.campaign", required=True, index=True, ondelete="restrict")
    from_state = fields.Selection(
        [("draft", "Draft"), ("ready", "Ready"), ("active", "Active"),
         ("paused", "Paused"), ("closed", "Closed"), ("archived", "Archived")],
        required=True,
    )
    to_state = fields.Selection(
        [("draft", "Draft"), ("ready", "Ready"), ("active", "Active"),
         ("paused", "Paused"), ("closed", "Closed"), ("archived", "Archived")],
        required=True,
    )
    changed_by_id = fields.Many2one("res.users", required=True, readonly=True, ondelete="restrict")
    changed_at = fields.Datetime(required=True, readonly=True, default=fields.Datetime.now, index=True)
    reason = fields.Text()

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.is_superuser():
            raise AccessError(_("Campaign state audit rows are system-managed."))
        return super().create(vals_list)

    def write(self, vals):
        raise AccessError(_("Campaign state audit rows are immutable."))

    def unlink(self):
        if not self.env.is_superuser():
            raise AccessError(_("Campaign state audit rows cannot be deleted in normal operation."))
        return super().unlink()
