#!/usr/bin/env bash
# Container entrypoint: install the daily cron schedule, start cron in the background,
# then run the dashboard in the foreground as PID 1. Code + state are bind-mounted at /app.
set -e
cd /app
mkdir -p logs briefings fulltext

# Install the daily schedule (crontab is bind-mounted with the code, so it updates on deploy).
if [ -f /app/crontab ]; then
  crontab /app/crontab
  echo "cron schedule installed:"; crontab -l | sed 's/^/  /'
fi
cron

echo "dashboard starting on ${DASH_HOST:-0.0.0.0}:${DASH_PORT:-8001} (writer=${BRIEFING_WRITER:-mindrouter})"
exec python3 /app/dashboard.py
