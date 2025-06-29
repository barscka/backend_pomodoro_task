#!/bin/bash
cd "$PWD"
source .venv/bin/activate
exec .venv/bin/gunicorn config.wsgi:application \
    --config gunicorn_conf.py \
    --env DJANGO_SETTINGS_MODULE=config.settings.local
