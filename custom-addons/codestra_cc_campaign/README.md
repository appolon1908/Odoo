# Codestra Contact Center Campaign

This addon is the governed native-Odoo campaign facade.

It extends `crm.team` with the operational call-center campaign structure,
role-scoped visibility, assignment history, lifecycle transitions and audit
while preserving the existing campaign authorities:

- `cc.campaign` remains the canonical platform campaign workspace.
- `call.center.campaign` remains the reviewed physical/integration record.
- `codestra_campaign_control_plane` remains the runtime provisioning and
  activation authority.

The facade owns no direct provider transport and enables no external effects.

See `docs/authority/CALLCENTER-CAMPAIGN-LIFECYCLE.md`.
