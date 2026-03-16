#!/usr/bin/env bash
set -e

python manage.py migrate
# Retention cleanup is intentionally limited to low-risk transient records in V1.
python manage.py cleanup_retention || true
gunicorn whisper.wsgi:application
