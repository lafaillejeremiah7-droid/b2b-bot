"""Base settings for the Deal Room Dashboard.

Carries the five deployment keys of design §3.0.3 (``PIPELINE_ADAPTER_MODE``,
``REPORTING_TIMEZONE``, ``ADAPTER_OPERATION_TIMEOUT_SECONDS``,
``SESSION_ABSOLUTE_LIFETIME_SECONDS``, ``SESSION_IDLE_TIMEOUT_SECONDS``), the
session cookie backstop of §3.1, and the Celery wiring of §2.7.

Every deployment key is read from the environment with the design's default and
is **validated here, at import**, so a misconfiguration is a startup failure
rather than a behavior change discovered in production. All five stay plain
module-level names because the middleware (§3.1) and the adapter facade
(§3.14.1) read them as ``settings.X``.
"""

import os
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def _env_str(name: str, default: str) -> str:
    """Read a string setting, falling back to the design's stated default."""
    return os.environ.get(name, default)


def _env_positive_int(name: str, default: int) -> int:
    """Read a positive whole-number setting, refusing anything that is not one."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ImproperlyConfigured(
            f"{name} must be a whole number of seconds; got {raw!r}."
        ) from None
    if value <= 0:
        raise ImproperlyConfigured(
            f"{name} must be greater than zero seconds; got {value}."
        )
    return value

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

# --- The custom user model (Requirement 1.5) ------------------------------
# Set here, in the scaffolding settings, because AUTH_USER_MODEL is a *swappable
# dependency*: Django resolves it while building the first migration that
# defines a model, and every later migration referencing the user model records
# that resolution. Task 1.1's only migration creates the pg_trgm extension and
# attaches no model state, so nothing has been resolved yet and this is the last
# moment it can be set without a schema rebuild.
#
# dashboard.Operator is a lean AbstractBaseUser with a `role` field over
# Viewer | Agent | Admin (design §3.2) and deliberately no PermissionsMixin —
# the role field is the entire authorization model, so Django's groups and
# per-model permissions cannot become a second authority that
# `available_actions()` (task 4.2) does not see. django.contrib.admin, the usual
# reason to need that machinery, is not installed.
AUTH_USER_MODEL = "dashboard.Operator"

# --- Time: the STORAGE timezone -------------------------------------------
# Requirement 13.11: every stored timestamp is UTC at one-second precision or
# finer. USE_TZ makes the ORM hand aware datetimes to timestamptz columns;
# TIME_ZONE fixes the connection's interpretation to UTC.
#
# These two are the storage contract and are NOT configurable per deployment.
# REPORTING_TIMEZONE below is a different thing entirely — see the warning
# there before touching either.
TIME_ZONE = "UTC"
USE_TZ = True
USE_I18N = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# =========================================================================
# Deployment keys of design §3.0.3
# =========================================================================

# --- Pipeline_Adapter selection (Requirement 12.3) ------------------------
# Selects the implementation task 7.2 instantiates: `stub` builds
# StubPipelineAdapter and turns on the UI stub badge, `live` builds
# LivePipelineAdapter.
#
# The default is `stub` on purpose. Requirement 12.3's whole point is that the
# dashboard ships and runs end to end before the bot exists, so an unconfigured
# deployment must be the one that transmits nothing outside the dashboard.
PIPELINE_ADAPTER_MODE_STUB = "stub"
PIPELINE_ADAPTER_MODE_LIVE = "live"
PIPELINE_ADAPTER_MODES = (PIPELINE_ADAPTER_MODE_STUB, PIPELINE_ADAPTER_MODE_LIVE)

PIPELINE_ADAPTER_MODE = _env_str("PIPELINE_ADAPTER_MODE", PIPELINE_ADAPTER_MODE_STUB)

# Fail loudly, and match exactly. There is deliberately no case folding, no
# whitespace trimming, and no fallback-to-a-default on an unrecognized value:
# any of those would let a typo resolve to a working mode, and a deployment
# that believes it is in `stub` while the adapter is `live` sends real cold
# email to real prospects. An unparseable value must stop the process.
if PIPELINE_ADAPTER_MODE not in PIPELINE_ADAPTER_MODES:
    raise ImproperlyConfigured(
        f"PIPELINE_ADAPTER_MODE must be exactly one of "
        f"{' or '.join(repr(mode) for mode in PIPELINE_ADAPTER_MODES)}; got "
        f"{PIPELINE_ADAPTER_MODE!r}. The value is compared exactly — it is not "
        f"lower-cased, not trimmed, and never falls back to a default — so that "
        f"a typo cannot silently resolve to a working adapter mode."
    )

# --- REPORTING_TIMEZONE: the *reporting* timezone (Requirement 10.13) -----
# THIS IS NOT THE STORAGE TIMEZONE. The two are separate settings with separate
# jobs, and conflating them silently shifts every analytics figure by the UTC
# offset. Stated plainly:
#
#   TIME_ZONE = "UTC"   -> STORAGE. Every timestamp column holds a UTC instant
#                          (Requirement 13.11). Never derived from the value
#                          below, and never changed per deployment.
#
#   REPORTING_TIMEZONE  -> INTERPRETATION of Analytics_View date-range
#                          boundaries, and nothing else. Per §3.11.3 the
#                          Analytics_View reads the Operator's selected start
#                          date as 00:00:00 and end date as 23:59:59 *in this
#                          zone*, then converts both wall-clock boundaries to
#                          UTC instants and queries with those instants,
#                          because storage is UTC.
#
# So: no row is ever written in this zone, and no stored column is ever read as
# if it were in this zone. Changing this value moves where a report's day
# boundaries fall. It must never change what a stored timestamp means.
REPORTING_TIMEZONE = _env_str("REPORTING_TIMEZONE", "America/New_York")

try:
    ZoneInfo(REPORTING_TIMEZONE)
except (ZoneInfoNotFoundError, ValueError) as exc:
    raise ImproperlyConfigured(
        f"REPORTING_TIMEZONE must be a resolvable IANA timezone name "
        f"(e.g. 'America/New_York'); got {REPORTING_TIMEZONE!r}."
    ) from exc

# --- Adapter operation timeout (Requirement 12.8) -------------------------
# Backs the timeout-to-failure conversion in the §3.14.1 TimeoutEnforcingAdapter
# facade: an operation that does not return within this many seconds becomes
# AdapterResult(status="failure") with a timeout reason, and records no email,
# call, invoice, or Release_Authorization row. The timeout lives in the facade
# rather than in each implementation so stub and live behave identically.
ADAPTER_OPERATION_TIMEOUT_SECONDS = _env_positive_int(
    "ADAPTER_OPERATION_TIMEOUT_SECONDS", 30
)

# --- Session expiry: two independent rules (Requirements 1.4, 1.12) -------
# §3.1 is explicit that these are two separate caps and that either one alone
# ends the session:
#
#   SESSION_ABSOLUTE_LIFETIME_SECONDS  12h — measured from a `session_started_at`
#                                      written once at sign-in and never
#                                      refreshed (Requirement 1.4).
#   SESSION_IDLE_TIMEOUT_SECONDS       30m — measured from a `last_seen_at`
#                                      refreshed on every request
#                                      (Requirement 1.12).
#
# The authoritative check is server-side, in the SessionExpiryMiddleware that
# task 4.1 owns. These are the values that middleware reads; no expiry logic
# lives here.
SESSION_ABSOLUTE_LIFETIME_SECONDS = _env_positive_int(
    "SESSION_ABSOLUTE_LIFETIME_SECONDS", 43_200  # 12 hours
)
SESSION_IDLE_TIMEOUT_SECONDS = _env_positive_int(
    "SESSION_IDLE_TIMEOUT_SECONDS", 1_800  # 30 minutes
)

# An idle window wider than the absolute lifetime would make the idle rule dead
# code — the absolute cap would always fire first — which is a configuration
# mistake, not a policy choice. Refuse it rather than run with one of the two
# rules of §3.1 silently unreachable.
if SESSION_IDLE_TIMEOUT_SECONDS > SESSION_ABSOLUTE_LIFETIME_SECONDS:
    raise ImproperlyConfigured(
        f"SESSION_IDLE_TIMEOUT_SECONDS ({SESSION_IDLE_TIMEOUT_SECONDS}) must not "
        f"exceed SESSION_ABSOLUTE_LIFETIME_SECONDS "
        f"({SESSION_ABSOLUTE_LIFETIME_SECONDS}); the idle rule of Requirement "
        f"1.12 would never be reachable."
    )

# Cookie-level backstop only (§3.1). The cookie age is the IDLE timeout, not
# the absolute lifetime, and the difference matters: with
# SESSION_SAVE_EVERY_REQUEST the cookie's expiry is pushed forward on every
# request, so an age of 30 minutes expires the cookie after 30 minutes of
# inactivity — exactly the sliding rule Django provides natively. Setting the
# age to 43200 instead would give a *sliding* 12-hour window, which is neither
# of the two rules above: it would not cap an active session at 12 hours, and
# it would let an idle session survive for 12 hours.
#
# Django's sliding expiry cannot express Requirement 1.4 at all, which is why
# the absolute cap is server-side. And because these values ride on a
# client-held cookie, they are a backstop and never the decision: the
# middleware re-derives both caps from session data on the server, so a forged
# or edited cookie expiry cannot extend a session.
SESSION_COOKIE_AGE = SESSION_IDLE_TIMEOUT_SECONDS
SESSION_SAVE_EVERY_REQUEST = True

# Must stay False: a browser-close-scoped cookie ignores SESSION_COOKIE_AGE,
# which would remove the backstop above.
SESSION_EXPIRE_AT_BROWSER_CLOSE = False

# --- Celery + Redis (design §2.7, §3.0.3) ---------------------------------
# Deployment shape per §3.0.3: one Django process, one Celery worker, one beat
# scheduler, Redis as broker.
#
# Per §2.7 the worker carries notification delivery with its 60-second retry
# ladder (Requirement 9.8) and nothing else in v1. Outbound Pipeline_Adapter
# operations are invoked *synchronously* inside the Operator's request under
# ADAPTER_OPERATION_TIMEOUT_SECONDS, so no Operator action depends on a worker
# being up — with Redis down, every action still works and notifications simply
# queue in the database as generated-but-undelivered (§5.2).
CELERY_BROKER_URL = _env_str("CELERY_BROKER_URL", "redis://127.0.0.1:6379/0")
CELERY_RESULT_BACKEND = _env_str("CELERY_RESULT_BACKEND", CELERY_BROKER_URL)

# One worker queue. A single worker serves the whole system, so there is no
# routing to reason about: every task lands here.
CELERY_TASK_DEFAULT_QUEUE = "dashboard"

CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"

# The scheduler runs on UTC — the storage zone, not REPORTING_TIMEZONE. A
# "nightly" job below is nightly in UTC. Only Analytics_View date-range
# boundaries use REPORTING_TIMEZONE.
CELERY_TIMEZONE = TIME_ZONE
CELERY_ENABLE_UTC = True

# Ack after the task body completes, one message in flight at a time, so a
# worker killed mid-task redelivers rather than drops.
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1

# Beat schedule: PLACEHOLDER, deliberately empty.
#
# The scheduler is wired so beat can start, but it schedules nothing yet. The
# design names four eventual beat jobs and each is owned by a later task; each
# is left as a reference rather than an entry, because a schedule entry naming
# a task that does not exist makes beat log an unregistered-task error on every
# tick instead of failing at deploy time.
#
#   * `last_activity_at` consistency job (§3.3) — recomputes
#     `leads.last_activity_at` from its source tables for every Lead, applying
#     the same applied-actions-only filter, and logs drift. It is a *verifier*,
#     not a writer. Owned by task 8.2.
#   * Outreach reconciliation job (§3.6.4) — marks `outreach_requests` pending
#     for more than 5 minutes as `indeterminate` and surfaces them on the
#     Deal_Room_View. It never auto-retries: the design biases to at-most-once
#     because a duplicate cold email is a compliance-visible harm. Owned by
#     task 11.4.
#   * `processed_events` purge (§3.14.3) — deletes claim rows past a 180-day
#     cutoff, comfortably above Requirement 12.5's 90-day retention floor so a
#     clock or scheduling error cannot purge inside the required window. Owned
#     by task 7.3.
#   * `analytics_daily_rollup` (§3.11.4) — OPTIONAL. The escape hatch built
#     only if a §3.11.4 performance budget is missed; v1 computes analytics on
#     read. Designed, not built. Owned by task 17.6.
#
# Note that §3.12 schedules NO audit purge: Requirement 11.8's 24-month
# retention is met by letting `audit_entries` accumulate, which removes any
# risk of a misconfigured cutoff deleting inside the window.
CELERY_BEAT_SCHEDULE: dict[str, dict] = {}
