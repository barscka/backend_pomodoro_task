#!/bin/sh
set -eu

if [ "${WAIT_FOR_DATABASE:-true}" = "true" ]; then
    python - <<'PY'
import os
import time

import psycopg


timeout = int(os.getenv("WAIT_FOR_DATABASE_SECONDS", "60"))
deadline = time.monotonic() + timeout

while True:
    try:
        connection = psycopg.connect(
            dbname=os.environ["DB_NAME"],
            user=os.environ["DB_USER"],
            password=os.environ["DB_PASS"],
            host=os.environ["DB_HOST"],
            port=os.environ.get("DB_PORT", "5432"),
            connect_timeout=3,
        )
        connection.close()
        break
    except psycopg.OperationalError:
        if time.monotonic() >= deadline:
            raise SystemExit("PostgreSQL indisponível após o tempo limite.")
        time.sleep(2)
PY
fi

exec "$@"
