# SPEC-1 — Call Center CRM hierarchy for Odoo 20 Community

This addon implements the organizational hierarchy:

Call Center Super User → Campaign → Supervisor(s) → Agent → CRM Leads / Activities.

Key design rules:

- A call-center campaign extends `crm.team`; it is not a parallel CRM hierarchy.
- `callcenter.group_callcenter_superuser` is operational authority and does not imply `base.group_system`.
- Every agent has one primary active campaign in the MVP.
- Primary and backup supervisor roles are campaign-specific.
- `callcenter.campaign.assignment` preserves dated agent and supervisor history.
- Agent transfers and supervisor changes close prior active assignments instead of deleting them.
- CRM lead visibility is campaign-scoped through record rules.
- Technical administrators retain technical override authority but remain distinct from the operational super user.

The addon is intentionally standalone from the repository's Odoo 19 call-center modules so it can be validated against Odoo 20 Community before any migration/convergence work.
