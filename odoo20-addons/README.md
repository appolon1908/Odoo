# Odoo 20 Community Addons

This directory is the Odoo 20-only addon root for Codestra.

- `callcenter_crm` is the authoritative SPEC-1 Phase-1 call-center CRM module.
- Odoo 19 modules remain under `custom-addons/` and are not mounted into the Odoo 20 runtime.
- Odoo 20 modules are certified only against the digest-pinned Odoo 20 Community runtime.
- Server 3 mounts this directory read-only as `/mnt/extra-addons`.

The earlier `callcenter` prototype is superseded by `callcenter_crm` and will be removed after the replacement passes the Odoo 20 certification gate.
