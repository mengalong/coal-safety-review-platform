#!/bin/sh
set -eu

if [ "${1:-}" != "--confirm" ] || [ -z "${2:-}" ] || [ -z "${3:-}" ]; then
  echo "Usage: $0 --confirm PREVIOUS_ENV BACKUP_DIRECTORY [CURRENT_ENV]" >&2
  exit 2
fi

PREVIOUS_ENV=$2
BACKUP=$3
CURRENT_ENV=${4:-.env.production}

current_compose() {
  docker compose --env-file "$CURRENT_ENV" -f compose.production.yaml "$@"
}

previous_compose() {
  docker compose --env-file "$PREVIOUS_ENV" -f compose.production.yaml "$@"
}

scripts/security-baseline-check.sh "$PREVIOUS_ENV"
current_compose stop web api worker
previous_compose pull api worker web
scripts/restore.sh --confirm "$BACKUP" "$PREVIOUS_ENV"
previous_compose up -d --no-build api worker web
scripts/production-smoke-test.sh "$PREVIOUS_ENV"
echo "Rollback completed with $PREVIOUS_ENV and $BACKUP"
