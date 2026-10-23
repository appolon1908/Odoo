# Lead Import and Queue Distribution Authority

## Scope

This implementation extends the canonical `crm.lead` record inside `codestra_cc_crm`. It does not create a parallel lead table or campaign authority.

- `cc.campaign` remains the campaign scope authority.
- `crm.lead` remains the permanent lead/customer source record.
- queue assignment and call-state mutations are system-controlled.
- assignment history and import rows are retained separately for audit.

## Import authority

Only the dedicated `codestra_cc_security.group_cc_call_center_superuser` role may create, validate, or confirm lead-import batches. The global administrator and technical administrator roles alone do not grant this authority.

The older `codestra_lead_ingestion` addon remains available for its compliance, outbox, and reconciliation history, but its manual batch creation/upload/import path is also gated by the same Call Center Super User authority. Its automatic approved-batch import cron is intentionally a no-op, so it cannot bypass manual confirmation.

CSV and XLSX files are parsed into `callcenter.lead.import.line` audit rows before any lead is created. The selected campaign comes from the batch, never the spreadsheet. Duplicate-file detection uses SHA-256 and requires an explicit override before the same file can be processed again for the same campaign.

Same-campaign duplicate checks use, in order:

1. source external ID;
2. normalized/sanitized phone;
3. normalized email only when no usable phone exists.

A matching customer in a different campaign in the source campaign's company is permitted and marks `cross_campaign_duplicate=True`. Matches in other companies never affect this indicator. The source campaign must be readable in the caller's allowed companies before lookup; validation and confirmation preflight access to every batch campaign.

Only the dedicated import authority may supply `source_batch_id` during lead creation, including through context defaults. Operational callers cannot attach leads to import audit batches.

## Lead pool

Imported leads enter the campaign pool as:

- `queue_state=available`;
- `user_id=False`;
- `cc_contact_center_record=True`.

Agent record rules expose only directly assigned CRM records, so the pool is not browseable by agents.

## Get Next Lead

`action_get_next_lead()` resolves the authenticated user's active campaign membership and requires the membership role to be `agent` or `senior_agent`.

Normal queue eligibility requires:

- the user's campaign;
- an operational campaign lifecycle (`staging_ready` in this fail-closed repository, or `active` when production activation is enabled);
- `queue_state=available`;
- active lead;
- no assigned user;
- `next_eligible_at` empty or due.

Selection is deterministic:

```sql
ORDER BY priority DESC, create_date ASC, id ASC
FOR UPDATE SKIP LOCKED
LIMIT 1
```

The lead-row lock prevents two concurrent agents from receiving the same lead. The active campaign-membership row is locked first, so two concurrent requests from the same agent cannot claim two different leads.

## Saved-lead lock

After creation, normal agent, supervisor, and service writes to governed lead data are rejected. The Call Center Super User may correct lead data. Internal queue methods require both `sudo` and an in-process capability object that cannot be supplied through normal RPC, and are limited to controlled fields:

- `user_id`;
- `queue_state`;
- `next_eligible_at`;
- `call_attempt_count`;
- `last_call_at`.

The queue capability cannot modify source data, campaign scope, workflow status, or archive state. Neither ordinary `sudo` nor a scope/transition capability bypasses the saved-source lock. Campaign transfer remains a separate Super-User-governed operation.

Manual agent-created leads are assigned to their creator. Supervisors may select an active agent or senior agent in their campaign before the first save; without an explicit assignee, the lead enters the unassigned available pool. Supervisors cannot become lead owners or reassign saved leads.

The saved call-center classification cannot be cleared, and source-list corrections must remain nonblank. Queue release checks read/write access and governed campaign scope for the complete selection before any elevated mutation, so mixed authorized/unauthorized selections leave every lead and assignment history unchanged.

## One working lead

An agent cannot receive a second normal lead while another lead is `assigned` to that agent in the same campaign. Callback, completed, and blocked states do not participate in the normal-new-lead selector.

## Audit

`callcenter.lead.assignment` records assignment source, actor, agent, campaign, assigned time, release time, and reason. The record is append-only except for the one-time release finalization performed by the internal queue service.

An explicit supervisor assignment at creation records `manual_supervisor_creation` with that supervisor as the actor and the selected agent as the owner. Creating an unassigned pool lead creates no assignment-history entry until assignment occurs.

Import batches are retained and cannot be deleted.
