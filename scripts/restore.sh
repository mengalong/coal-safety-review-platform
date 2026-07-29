#!/bin/sh
set -eu

if [ "${1:-}" != "--confirm" ] || [ -z "${2:-}" ]; then
  echo "Usage: $0 --confirm BACKUP_DIRECTORY [ENV_FILE]" >&2
  exit 2
fi

BACKUP=$(cd "$2" && pwd)
ENV_FILE=${3:-.env.production}

compose() {
  docker compose --env-file "$ENV_FILE" -f compose.production.yaml "$@"
}

test -f "$BACKUP/database.dump"
test -f "$BACKUP/SHA256SUMS"
(cd "$BACKUP" && shasum -a 256 -c SHA256SUMS)

compose stop api worker
compose exec -T postgres pg_restore -U coal -d coal --clean --if-exists --no-owner < "$BACKUP/database.dump"
export COAL_BACKUP_PATH
COAL_BACKUP_PATH=$(dirname "$BACKUP")
compose --profile maintenance run --rm minio-client -c \
  'mc alias set target http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null && mc mirror --overwrite --remove "/backups/'"$(basename "$BACKUP")"'/objects" target/coal-review'
compose up -d api worker web
echo "Restore completed from $BACKUP"
