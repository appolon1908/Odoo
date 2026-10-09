# Spanish-market CRM staging and GoDaddy DNS handoff

This mission covers Spain, Dominican Republic and Mexico CRM-ready exports. Do not place contact records into GitHub. The originals remain on the laptop.

## Observations

- Odoo 20 staging runs `callcenter_crm 20.0.1.5.0`.
- The campaign import wizard accepts `contact_name,phone,email,external_id,company,source,notes`; CSVs exported by Leads Workstation use a different schema and need validated mapping.
- Spain's CRM-ready export is approximately 10 MB, Dominican Republic approximately 0.9 MB, Mexico approximately 20 KB. Earlier inventory describes 32,149, 2,422 and 49 deduplicated records respectively, not independently certified for import.
- The datasets contain personal contact information. Verification or consent to marketing is not established from column presence. Keep raw data in private staging, run duplicate/format review, and prohibit live SMS/calls/email/WhatsApp.
- Odoo import is limited to Call Center Super User and active/paused campaign. Confirm an authorized market-specific campaign, data-protection basis, and reviewed import batch before performing a write.

## DNS release preflight

Authoritative DNS for `codestra.co` uses GoDaddy `ns25.domaincontrol.com` and `ns26.domaincontrol.com`. A record for `crm.codestra.co` is missing. The app server is known on LAN as `10.0.0.218`; Caddy was inactive and no publicly accessible TLS ingress for this subdomain has been certified.

**Do not assume** the apex public IP also belongs to the CRM ingress or point GoDaddy records at an RFC1918 LAN IP. Before editing GoDaddy, identify and validate the externally routed reverse proxy and its public IPv4, configure Caddy/Kong with HTTPS and protected routes, then add the `crm` A record targeting that verified public ingress. Verify authoritative DNS, certificate hostname, browser authentication and that private endpoints are not exposed.

Deployment GO=NO until these prerequisites and positive authenticated Odoo tests pass.