"""Odoo 20 scoped API keys for the Codestra read-only CRM dashboard.

This *does not* grant access: it only adds a separate key purpose to Odoo's
ordinary user-owned key creation dialog. A code with this scope cannot satisfy
the generic 'rpc' scope (the native API-key lookup matches scope exactly).
Record rules and roles remain the authoritative access policy.
"""
from odoo import fields, models


class ResUsersApiKeysDescriptionCodestra(models.TransientModel):
    _inherit = "res.users.apikeys.description"

    scope = fields.Selection(
        selection_add=[("codestra-crm-read", "Codestra CRM Dashboard — Read Only")],
        ondelete={"codestra-crm-read": "set default"},
    )
