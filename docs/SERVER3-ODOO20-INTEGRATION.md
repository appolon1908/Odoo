# Server 3 — Odoo 20 integration

Server 3 is the Codestra CRM application host at `10.0.0.218`. The Ubuntu development desktop at `10.0.0.217` remains the primary development workstation.

## Source-of-truth flow

- GitHub repository: `appolon1908/Odoo`
- Development workstation checkout: `/srv/codestra/apps/Odoo`
- Server 3 deploy checkout: `/srv/codestra/apps/Odoo`
- Integration branch: `development`
- Production activation remains disabled until an exact reviewed commit is promoted through the normal PR gates.

Server 3 must consume reviewed Git commits. Do not edit application source inside running containers.

## Odoo 20 runtime

The Server 3 runtime uses reviewed digest-pinned Odoo 20 and PostgreSQL 16 images. The existing `custom-addons/` tree was originally certified for Odoo 19. It is mounted read-only into Odoo 20 for migration and compatibility testing, but modules must not be installed or upgraded automatically until their Odoo 20 compatibility tests pass.

Create the untracked secret file:

```bash
cd /srv/codestra/apps/Odoo
umask 077
printf 'POSTGRES_PASSWORD=%s\n' "$(openssl rand -hex 32)" > .env.server3
```

Initialize the staging database once before the first start:

```bash
set -a; . ./.env.server3; set +a
docker run --rm --network compose_default \\
  -v compose_odoo20-data:/var/lib/odoo \\
  -v /srv/codestra/apps/Odoo/custom-addons:/mnt/extra-addons:ro \\
  --entrypoint odoo \\
  odoo@sha256:cdd83e8359b3e8c357895d476396c05021fed9975bf420f353bab25fcaed1533 \\
  db --db_host=db --db_port=5432 --db_user=odoo --db_password="$POSTGRES_PASSWORD" init codestra_odoo20_staging
```

The steady-state server pins the database to `codestra_odoo20_staging` and disables Odoo's database list/manager surface.

Start the isolated stack:

```bash
docker compose --env-file .env.server3 -f deploy/compose/compose.server3.odoo20.yaml up -d
```

Inspect:

```bash
docker compose --env-file .env.server3 -f deploy/compose/compose.server3.odoo20.yaml ps
curl -I http://10.0.0.218:8069
```

## Development-to-server synchronization

Development happens on `10.0.0.217`. Changes go to a feature branch and PR first. After merge to `development`, Server 3 fetches and checks out the exact reviewed SHA. The server checkout is a deployment consumer, not an authoring workstation.

Before updating Server 3:

```bash
git fetch origin
git status --short
git rev-parse HEAD
git rev-parse origin/development
```

Never overwrite a dirty server tree. Never deploy an unreviewed PR head to a production-enabled environment.

## Network boundary

Odoo is bound to `10.0.0.218:8069`, reachable on the private LAN. Do not expose port 8069 directly to the public Internet. Public ingress must remain behind the approved Caddy/Kong path when activation is authorized.
