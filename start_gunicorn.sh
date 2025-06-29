#!/bin/bash
cd /home/barscka/workspace/fullstack/pomodoro_task/backend_pomodoro_task
source .venv/bin/activate
exec .venv/bin/gunicorn config.wsgi:application \
    --config gunicorn_conf.py \
    --env DJANGO_SETTINGS_MODULE=config.settings.local
