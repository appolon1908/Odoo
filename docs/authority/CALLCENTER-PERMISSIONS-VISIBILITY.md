# Call Center Permissions and Visibility Authority

## Operational roles

The simplified external role names resolve to the canonical Codestra groups:

| Stable role ID | Canonical authority |
| --- | --- |
| `callcenter.group_callcenter_agent` | `codestra_cc_security.group_cc_campaign_agent` |
| `callcenter.group_callcenter_supervisor` | `codestra_cc_security.group_cc_campaign_supervisor` |
| `callcenter.group_callcenter_superuser` | `codestra_cc_security.group_cc_global_administrator` |

The compatibility IDs are aliases, not a second RBAC system. Supervisor capability is a superset of Agent capability in the existing operational hierarchy. The operational Call Center Super User has global call-center authority but does **not** imply `base.group_system` or the technical-administrator group.

## CRM lead visibility

Agent visibility is the intersection of:
- an active campaign membership;
- the campaign global record rule; and
- direct lead assignment: `user_id = current user`.

Unassigned leads and peer-assigned leads are therefore invisible to agents through list, kanban, search, name search, grouped reads, direct record IDs and RPC/API access.

Supervisors see all leads in `user.cc_supervised_campaign_ids`. An active supervisor membership can be primary or backup. Exactly one active primary supervisor is retained per campaign; backup supervisors do not replace the primary.

The Call Center Super User bypasses campaign scope for governed call-center records while remaining separate from Odoo technical administration.

## Assignment

Assignment is enforced in Python in addition to record rules. A target lead owner must have an active `cc.campaign.membership` in the lead campaign with role `agent` or `senior_agent`.

Agents cannot change lead ownership to another user and cannot change `team_id`. Agent-created governed CRM leads are automatically assigned to the creating agent.

Supervisors can assign or bulk-assign leads only inside campaigns they supervise. Cross-campaign changes through normal `write()` remain denied.

Only the operational Call Center Super User can use the governed `action_callcenter_transfer_campaign()` method. The method validates the target campaign, target agent, target customer profile and optional CRM team before changing campaign ownership.

## Archive and deletion

Normal physical deletion of governed call-center leads is denied. The operational Super User's CRM ACL has `perm_unlink=0`, and the model also denies ordinary `unlink()`.

- Agent: cannot archive or restore.
- Supervisor: may archive leads only in supervised campaigns; cannot restore.
- Call Center Super User: may archive and restore.
- Technical administrator: deletion is available only through the private maintenance method `_callcenter_maintenance_unlink()`, which requires `base.group_system`; it is not a normal UI/RPC operation.

## Exports and reporting

Agent and Supervisor raw CRM/customer export remains disabled in model code. Existing reporting policy keeps supervisor bulk export disabled by default.

Existing KPI record rules provide:
- Agent: own KPI snapshots.
- Supervisor: campaign scope.
- Call Center Super User: all campaign KPI evidence.

## Enforcement layers

Security relies on all three layers:

1. Odoo ACLs (`ir.model.access`);
2. record rules (`ir.rule`) for row visibility;
3. model methods/constraints for reassignment, archive, transfer, export and deletion.

UI visibility is usability only and is not an authorization boundary.
