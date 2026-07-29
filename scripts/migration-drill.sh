#!/bin/sh
set -eu

ENV_FILE=${1:-.env.production}
DATABASE=coal_migration_drill

compose() {
  docker compose --env-file "$ENV_FILE" -f compose.production.yaml "$@"
}

cleanup() { compose exec -T postgres dropdb -U coal --if-exists "$DATABASE" >/dev/null 2>&1 || true; }
trap cleanup EXIT INT TERM
cleanup
compose exec -T postgres createdb -U coal "$DATABASE"
compose run --rm api sh -c 'COAL_DATABASE_URL="${COAL_DATABASE_URL%/*}/coal_migration_drill" alembic upgrade head'
compose run --rm api sh -c 'COAL_DATABASE_URL="${COAL_DATABASE_URL%/*}/coal_migration_drill" alembic downgrade base'
compose run --rm api sh -c 'COAL_DATABASE_URL="${COAL_DATABASE_URL%/*}/coal_migration_drill" alembic upgrade head'
echo "Migration upgrade/downgrade drill passed"
