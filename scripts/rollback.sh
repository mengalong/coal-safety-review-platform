#!/bin/sh
set -eu

if [ "${1:-}" != "--confirm" ] || [ -z "${2:-}" ] || [ -z "${3:-}" ]; then
  echo "Usage: $0 --confirm PREVIOUS_ENV BACKUP_DIRECTORY [CURRENT_ENV] [--local-drill]" >&2
  exit 2
fi

PREVIOUS_ENV=$2
BACKUP=$3
CURRENT_ENV=${4:-.env.production}
DRILL_MODE=${5:-production}
case "$DRILL_MODE" in
  production|--local-drill) ;;
  *) echo "fifth argument must be --local-drill when provided" >&2; exit 2 ;;
esac
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
STARTED_AT=$(date +%s)
RECORD_DIR=${COAL_RELEASE_RECORD_DIR:-$(dirname "$BACKUP")/release-records}
RECORD="$RECORD_DIR/$STAMP-rollback.env"
UAT_RESULT="$RECORD_DIR/$STAMP-rollback-uat.json"
STATUS=failed

mkdir -p "$RECORD_DIR"

write_record() {
  EXIT_CODE=$?
  FINISHED_AT=$(date +%s)
  trap - 0 INT TERM
  {
    echo "status=$STATUS"
    echo "rolled_back_at=$STAMP"
    echo "elapsed_seconds=$((FINISHED_AT - STARTED_AT))"
    echo "previous_env=$PREVIOUS_ENV"
    echo "current_env=$CURRENT_ENV"
    echo "backup=$BACKUP"
    echo "image_pull=$([ "$DRILL_MODE" = "--local-drill" ] && echo skipped-local-drill || echo immutable-digest)"
    echo "uat_mode=${COAL_UAT_MODE:-basic}"
    echo "uat_result=$UAT_RESULT"
  } > "$RECORD"
  chmod 600 "$RECORD"
  exit "$EXIT_CODE"
}
trap write_record 0 INT TERM

current_compose() {
  docker compose --env-file "$CURRENT_ENV" -f compose.production.yaml "$@"
}

previous_compose() {
  docker compose --env-file "$PREVIOUS_ENV" -f compose.production.yaml "$@"
}

scripts/security-baseline-check.sh "$PREVIOUS_ENV"
current_compose stop web api worker
if [ "$DRILL_MODE" = "--local-drill" ]; then
  echo "Local drill: using prebuilt previous-version images without registry pull" >&2
else
  previous_compose pull api worker web
fi
scripts/restore.sh --confirm "$BACKUP" "$PREVIOUS_ENV"
previous_compose up -d --no-build api worker web
scripts/production-smoke-test.sh "$PREVIOUS_ENV"
scripts/release-uat.sh "$PREVIOUS_ENV" "$UAT_RESULT"
STATUS=passed
echo "Rollback completed with $PREVIOUS_ENV and $BACKUP; record: $RECORD"
