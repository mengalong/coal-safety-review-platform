#!/bin/sh
set -eu

if [ "${1:-}" != "--confirm" ] || [ -z "${2:-}" ]; then
  echo "Usage: $0 --confirm BACKUP_DIRECTORY [ENV_FILE]" >&2
  exit 2
fi

SOURCE=$2
ENV_FILE=${3:-.env.production}
DECRYPTED=

cleanup() {
  if [ -n "$DECRYPTED" ]; then
    rm -rf "$DECRYPTED"
  fi
}
trap cleanup EXIT INT TERM

case "$SOURCE" in
  *.tar.gz.age)
    command -v age >/dev/null 2>&1 || { echo "age is required for encrypted backups" >&2; exit 1; }
    test -r "${COAL_BACKUP_AGE_IDENTITY_FILE:-}" || {
      echo "COAL_BACKUP_AGE_IDENTITY_FILE is required for encrypted restore" >&2
      exit 1
    }
    DECRYPTED=$(mktemp -d)
    age -d -i "$COAL_BACKUP_AGE_IDENTITY_FILE" "$SOURCE" | tar -C "$DECRYPTED" -xzf -
    BACKUP=$(find "$DECRYPTED" -mindepth 1 -maxdepth 1 -type d | head -1)
    ;;
  *) BACKUP=$(cd "$SOURCE" && pwd) ;;
esac

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
