# SPEC-1 Phase-1 Staging UAT

Run this only after the exact pull-request head passes the Odoo 20 Phase-1
certification workflow. It targets staging only and does not enable telephony,
provider effects, production, or public ingress.

## Preconditions

- clean Server 3 checkout at the exact reviewed SHA;
- `callcenter_crm` installed/upgraded on `codestra_odoo20_staging`;
- `.env.server3` present with staging database credentials;
- staging database backup completed.

## Command

```bash
cd /srv/codestra/apps/Odoo
CALLCENTER_UAT_CONFIRM=YES bash scripts/run_odoo20_callcenter_staging_uat.sh
```

The harness creates separate temporary Odoo records for one operational Super
User, two supervisors, four agents, and an ordinary Sales Manager, while using
the Odoo Technical Administrator as a separate role.

It verifies:

- Agent and Supervisor cross-campaign isolation;
- standard Sales Manager isolation from call-center leads and native teams;
- saved-lead write protection;
- export protection;
- operational Super User editing;
- native CRM assignment opt-out;
- queue assignment and no duplicate sequential allocation;
- controlled CSV import;
- duplicate-file protection.

It prints a machine-readable evidence line and then removes only its own
temporary UAT records:

```text
CALLCENTER_PHASE1_UAT={...}
CALLCENTER_PHASE1_UAT_CLEANUP=PASS
```

The evidence includes timings for lead creation/search, Get Next Lead, import
validation and import processing. True simultaneous queue selection is certified
by the module's `test_queue_concurrency.py`, which uses two open PostgreSQL
transactions and proves `FOR UPDATE SKIP LOCKED` returns distinct leads.
