import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-only-change-me")
DEBUG = os.getenv("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = [h for h in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_htmx",
    "dashboard",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "dashboard" / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {"default": {
    "ENGINE": "django.db.backends.postgresql",
    "NAME": os.getenv("POSTGRES_DB", "b2b_bot"),
    "USER": os.getenv("POSTGRES_USER", "b2b_bot"),
    "PASSWORD": os.getenv("POSTGRES_PASSWORD", "b2b_bot"),
    "HOST": os.getenv("POSTGRES_HOST", "localhost"),
    "PORT": os.getenv("POSTGRES_PORT", "5432"),
}}

AUTH_USER_MODEL = "dashboard.Operator"
USE_TZ = True
TIME_ZONE = "UTC"
REPORTING_TIMEZONE = os.getenv("REPORTING_TIMEZONE", "America/New_York")
PIPELINE_ADAPTER_MODE = os.getenv("PIPELINE_ADAPTER_MODE", "stub")
ADAPTER_OPERATION_TIMEOUT_SECONDS = int(os.getenv("ADAPTER_OPERATION_TIMEOUT_SECONDS", "30"))
SESSION_ABSOLUTE_LIFETIME_SECONDS = int(os.getenv("SESSION_ABSOLUTE_LIFETIME_SECONDS", "43200"))
SESSION_IDLE_TIMEOUT_SECONDS = int(os.getenv("SESSION_IDLE_TIMEOUT_SECONDS", "1800"))
SESSION_COOKIE_AGE = SESSION_IDLE_TIMEOUT_SECONDS
SESSION_SAVE_EVERY_REQUEST = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", CELERY_BROKER_URL)
CELERY_TASK_DEFAULT_QUEUE = "notifications"
CELERY_BEAT_SCHEDULE = {}
