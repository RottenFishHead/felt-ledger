#!/bin/sh
# Runs on the app machine, so migrations apply to the SQLite database on the mounted volume.
set -e
mkdir -p "${DJANGO_DATA_DIR:-.}/media"
python manage.py migrate --noinput
# Back up the database to R2 (once the keys are set): on start and every 10 minutes, but only if something
# changed and the last backup is over an hour old. The machine stops when idle, so a backup also runs on the
# next start. A failed backup is logged and never stops the app.
if [ -n "$R2_ACCESS_KEY_ID" ]; then
  (while true; do python manage.py backup_db --if-changed --min-minutes 60 || echo "Database backup failed"; sleep 600; done) &
fi
# --timeout 120: phones on slow connections can take a while to upload several photos.
exec gunicorn pokertracker.wsgi --bind 0.0.0.0:8000 --workers 2 --timeout 120 --access-logfile -
