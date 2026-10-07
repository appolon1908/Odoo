# Native CRM Team Campaign Lifecycle

## Authority

`crm.team` is the native operational campaign surface. The existing
`cc.campaign` / `codestra_campaign_control_plane` remains the canonical
platform/runtime-control authority. A CRM team campaign can optionally link to
one canonical workspace with `cc_campaign_id`.

This prevents a second provider-control plane while making native Odoo CRM teams
the place supervisors and agents work.

## Native fields

A call-center campaign extends `crm.team` with:

- `is_callcenter_campaign`
- `campaign_code` (normalized uppercase, unique)
- `campaign_type`: outbound / inbound / blended
- `campaign_state`: draft / ready / active / paused / closed
- native `user_id`: Primary Supervisor
- `backup_supervisor_ids`
- native `member_ids`: active agents, synchronized and not edited directly
- `start_at`, `end_at`, `client_id`, `description`
- optional `cc_campaign_id`

## Assignment history

`callcenter.campaign.assignment` is the historical CRM-team assignment record.
Creating/ending it synchronizes `crm.team.member`. Direct mutation of native
call-center memberships is blocked.

One user may have only one active primary campaign assignment. Ending an
assignment sets `date_to`; rows cannot be deleted or reactivated.

## Lifecycle

Server-side lifecycle:

```text
Draft -> Ready -> Active <-> Paused
                    \       /
                     -> Closed -> Archived(active=False)
```

Allowed transitions:

- Draft -> Ready
- Ready -> Draft
- Ready -> Active
- Active -> Paused
- Paused -> Active
- Active -> Closed
- Paused -> Closed
- Closed -> Archived by setting native `active=False`

Campaign state cannot be written directly. Normal archive/unarchive is blocked.

Ready validation requires a unique code, Primary Supervisor, at least one active
Agent assignment, valid dates, active internal users and synchronized native CRM
membership. If `cc_campaign_id` is linked, matching active canonical
memberships are also required.

## Closed / historical behavior

Closing:

1. sets `campaign_state=closed`;
2. ends all active `callcenter.campaign.assignment` rows with `date_to`;
3. deactivates the synchronized `crm.team.member` rows;
4. revokes linked active canonical operational memberships when a canonical
   workspace is linked;
5. records an immutable state audit row.

Closed campaigns cannot receive leads, assignments, calls or reassignment.
They can be duplicated into a clean Draft with a new campaign code.

## Operational gate

New `codestra.vicidial.call` rows linked to a lead whose `crm.team` is a
call-center campaign are accepted only when:

```text
team.active == True
AND
team.campaign_state == "active"
```

This check is in the ORM model, so bypassing UI controls or knowing a lead ID
does not bypass the campaign state.

Operational users are also prevented from creating or editing campaign leads
unless the native campaign is Active. Super Users may preload leads while Draft,
Ready or Paused, but not after Closed/Archived.

## Audit

Every lifecycle transition is appended to
`callcenter.campaign.state.log` with campaign, from/to state, actor, time and
reason. Audit rows are immutable and cannot be deleted.
