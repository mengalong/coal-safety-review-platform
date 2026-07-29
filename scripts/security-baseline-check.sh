#!/bin/sh
set -eu

ENV_FILE=${1:-.env.production}

test -f "$ENV_FILE" || { echo "Environment file not found: $ENV_FILE" >&2; exit 2; }
if grep -Eq 'replace-with|inject-from-secret-manager' "$ENV_FILE"; then
  echo "Production environment still contains placeholder secrets" >&2
  exit 1
fi

MODE=$(stat -f '%Lp' "$ENV_FILE" 2>/dev/null || stat -c '%a' "$ENV_FILE")
case "$MODE" in
  600|400) ;;
  *) echo "Production environment permissions must be 600 or 400, got $MODE" >&2; exit 1 ;;
esac

CERT=$(sed -n 's/^COAL_TLS_CERT_FILE=//p' "$ENV_FILE")
KEY=$(sed -n 's/^COAL_TLS_KEY_FILE=//p' "$ENV_FILE")
test -r "$CERT" && test -r "$KEY"
openssl x509 -in "$CERT" -noout -checkend 604800
openssl pkey -in "$KEY" -noout -check >/dev/null

docker compose --env-file "$ENV_FILE" -f compose.production.yaml config --quiet
docker compose --env-file "$ENV_FILE" -f compose.production.yaml config | grep -q 'no-new-privileges:true'
docker compose --env-file "$ENV_FILE" -f compose.production.yaml config | grep -q 'read_only: true'
echo "Production security baseline passed"
