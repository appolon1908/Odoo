#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ADDONS_DIR="$ROOT_DIR/odoo20-addons"
MODULE="callcenter_crm"
ODOO_IMAGE="${ODOO20_CI_IMAGE:-odoo@sha256:cdd83e8359b3e8c357895d476396c05021fed9975bf420f353bab25fcaed1533}"
POSTGRES_IMAGE="${ODOO20_POSTGRES_IMAGE:-postgres@sha256:65b16a8b326e0cfbdf33fa7e783f2a0cb352a61448616ccccfd616ef42aa0f65}"
RUN_ID="$(printf '%s' "${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-1}-$$" | tr -cd 'A-Za-z0-9_.-')"
NETWORK="codestra-odoo20-spec1-${RUN_ID}"
DB_CONTAINER="codestra-odoo20-db-${RUN_ID}"
DATABASE="codestra_spec1_ci"
DB_USER="odoo"
TEST_LOG="$(mktemp)"
UPGRADE_LOG="$(mktemp)"

cleanup() {
  docker rm -f "$DB_CONTAINER" >/dev/null 2>&1 || true
  docker network rm "$NETWORK" >/dev/null 2>&1 || true
  rm -f "$TEST_LOG" "$UPGRADE_LOG"
}
trap cleanup EXIT

for command in docker python3; do
  command -v "$command" >/dev/null 2>&1 || {
    printf 'ERROR=MISSING_COMMAND:%s\n' "$command" >&2
    exit 1
  }
done

for image in "$ODOO_IMAGE" "$POSTGRES_IMAGE"; do
  [[ "$image" == *@sha256:* ]] || {
    printf 'ERROR=IMAGE_NOT_DIGEST_PINNED:%s\n' "$image" >&2
    exit 1
  }
done

printf '==> Static Odoo 20 SPEC-1 validation\n'
python3 -m compileall -q "$ADDONS_DIR/$MODULE"
python3 - "$ADDONS_DIR/$MODULE" <<'PY'
import ast
import csv
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

root = Path(sys.argv[1])
manifest = ast.literal_eval((root / "__manifest__.py").read_text(encoding="utf-8"))
assert manifest["version"].startswith("20.0."), manifest["version"]
assert manifest["installable"] is True
for relative in manifest.get("data", []):
    path = root / relative
    assert path.is_file(), relative
    if path.suffix == ".xml":
        ET.parse(path)
for path in root.rglob("*.xml"):
    ET.parse(path)
with (root / "security" / "ir.access.csv").open(newline="", encoding="utf-8") as handle:
    rows = list(csv.DictReader(handle))
assert rows
assert set(rows[0]) == {"id", "name", "model_id", "group_id/id", "operation", "domain"}
print("ODOO20_SPEC1_STATIC=PASS")
PY

docker image inspect "$ODOO_IMAGE" >/dev/null 2>&1 || docker pull "$ODOO_IMAGE"
docker image inspect "$POSTGRES_IMAGE" >/dev/null 2>&1 || docker pull "$POSTGRES_IMAGE"

ODOO_VERSION="$(docker run --rm --entrypoint odoo "$ODOO_IMAGE" --version)"
printf 'ODOO20_VERSION=%s\n' "$ODOO_VERSION"
[[ "$ODOO_VERSION" == Odoo\ Server\ 20.0* ]] || {
  printf 'ERROR=NOT_ODOO20:%s\n' "$ODOO_VERSION" >&2
  exit 1
}

DB_PASSWORD="$(python3 - <<'PY'
import secrets
print("ci_" + secrets.token_urlsafe(24))
PY
)"

docker network create "$NETWORK" >/dev/null
docker run -d   --name "$DB_CONTAINER"   --network "$NETWORK"   --network-alias db   -e POSTGRES_USER="$DB_USER"   -e POSTGRES_PASSWORD="$DB_PASSWORD"   -e POSTGRES_DB=postgres   --health-cmd="pg_isready -U $DB_USER -d postgres"   --health-interval=2s   --health-timeout=3s   --health-retries=60   "$POSTGRES_IMAGE" >/dev/null

for _ in $(seq 1 60); do
  health="$(docker inspect --format '{{.State.Health.Status}}' "$DB_CONTAINER")"
  [[ "$health" == healthy ]] && break
  if [[ "$health" == unhealthy ]]; then
    docker logs "$DB_CONTAINER" >&2 || true
    printf 'ERROR=POSTGRES_UNHEALTHY\n' >&2
    exit 1
  fi
  sleep 2
done
[[ "$(docker inspect --format '{{.State.Health.Status}}' "$DB_CONTAINER")" == healthy ]] || {
  printf 'ERROR=POSTGRES_TIMEOUT\n' >&2
  exit 1
}

printf '==> Installing and testing %s on Odoo 20\n' "$MODULE"
if ! docker run --rm   --network "$NETWORK"   -e HOST=db -e PORT=5432 -e USER="$DB_USER" -e PASSWORD="$DB_PASSWORD"   -v "$ADDONS_DIR:/mnt/extra-addons:ro"   "$ODOO_IMAGE"   --addons-path=/usr/lib/python3/dist-packages/odoo/addons,/mnt/extra-addons   -d "$DATABASE"   --init="$MODULE"   --without-demo   --test-enable   --test-tags="/$MODULE"   --stop-after-init   --workers=0   --http-interface=127.0.0.1   --log-level=test   2>&1 | tee "$TEST_LOG"; then
  printf 'ERROR=ODOO20_SPEC1_INSTALL_OR_TEST_FAILED\n' >&2
  exit 1
fi

grep -Eq '0 failed, 0 error\(s\)' "$TEST_LOG" || {
  printf 'ERROR=ODOO20_SPEC1_SUCCESS_MARKER_MISSING\n' >&2
  exit 1
}

printf '==> Exercising module upgrade path\n'
if ! docker run --rm   --network "$NETWORK"   -e HOST=db -e PORT=5432 -e USER="$DB_USER" -e PASSWORD="$DB_PASSWORD"   -v "$ADDONS_DIR:/mnt/extra-addons:ro"   "$ODOO_IMAGE"   --addons-path=/usr/lib/python3/dist-packages/odoo/addons,/mnt/extra-addons   -d "$DATABASE"   --update="$MODULE"   --without-demo   --stop-after-init   --workers=0   --http-interface=127.0.0.1   --log-level=test   2>&1 | tee "$UPGRADE_LOG"; then
  printf 'ERROR=ODOO20_SPEC1_UPGRADE_FAILED\n' >&2
  exit 1
fi

printf 'ODOO20_SPEC1_INSTALL_TEST=PASS\n'
printf 'ODOO20_SPEC1_UPGRADE=PASS\n'
printf 'ODOO20_SPEC1_CERTIFICATION=PASS\n'
