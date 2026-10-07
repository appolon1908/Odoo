# Revised Lead Creation & Post-Save Lock Policy

## Authority

This policy is enforced by the canonical `codestra_cc_crm` extension of
`crm.lead`. The operational "Call Center Super User" maps to the dedicated
`codestra_cc_security.group_cc_call_center_superuser`. That group implies the
existing contact-center global administrator capabilities but is not granted to
Odoo's technical Administrator by default. Technical administration is therefore
not a saved-lead edit bypass.

## Creation

For active operational memberships, lead creation is server-derived.

- campaign: the creator's single active reconciled campaign membership;
- assigned user: the current user for agents and senior agents;
- supervisor creation: defaults to the current supervisor but may assign a user
  from the same campaign before the first save;
- creator/date: native Odoo `create_uid` and `create_date`;
- provenance: missing manual source keys default to
  `manual:odoo:user:<uid>`.

Agents cannot submit another campaign or another assigned user. The model
rejects those attempts before persistence.

## Post-save lock

Any saved call-center lead (`cc_contact_center_record=True`) is immutable for
all callers except the Call Center Super User. The guard is in
`crm.lead.write()`, so it applies to forms, list editing, imports, RPC/API,
automations, and bulk writes.

Agents, supervisors, global contact-center administrators, integration-service
identities, and technical administrators receive `AccessError` unless they also
hold the dedicated Call Center Super User role.

The Call Center Super User may correct or archive a lead. Campaign changes keep
the canonical/legacy campaign and business-unit fields synchronized and remain
subject to cross-model consistency constraints.

## Delete and archive

Call-center leads remain non-deletable for every role. Archive is a write
(`active=False`) and therefore is denied to agents/supervisors and permitted to
the Call Center Super User.

## Call history exception

The lead is the original source record. Call attempts, activities, dispositions,
and later call outcomes must be stored as separate history/operation records.
No context flag or automation capability bypasses the saved-lead write lock.
