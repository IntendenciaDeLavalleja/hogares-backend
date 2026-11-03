#!/bin/sh
set -eu

if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
  attempt=1
  until flask db upgrade; do
    if [ "$attempt" -ge 20 ]; then
      echo "Database migration failed after $attempt attempts" >&2
      exit 1
    fi
    attempt=$((attempt + 1))
    sleep 3
  done
fi

exec gunicorn -c /app/gunicorn.conf.py "wsgi:app"
