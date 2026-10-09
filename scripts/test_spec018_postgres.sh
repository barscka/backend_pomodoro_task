#!/usr/bin/env bash
# Imagem já disponível localmente; nenhum volume, credencial ou banco existente.
set -euo pipefail
cd "$(dirname "$0")/.."
container="spec018-postgres-test-$$"
cleanup() { docker rm -f "$container" >/dev/null 2>&1 || true; }
trap cleanup EXIT INT TERM
docker image inspect postgres:16 >/dev/null
docker run --pull=never --rm -d --name "$container" \
  --publish 127.0.0.1::5432 --tmpfs /var/lib/postgresql/data \
  -e POSTGRES_USER=spec018_test -e POSTGRES_PASSWORD=disposable-test-only \
  -e POSTGRES_DB=spec018_test postgres:16 >/dev/null
ready=false
for _ in $(seq 1 30); do
  if docker exec "$container" pg_isready -U spec018_test -d spec018_test >/dev/null 2>&1; then
    ready=true
    break
  fi
  sleep 1
done
if [ "$ready" != true ]; then echo 'PostgreSQL descartável não iniciou.' >&2; exit 1; fi
port=$(docker inspect --format '{{(index (index .NetworkSettings.Ports "5432/tcp") 0).HostPort}}' "$container")
APP_ENV=test TESTING=true TEST_POSTGRES_DB=spec018_test \
TEST_POSTGRES_USER=spec018_test TEST_POSTGRES_PASSWORD=disposable-test-only \
TEST_POSTGRES_HOST=127.0.0.1 TEST_POSTGRES_PORT="$port" \
.venv/bin/python manage.py test apps.pomodoro.test_spec_018 apps.pomodoro.test_spec_018_postgres \
  --settings=config.settings.postgres_concurrency --noinput --keepdb
