#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

: "${CALLCENTER_UAT_CONFIRM:?Set CALLCENTER_UAT_CONFIRM=YES to run staging UAT}"
[[ "$CALLCENTER_UAT_CONFIRM" == "YES" ]] || {
  printf 'ERROR=UAT_CONFIRMATION_REQUIRED\n' >&2
  exit 1
}

ENV_FILE="${ODOO20_ENV_FILE:-.env.server3}"
COMPOSE_FILE="${ODOO20_COMPOSE_FILE:-deploy/compose/compose.server3.odoo20.yaml}"
DATABASE="${ODOO20_UAT_DATABASE:-codestra_odoo20_staging}"

[[ -f "$ENV_FILE" ]] || {
  printf 'ERROR=MISSING_ENV_FILE:%s\n' "$ENV_FILE" >&2
  exit 1
}

docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" config >/dev/null
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps

printf '==> Running isolated SPEC-1 staging UAT against %s\n' "$DATABASE"
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" run --rm -T odoo \
  odoo shell --database="$DATABASE" --no-http < scripts/callcenter_phase1_uat.py
