#!/bin/sh
set -eu

ENV_FILE=${1:-.env.production}
HTTPS_PORT=$(sed -n 's/^COAL_PUBLIC_HTTPS_PORT=//p' "$ENV_FILE")
HTTPS_PORT=${HTTPS_PORT:-8443}

compose() {
  docker compose --env-file "$ENV_FILE" -f compose.production.yaml "$@"
}

WAIT_SECONDS=${COAL_SMOKE_WAIT_SECONDS:-120}
WAITED=0
while :; do
  STATUS=$(compose ps -a)
  if echo "$STATUS" | grep -Eq 'unhealthy|Exited|Restarting'; then
    echo "$STATUS" >&2
    echo "One or more production containers failed while waiting for health" >&2
    exit 1
  fi
  if ! echo "$STATUS" | grep -Eq 'health: starting|Created'; then
    break
  fi
  if [ "$WAITED" -ge "$WAIT_SECONDS" ]; then
    echo "$STATUS" >&2
    echo "Production containers did not become healthy within ${WAIT_SECONDS}s" >&2
    exit 1
  fi
  sleep 2
  WAITED=$((WAITED + 2))
done
echo "$STATUS"
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
