#!/bin/sh
set -e

if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
  python /app/webadmin/manage.py migrate --noinput
  python /app/webadmin/manage.py collectstatic --noinput
fi

exec "$@"
