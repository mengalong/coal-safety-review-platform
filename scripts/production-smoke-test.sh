#!/bin/sh
set -eu

ENV_FILE=${1:-.env.production}
HTTPS_PORT=$(sed -n 's/^COAL_PUBLIC_HTTPS_PORT=//p' "$ENV_FILE")
HTTPS_PORT=${HTTPS_PORT:-8443}

compose() {
  docker compose --env-file "$ENV_FILE" -f compose.production.yaml "$@"
}

compose ps --status running
READY=$(curl -kfsS "https://127.0.0.1:$HTTPS_PORT/api/v1/readyz")
echo "$READY" | grep -q '"status":"ready"'
HEADERS=$(curl -kfsSI "https://127.0.0.1:$HTTPS_PORT/")
echo "$HEADERS" | grep -qi '^strict-transport-security:'
echo "$HEADERS" | grep -qi '^content-security-policy:'
compose exec -T postgres pg_isready -U coal -d coal
compose exec -T redis sh -c 'redis-cli -a "$COAL_REDIS_PASSWORD" ping' | grep -q PONG
compose exec -T worker celery -A coal_platform.worker:celery_app inspect ping --timeout 10 | grep -q pong
compose --profile maintenance run --rm minio-client -c \
  'mc alias set target http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null && mc stat target/coal-review >/dev/null'
echo "Production smoke test passed"
