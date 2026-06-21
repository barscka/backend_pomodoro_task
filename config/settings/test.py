import os

from .base import *
from .database import build_database_config


APP_ENV = "test"
DEBUG = False
SECRET_KEY = "django-test-key-not-for-production"
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
DATABASES = {
    "default": build_database_config(
        app_env=APP_ENV,
        environ=os.environ,
        base_dir=BASE_DIR,
    )
}
