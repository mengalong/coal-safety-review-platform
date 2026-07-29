#!/bin/sh
set -eu

ENV_FILE=${1:-.env.production}
OUTPUT=${2:-./artifacts/release-uat.json}
MODE=${COAL_UAT_MODE:-basic}
LOGIN_NAME=${COAL_UAT_LOGIN_NAME:-uat-admin}

test -n "${COAL_UAT_PASSWORD:-}" || {
  echo "COAL_UAT_PASSWORD is required for release and rollback acceptance" >&2
  exit 1
}

BASE_URL=${COAL_UAT_BASE_URL:-$(sed -n 's/^COAL_PUBLIC_ORIGIN=//p' "$ENV_FILE")}
test -n "$BASE_URL" || {
  echo "COAL_UAT_BASE_URL or COAL_PUBLIC_ORIGIN is required" >&2
  exit 1
}

CA_FILE=${COAL_UAT_CA_FILE:-$(sed -n 's/^COAL_TLS_CERT_FILE=//p' "$ENV_FILE")}
test -r "$CA_FILE" || {
  echo "COAL_UAT_CA_FILE or a readable COAL_TLS_CERT_FILE is required" >&2
  exit 1
}

mkdir -p "$(dirname "$OUTPUT")"
case "$MODE" in
  basic)
    scripts/uat.sh --base-url "$BASE_URL" --login-name "$LOGIN_NAME" --ca-file "$CA_FILE" \
      --confirm-write --output "$OUTPUT"
    ;;
  full)
    test "${COAL_UAT_CONFIRM_MODEL_COST:-false}" = "true" || {
      echo "COAL_UAT_CONFIRM_MODEL_COST=true is required for full release UAT" >&2
      exit 1
    }
    scripts/uat.sh --base-url "$BASE_URL" --login-name "$LOGIN_NAME" --ca-file "$CA_FILE" \
      --confirm-write --full-audit --confirm-model-cost --output "$OUTPUT"
    ;;
  *)
    echo "COAL_UAT_MODE must be basic or full" >&2
    exit 1
    ;;
esac
