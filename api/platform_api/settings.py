"""Django settings for the Frappe/ERPNext management platform API."""
import os
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'insecure-dev-key')
DEBUG = os.environ.get('DJANGO_DEBUG', '0') == '1'

ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get('DJANGO_ALLOWED_HOSTS', '*').split(',')
    if host.strip()
] or ['*']

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.postgres',
    'rest_framework',
    'accounts',
    'benches',
    'sites',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'platform_api.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'platform_api.wsgi.application'

DATABASES = {
    'default': dj_database_url.parse(
        os.environ.get(
            'DATABASE_URL',
            'postgres://frappe_platform:frappe_platform@postgres:5432/frappe_platform',
        )
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
}

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': __import__('datetime').timedelta(hours=8),
    'REFRESH_TOKEN_LIFETIME': __import__('datetime').timedelta(days=7),
}

# --- Celery ---
CELERY_BROKER_URL = os.environ.get(
    'CELERY_BROKER_URL', 'redis://platform-redis:6379/0'
)
CELERY_RESULT_BACKEND = os.environ.get(
    'CELERY_RESULT_BACKEND', 'redis://platform-redis:6379/1'
)
CELERY_TASK_SERIALIZER = 'json'
CELERY_ACCEPT_CONTENT = ['json']
CELERY_RESULT_SERIALIZER = 'json'

# --- Platform / infrastructure settings ---
DOCKER_NETWORK_NAME = os.environ.get('DOCKER_NETWORK_NAME', 'frappe-platform-net')
BENCH_IMAGE_TAG = os.environ.get('BENCH_IMAGE_TAG', 'frappe-platform/bench:latest')
MARIADB_HOST = os.environ.get('MARIADB_HOST', 'mariadb')
MARIADB_ROOT_PASSWORD = os.environ.get('MARIADB_ROOT_PASSWORD', '')
PLATFORM_ADMIN_EMAIL = os.environ.get('ADMIN_EMAIL', 'admin@example.com')
PLATFORM_ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', '')
NGINX_CONF_DIR = os.environ.get('NGINX_CONF_DIR', '/nginx_conf')
PROXY_CONTAINER_NAME = os.environ.get('PROXY_CONTAINER_NAME', 'frappe-platform-proxy')

# Maximum number of characters of command output kept inline on a Job row
# before older lines are dropped (full output is written to a log file on
# the relevant bench's volume instead).
JOB_LOG_MAX_CHARS = 20000

# Site restore uploads (SQL dumps, attachment zips) can legitimately be
# large. Disabled rather than just raised: this is a trusted, single-admin
# tool (same trust boundary as the docker.sock mount), so there's no
# meaningful risk being defended against by capping upload size here.
DATA_UPLOAD_MAX_MEMORY_SIZE = None
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024

# Scratch space shared between `api` and `worker` (same named volume mounted
# in both) for restore uploads that need to survive past the request: the
# view writes the upload here (a fast local write regardless of size) and
# hands the Celery task just this path, instead of transferring the file
# into the bench container inline — that transfer is a slow docker
# `put_archive` call that can take far longer than a web worker's timeout
# for a multi-gigabyte dump.
UPLOAD_STAGING_DIR = os.environ.get('UPLOAD_STAGING_DIR', '/app/upload_staging')

# --- Email (invite delivery) ---
# Defaults to logging emails to the worker's console rather than sending
# them — the admin always gets the invite link back from the API response
# regardless, so this is optional polish, not a hard requirement. Set
# EMAIL_HOST (and friends) to actually send.
if os.environ.get('EMAIL_HOST'):
    EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
    EMAIL_HOST = os.environ['EMAIL_HOST']
    EMAIL_PORT = int(os.environ.get('EMAIL_PORT', '587'))
    EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
    EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
    EMAIL_USE_TLS = os.environ.get('EMAIL_USE_TLS', '1') == '1'
else:
    EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', 'noreply@frappe-platform.localhost')
