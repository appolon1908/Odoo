# Odoo 20 Community Addons

This directory is the Odoo 20-only addon root for Codestra.

- `callcenter_crm` is the authoritative SPEC-1 Phase-1 call-center CRM module.
- Odoo 19 modules remain under `custom-addons/` and are not mounted into the Odoo 20 runtime.
- Odoo 20 modules are certified only against the digest-pinned Odoo 20 Community runtime.
- Server 3 mounts this directory read-only as `/mnt/extra-addons`.

`callcenter_crm` is the only call-center authority in the Odoo 20 addon root. The earlier `callcenter` prototype has been removed to prevent competing campaign/security models.
