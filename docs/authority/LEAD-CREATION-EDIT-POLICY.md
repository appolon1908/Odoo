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
- supervisor creation: an explicit assignee must be an active agent or senior
  agent in the same campaign; a missing or false assignee creates an unassigned
  `available` pool lead, never a supervisor-owned lead;
- creator/date: native Odoo `create_uid` and `create_date`;
- provenance: missing manual source keys default to
  `manual:odoo:user:<uid>`.

Agents cannot submit another campaign or another assigned user. The model
rejects those attempts before persistence.

## Post-save lock

The source fields of every saved call-center lead
(`cc_contact_center_record=True`) are immutable for all callers except the
Call Center Super User. The guard is in
`crm.lead.write()`, so it applies to forms, list editing, imports, RPC/API,
automations, and bulk writes.

Agents, supervisors, global contact-center administrators, integration-service
identities, and technical administrators receive `AccessError` unless they also
hold the dedicated Call Center Super User role.

The Call Center Super User may correct or archive a lead. Campaign changes keep
the canonical/legacy campaign and business-unit fields synchronized and remain
subject to cross-model consistency constraints.

Saved call-center classification cannot be removed, even by the Super User or
an elevated process. Source-list corrections must remain nonblank; the provenance
constraint also runs when only the source-list field changes.

## Delete and archive

Call-center leads remain non-deletable for every role. Archive is a write
(`active=False`) and therefore is denied to agents/supervisors and permitted to
the Call Center Super User.

## Queue and call history

The lead is the original source record. Call attempts, activities, dispositions,
and later call outcomes must be stored as separate history/operation records.
The queue's private in-process capability is the only system exception. It
requires `sudo` and permits only `user_id`, `queue_state`, `next_eligible_at`,
`call_attempt_count`, and `last_call_at`. It cannot change source fields, campaign
ownership, workflow status, or archive state. Assignment still requires an active
agent or senior-agent membership in the lead's campaign. Public context values,
ordinary `sudo`, and scope/transition capabilities do not bypass this lock.

This reconciles the creation policy with the queue assignment invariant: a
supervisor creates pool work or selects an eligible agent before the first save;
afterward only the dedicated Super User or the bounded queue operation can change
assignment. Call history remains in separate operation records.
