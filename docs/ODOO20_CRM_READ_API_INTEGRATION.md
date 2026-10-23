# Codestra Odoo 20 CRM Read API

This module has a purpose-specific, Odoo-native API-key scope: `codestra-crm-read`. It must **never** accept or require `rpc` credentials for the Codestra Django dashboard. The key authenticates a particular Odoo user and grants only access to three GET endpoints; Odoo access control and record rules still apply.

## Staging credential setup

An authorized Odoo account owner opens the Odoo 20 API key creation interface, selects **Codestra CRM Dashboard — Read Only**, and generates a short-lived key. Store it only in the deployment secret store or a protected Django backend environment secret named `ODOO_API_TOKEN`; never place it in the React bundle, repository, browser storage, or deployment logs.

This scoped credential is **not valid for Odoo's normal RPC API**. For Codestra's central administrator dashboard, use a dedicated, reviewed Odoo account with the appropriate call-center role. Agent-/supervisor-specific dashboards must use per-user delegated credentials or a separately reviewed identity-binding architecture: a shared Odoo service account must **not** be treated as the browser user's Odoo identity.

The canonical endpoints are:

- `GET /callcenter/api/v1/overview`
- `GET /callcenter/api/v1/campaigns?page=1&limit=25`
- `GET /callcenter/api/v1/leads?page=1&limit=25&campaign_id=123`

All endpoints return JSON schema_version=1, enforce Odoo roles and record rules, cap pagination, and set Cache-Control: private, no-store. Anonymous users and wrong-scope bearer keys must be rejected.

## Release gates

The Codestra Django read-only proxy (backend2 PR #24) and React UI (codestra PR #55) remain separate guarded dependencies. Never use public JavaScript to call Odoo directly or expose the Odoo key. Both Codestra and Odoo identity checks must pass. Browser tests with fixtures certify UX only; full release requires real bearer-authorized GET results and distinct role isolation tests.

External calls, SMS, WhatsApp, payments and production deployment remain disabled without separate authorization.
