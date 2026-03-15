#!/usr/bin/env bash
set -e

python manage.py migrate
python manage.py cleanup_retention || true
gunicorn whisper.wsgi:application
