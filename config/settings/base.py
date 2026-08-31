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
    "dashboard.middleware.SessionExpiryMiddleware",
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
LOGIN_URL = "/sign-in/"
LOGIN_REDIRECT_URL = "/leads/"
USE_TZ = True
TIME_ZONE = "UTC"
REPORTING_TIMEZONE = os.getenv("REPORTING_TIMEZONE", "America/New_York")
PIPELINE_ADAPTER_MODE = os.getenv("PIPELINE_ADAPTER_MODE", "stub")
PIPELINE_EVENT_SECRET = os.getenv("PIPELINE_EVENT_SECRET", "")
ADAPTER_OPERATION_TIMEOUT_SECONDS = int(os.getenv("ADAPTER_OPERATION_TIMEOUT_SECONDS", "30"))
SESSION_ABSOLUTE_LIFETIME_SECONDS = int(os.getenv("SESSION_ABSOLUTE_LIFETIME_SECONDS", "43200"))
SESSION_IDLE_TIMEOUT_SECONDS = int(os.getenv("SESSION_IDLE_TIMEOUT_SECONDS", "1800"))
SESSION_COOKIE_AGE = SESSION_IDLE_TIMEOUT_SECONDS
SESSION_SAVE_EVERY_REQUEST = True

# External integrations are injected at runtime; secrets stay out of the repo.
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "")
GOOGLE_MAPS_SEARCH_PAGE_SIZE = int(os.getenv("GOOGLE_MAPS_SEARCH_PAGE_SIZE", "10"))
SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY", "")
SERPAPI_SEARCH_MAX_RESULTS = int(os.getenv("SERPAPI_SEARCH_MAX_RESULTS", "10"))
PREVIEW_HOST_PATTERN = os.getenv("PREVIEW_HOST_PATTERN", "preview.")
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
STRIPE_WEBHOOK_TOLERANCE_SECONDS = int(os.getenv("STRIPE_WEBHOOK_TOLERANCE_SECONDS", "300"))
STRIPE_CURRENCY = os.getenv("STRIPE_CURRENCY", "usd").lower()
STRIPE_INVOICE_DAYS_UNTIL_DUE = int(os.getenv("STRIPE_INVOICE_DAYS_UNTIL_DUE", "0"))
STRIPE_INVOICE_DESCRIPTION = os.getenv("STRIPE_INVOICE_DESCRIPTION", "Website Design & Digital Presence")
STRIPE_API_TIMEOUT_SECONDS = int(os.getenv("STRIPE_API_TIMEOUT_SECONDS", "20"))

# Gmail uses a refresh token in deployment so Sales Bot can obtain short-lived
# access tokens without storing any credential in the repository or database.
GMAIL_OAUTH_CLIENT_ID = os.getenv("GMAIL_OAUTH_CLIENT_ID", "")
GMAIL_OAUTH_CLIENT_SECRET = os.getenv("GMAIL_OAUTH_CLIENT_SECRET", "")
GMAIL_OAUTH_REFRESH_TOKEN = os.getenv("GMAIL_OAUTH_REFRESH_TOKEN", "")
GMAIL_OAUTH_TOKEN_TIMEOUT_SECONDS = int(os.getenv("GMAIL_OAUTH_TOKEN_TIMEOUT_SECONDS", "15"))
GMAIL_API_TIMEOUT_SECONDS = int(os.getenv("GMAIL_API_TIMEOUT_SECONDS", "30"))

# Outbound identity is injected at deploy/runtime so personal contact details are
# never committed to this public repository.
OUTREACH_SENDER_NAME = os.getenv("OUTREACH_SENDER_NAME", "Jeremiah Lafaille")
OUTREACH_PHONE = os.getenv("OUTREACH_PHONE", "")
OUTREACH_EMAIL = os.getenv("OUTREACH_EMAIL", "")
NOTIFICATION_EMAIL_FROM = os.getenv("NOTIFICATION_EMAIL_FROM", OUTREACH_EMAIL)

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", CELERY_BROKER_URL)
CELERY_TASK_DEFAULT_QUEUE = "notifications"
CELERY_BEAT_SCHEDULE = {
    "reconcile-outreach-reservations": {
        "task": "dashboard.tasks.reconcile_outreach_reservations",
        "schedule": 300.0,
    },
    "verify-last-activity-consistency": {
        "task": "dashboard.tasks.verify_last_activity_consistency",
        "schedule": 86400.0,
    },
}
