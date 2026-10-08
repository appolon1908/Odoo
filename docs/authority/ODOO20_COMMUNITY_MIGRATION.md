# Codestra Odoo 20 Community migration — staging gate (October 8, 2026)

**Scope:** `appolon1908/Odoo`, Odoo 20 Community, PostgreSQL 16, staging first. This branch does **not** promote production, install 81 unported addons, or merge PR #154/#157.

## Verified starting point

- On Codestra `server-3`, `compose-odoo-1` runs Odoo `20.0-20260926`, with `compose-db-1` running PostgreSQL `16.15`.
- Staging DB `codestra_odoo20_staging` has Odoo `base` 20.0; canonical Codestra modules are currently **uninstallable**.
- The live Odoo container has an **empty** `PASSWORD` environment variable, despite the dedicated PostgreSQL service having a working configured credential. This must be repaired by an authorized operator in the staging deployment, **without logging the secret**. Set `POSTGRES_PASSWORD` in secure Compose environment before re-creating **only** the staging Odoo service. Fail closed if missing.
- The checked host source tree has **81** custom addon manifests, all declaring `19.0` at assessment. Version bump alone is not a port.
- Pre-change backup (DB custom format and filestore, restore-list validated): `/srv/codestra/backups/odoo20-migration/20261008T174712Z`. Do not treat a filesystem path alone as proof of an off-host backup.

## Required sequence

1. **Keep Odoo 19 as a distinct certification line** until the new system passes; do not replace the existing Odoo 19 CI gate merely because Odoo 20 exists. Preserve source and merge-result SHA checks.
2. Recover the Odoo 20 staging DB connection. Verify `HOST`, `USER`, and **nonempty** `PASSWORD` are present inside the Odoo container; do not print values; validate a read-only `SELECT 1`.
3. Back up each source Odoo 19 database **and** its filestore; record checksums, verify `pg_restore -l`, restore to an isolated database, and neutralize email, SMS, scheduled jobs, phone calls and payment integrations.
4. Upgrade standard Odoo tables in an isolated test DB using a supported Odoo 19→20 Community database migration procedure. Do not point Odoo 20 directly at unconverted 19 data. Preserve record IDs, foreign keys, `ir_model_data` XML IDs, attachments, history and audit tables.
5. Port **every installed and required addon** through actual 20.0 module code, views, ACLs, record rules, assets, SQL reporting models and Odoo 20 runtime tests. Use `python3 scripts/odoo20/inventory.py --require-ready` only as a **manifest precondition**, never as certification.
6. Resolve PR #154 saved-lead immutability with PR #157 lead allocation before migration. Saved lead writes remain denied to agents/supervisors/services; queue assignment history is separate. Explicit super-user controlled amendments must be audited. Negative tests must prove cross-campaign invisibility and no service bypass.
7. Database verification: `PGDATABASE=codestra_odoo20_staging` plus standard `PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD` then `python3 scripts/odoo20/db_preflight.py`. This query is read only and **fails** on uninstallable Codestra modules.
8. Run Odoo 20 module installation on a fresh certification DB, then a restored **migrated** test copy. Compare campaign/agent/membership/lead counts, immutable lead data, duplicate detection, queue state/history, consent/suppression records, reporting SQL views and broken references; test agent, supervisor, compliance, workforce and super-user permissions.
9. Obtain independent code review and exact-head Odoo 20 CI evidence, perform a full restore/rehearsal and document rollback. **GO=NO** until all gates pass and production activation is explicitly authorized.

## Isolation

`deploy/compose/compose.odoo20.certification.yaml` provisions a **separate disposable** Odoo 20/PostgreSQL stack with no published ports, a required nonempty password and separate volumes. Never mount existing production/staging PostgreSQL volumes into it. Do not run `docker compose down -v` against any shared project.

Official docs: https://www.odoo.com/documentation/20.0/administration/upgrade.html
