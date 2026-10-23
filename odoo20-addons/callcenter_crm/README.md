# SPEC-1 — Odoo 20 Call Center CRM Core

This module is the Phase-1 call-center CRM authority for Odoo 20 Community.

It implements:

- separate Agent, Supervisor, and Call Center Super User roles;
- authoritative `callcenter.campaign` records linked 1:1 to native `crm.team`;
- campaign assignment and lifecycle audit history;
- native CRM team/member synchronization with `assignment_optout=True`;
- group-less Odoo 20 access restrictions for call-center teams, members, and leads;
- saved-lead lock: agents and supervisors may create, but after the first save only the Call Center Super User may edit; normal physical deletion is blocked and agent/supervisor export is denied;
- controlled CSV/XLSX campaign imports with SHA-256 file audit and duplicate checks;
- call-center queue selection using PostgreSQL `FOR UPDATE SKIP LOCKED`;
- one-current-lead-per-agent protection and lead assignment history;
- scheduled end-date auto-pause.

Phase 2 telephony, dispositions, callbacks, recordings, scripts, and QA scoring are intentionally excluded.
