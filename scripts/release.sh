#!/bin/sh
set -eu

if [ "${1:-}" != "--confirm" ]; then
  echo "Usage: $0 --confirm [ENV_FILE] [BACKUP_ROOT]" >&2
  exit 2
fi

ENV_FILE=${2:-.env.production}
BACKUP_ROOT=${3:-./backups}
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
STARTED_AT=$(date +%s)
RECORD_DIR="$BACKUP_ROOT/release-records"
RECORD="$RECORD_DIR/$STAMP.env"
UAT_RESULT="$RECORD_DIR/$STAMP-uat.json"
STATUS=failed
BACKUP_ARTIFACT=

mkdir -p "$RECORD_DIR"

write_record() {
  EXIT_CODE=$?
  FINISHED_AT=$(date +%s)
  trap - 0 INT TERM
  {
    echo "status=$STATUS"
    echo "released_at=$STAMP"
    echo "elapsed_seconds=$((FINISHED_AT - STARTED_AT))"
    echo "api_image=${API_IMAGE:-unknown}"
    echo "web_image=${WEB_IMAGE:-unknown}"
    echo "backup=${BACKUP_ARTIFACT:-pending}"
    echo "uat_mode=${COAL_UAT_MODE:-basic}"
    echo "uat_result=$UAT_RESULT"
  } > "$RECORD"
  chmod 600 "$RECORD"
  exit "$EXIT_CODE"
}
trap write_record 0 INT TERM

compose() {
  docker compose --env-file "$ENV_FILE" -f compose.production.yaml "$@"
}

API_IMAGE=$(sed -n 's/^COAL_API_IMAGE=//p' "$ENV_FILE")
WEB_IMAGE=$(sed -n 's/^COAL_WEB_IMAGE=//p' "$ENV_FILE")
case "$API_IMAGE" in *@sha256:*) ;; *) echo "COAL_API_IMAGE must use an immutable sha256 digest" >&2; exit 1 ;; esac
case "$WEB_IMAGE" in *@sha256:*) ;; *) echo "COAL_WEB_IMAGE must use an immutable sha256 digest" >&2; exit 1 ;; esac

scripts/security-baseline-check.sh "$ENV_FILE"
compose pull api worker web
scripts/migration-drill.sh "$ENV_FILE"
scripts/backup.sh "$ENV_FILE" "$BACKUP_ROOT" "$STAMP"
compose up -d --no-build postgres redis minio
compose run --rm api alembic upgrade head
compose up -d --no-build api worker web
scripts/production-smoke-test.sh "$ENV_FILE"

BACKUP_ARTIFACT="$BACKUP_ROOT/$STAMP"
if [ ! -d "$BACKUP_ARTIFACT" ]; then
  BACKUP_ARTIFACT="$BACKUP_ARTIFACT.tar.gz.age"
fi
scripts/release-uat.sh "$ENV_FILE" "$UAT_RESULT"
STATUS=passed
echo "Release completed; record: $RECORD"
