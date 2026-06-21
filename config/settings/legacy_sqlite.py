import os

from .base import *
from .database import build_legacy_sqlite_config


APP_ENV = "migration"
DEBUG = False
SECRET_KEY = "django-migration-key-not-for-production"
DATABASES = {
    "default": build_legacy_sqlite_config(
        environ=os.environ,
        base_dir=BASE_DIR,
    )
}
