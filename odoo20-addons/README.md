# Odoo 20 Community Addons

This directory is the Odoo 20-only addon root for Codestra.

- Odoo 19 modules remain under `custom-addons/` and retain their existing Odoo 19 validation policy.
- Odoo 20 modules live here and are tested only against the digest-pinned Odoo 20 runtime.
- Server 3 mounts this directory read-only as `/mnt/extra-addons`.

Do not copy an Odoo 19 module into this tree and mark it compatible without an Odoo 20 install/update/test pass.
