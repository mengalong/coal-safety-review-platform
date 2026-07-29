#!/bin/sh
set -eu

ENV_FILE=${1:-.env.production}

compose() {
  docker compose --env-file "$ENV_FILE" -f compose.production.yaml "$@"
}

compose --profile maintenance run --rm minio-client -c '
  mc alias set target http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null
  mc version enable target/coal-review
  mc ilm rule add --expire-days 7 --prefix tmp/ target/coal-review
  mc ilm rule add --noncurrent-expire-days 30 target/coal-review
  mc ilm rule ls target/coal-review
'
