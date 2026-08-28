# b2b-bot — Deal Room Dashboard

Operator console over the B2B outreach pipeline. Spec lives in
[`.kiro/specs/deal-room-dashboard/`](.kiro/specs/deal-room-dashboard/).

**Stack** (design §2.2, §2.3, §2.7): Django 5 on PostgreSQL 16, server-rendered
templates with HTMX, Celery + Redis for notification delivery only.

## Layout

```
config/                 Django project package
  settings/base.py      shared settings (§3.0.3 keys land here — task 1.2)
  urls.py wsgi.py asgi.py
dashboard/              the application
  models/               persistence only
  services/             transaction owners; the only caller of adapter/
  adapter/              Pipeline_Adapter boundary (stub + live)
  views/                parse → one service call → render; never writes
  templates/dashboard/  server-rendered screens
  migrations/           0001 enables pg_trgm for the §4.7 search index
```

`models/`, `services/`, `adapter/` and `views/` are packages rather than modules
so the four layering rules of design §3.0.1 can be expressed as import-linter
contracts (task 1.4). Do not collapse them into single files.

## Local development

PostgreSQL 16 is the only supported backend — the schema depends on partial
unique indexes, generated stored columns, plpgsql triggers, JSONB and
`ON CONFLICT`. There is no SQLite fallback.

```sh
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -e .
export POSTGRES_HOST=127.0.0.1 POSTGRES_DB=deal_room POSTGRES_USER=deal_room_app
.venv/bin/python manage.py migrate
```

Creating the `pg_trgm` extension needs a role with CREATE on the database; that
is a migration-time privilege, separate from the restricted application role the
release and audit safeguards rely on (design §3.1, §3.7.3).
