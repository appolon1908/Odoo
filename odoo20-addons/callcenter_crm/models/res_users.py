from odoo import Command, api, models, _
from odoo.exceptions import AccessError, ValidationError


class ResUsers(models.Model):
    _inherit = "res.users"

    def _cc_role_manager(self):
        return (
            self.env.is_superuser()
            or self.env.user.has_group("base.group_system")
            or self.env.user.has_group("callcenter_crm.group_callcenter_superuser")
        )

    def action_promote_callcenter_agent(self):
        if not self._cc_role_manager():
            raise AccessError(_("Only Call Center Super Users or technical administrators may assign operational roles."))
        group = self.env.ref("callcenter_crm.group_callcenter_agent")
        for user in self:
            if user.share:
                raise ValidationError(_("Portal/shared users cannot be promoted to call-center agents."))
            user.sudo().write({"group_ids": [Command.link(group.id)]})
        return True

    def action_promote_callcenter_supervisor(self):
        if not self._cc_role_manager():
            raise AccessError(_("Only Call Center Super Users or technical administrators may assign operational roles."))
        group = self.env.ref("callcenter_crm.group_callcenter_supervisor")
        for user in self:
            if user.share:
                raise ValidationError(_("Portal/shared users cannot be promoted to call-center supervisors."))
            user.sudo().write({"group_ids": [Command.link(group.id)]})
        return True

    def action_grant_callcenter_superuser(self):
        if not (self.env.is_superuser() or self.env.user.has_group("base.group_system")):
            raise AccessError(_("Only the Odoo Technical Administrator may grant Call Center Super User."))
        group = self.env.ref("callcenter_crm.group_callcenter_superuser")
        self.sudo().write({"group_ids": [Command.link(group.id)]})
        return True

    def write(self, vals):
        result = super().write(vals)
        if "group_ids" in vals and "callcenter.campaign" in self.env:
            self._sync_callcenter_admin_assignments()
        return result

    def _sync_callcenter_admin_assignments(self):
        Campaign = self.env["callcenter.campaign"].sudo()
        Assignment = self.env["callcenter.campaign.assignment"].sudo().with_context(active_test=False)
        cc_group = self.env.ref("callcenter_crm.group_callcenter_superuser")
        sys_group = self.env.ref("base.group_system")
        campaigns = Campaign.search([])
        now = self.env["ir.fields.converter"]._datetime_to_string if False else None
        from odoo import fields
        close_at = fields.Datetime.now()
        for user in self:
            groups = user.sudo().all_group_ids
            should_have = cc_group in groups or sys_group in groups
            current = Assignment.search([
                ("user_id", "=", user.id), ("role", "=", "admin"), ("active", "=", True)
            ])
            if should_have:
                current_campaigns = current.campaign_id
                for campaign in campaigns - current_campaigns:
                    Assignment.create({
                        "campaign_id": campaign.id, "user_id": user.id, "role": "admin",
                        "is_primary": False, "date_from": close_at, "assigned_by_id": self.env.user.id,
                    })
            elif current:
                current.action_close(close_at)
        return True
