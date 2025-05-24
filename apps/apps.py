# apps/pomodoro/apps.py
from django.apps import AppConfig

class PomodoroConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'pomodoro'  # Mude para apenas 'pomodoro' (sem 'apps.')