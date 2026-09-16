import os

from django.core.exceptions import ImproperlyConfigured

from .base import *

APP_ENV = 'test'
DEBUG = False
SECRET_KEY = 'django-test-key-not-for-production'

name = os.getenv('TEST_POSTGRES_DB', '')
if os.getenv('TESTING') != 'true' or not name.endswith('_test'):
    raise ImproperlyConfigured('PostgreSQL de teste exige TESTING=true e banco com sufixo _test.')

DATABASES = {'default': {
    'ENGINE': 'django.db.backends.postgresql',
    'NAME': name,
    'USER': os.environ['TEST_POSTGRES_USER'],
    'PASSWORD': os.environ['TEST_POSTGRES_PASSWORD'],
    'HOST': os.getenv('TEST_POSTGRES_HOST', '127.0.0.1'),
    'PORT': os.getenv('TEST_POSTGRES_PORT', '5432'),
    'TEST': {'NAME': name},
}}
