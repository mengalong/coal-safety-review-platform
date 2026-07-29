#!/bin/sh
set -eu

ENV_FILE=${1:-.env.production}
BACKUP_ROOT=${2:-./backups}
STAMP=${3:-$(date -u +%Y%m%dT%H%M%SZ)}
TARGET="$BACKUP_ROOT/$STAMP"
TEMP="$TARGET.tmp"

compose() {
  docker compose --env-file "$ENV_FILE" -f compose.production.yaml "$@"
}

mkdir -p "$TEMP/objects"
cleanup() { rm -rf "$TEMP"; }
trap cleanup EXIT INT TERM

compose exec -T postgres pg_dump -U coal -d coal --format=custom --no-owner > "$TEMP/database.dump"
export COAL_BACKUP_PATH="$BACKUP_ROOT"
compose --profile maintenance run --rm minio-client -c \
  'mc alias set source http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null && mc mirror --overwrite source/coal-review "/backups/'"$STAMP"'.tmp/objects"'

(cd "$TEMP" && find . -type f -print0 | sort -z | xargs -0 shasum -a 256 > SHA256SUMS)
mv "$TEMP" "$TARGET"
trap - EXIT INT TERM

if [ -n "${COAL_BACKUP_AGE_RECIPIENT:-}" ]; then
  command -v age >/dev/null 2>&1 || { echo "age is required for encrypted backups" >&2; exit 1; }
  tar -C "$BACKUP_ROOT" -czf - "$STAMP" | age -r "$COAL_BACKUP_AGE_RECIPIENT" -o "$TARGET.tar.gz.age"
  rm -rf "$TARGET"
  echo "Encrypted backup created: $TARGET.tar.gz.age"
else
  echo "Backup created: $TARGET"
  echo "Warning: configure COAL_BACKUP_AGE_RECIPIENT or store this directory on an encrypted volume." >&2
fi
