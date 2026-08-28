"""Base settings for the Deal Room Dashboard.

Scope note (task 1.1): this module carries only what the project skeleton needs
to import and pass ``manage.py check``. The five deployment keys of design
§3.0.3 (``PIPELINE_ADAPTER_MODE``, ``REPORTING_TIMEZONE``,
``ADAPTER_OPERATION_TIMEOUT_SECONDS``, ``SESSION_ABSOLUTE_LIFETIME_SECONDS``,
``SESSION_IDLE_TIMEOUT_SECONDS``), the session cookie backstop of §3.1, and the
Celery wiring are owned by task 1.2 and are deliberately absent here.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY", "insecure-development-key-override-in-every-deployment"
)
DEBUG = os.environ.get("DJANGO_DEBUG", "0") == "1"
ALLOWED_HOSTS = [
    host for host in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if host
]

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Required for the pg_trgm-backed substring search of design §4.7 and for
    # the JSONB/array/range helpers the audit and analytics layers use.
    "django.contrib.postgres",
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
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
    # dashboard.middleware.SessionExpiryMiddleware is added by task 4.1.
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# --- Database -------------------------------------------------------------
# PostgreSQL 16 is the ONLY supported backend (design §2.2). The schema depends
# on partial unique indexes, GENERATED ALWAYS AS ... STORED columns, plpgsql
# triggers, JSONB, timestamptz and INSERT ... ON CONFLICT. SQLite cannot serve
# any of those, so it is not offered even as a test-run convenience.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "deal_room"),
        "USER": os.environ.get("POSTGRES_USER", "deal_room_app"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# AUTH_USER_MODEL = "dashboard.Operator" is set by task 1.3, before the first
# migration that defines a model is generated.

# --- Time -----------------------------------------------------------------
# Requirement 13.11: every stored timestamp is UTC at one-second precision or
# finer. USE_TZ makes the ORM hand aware datetimes to timestamptz columns;
# TIME_ZONE fixes the connection's interpretation to UTC. Operator-facing
# rendering uses REPORTING_TIMEZONE (§3.0.3, task 1.2), never this value.
TIME_ZONE = "UTC"
USE_TZ = True
USE_I18N = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
