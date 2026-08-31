"""Celery application for the Deal Room Dashboard (design §2.7, §3.0.3).

Broker, queue, serialization, and beat schedule all live in
``config.settings.base`` under the ``CELERY_`` namespace; this module only
constructs the app and points it at those settings. There is one worker queue
and the beat schedule is an empty placeholder — see the comments beside
``CELERY_BEAT_SCHEDULE`` for the four jobs the design eventually schedules and
the tasks that own them.

No task is defined here. Per §2.7, Celery carries notification delivery only
(Requirement 9.8); outbound adapter operations are synchronous.
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.base")

app = Celery("deal_room")

# `namespace="CELERY"` means every Celery option is a CELERY_-prefixed Django
# setting, so the deployment has exactly one place to configure — and
# CELERY_BROKER_URL resolves through django.conf.settings rather than through a
# second, divergent config source.
app.config_from_object("django.conf:settings", namespace="CELERY")

# Picks up `tasks.py` in each installed app once tasks exist (tasks 8.2, 11.4,
# 7.3, 16.3). Lazy: it reads INSTALLED_APPS when the app is finalized.
app.autodiscover_tasks()
