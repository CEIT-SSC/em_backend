#!/usr/bin/env sh
set -e

# Wait for the database to be ready
until pg_isready -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER"; do
  echo "⏳  waiting for postgres…"
  sleep 2
done

echo "✅  database is up"

python manage.py migrate --noinput
python manage.py collectstatic --noinput

exec "$@"

