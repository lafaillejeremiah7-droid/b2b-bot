# B2B Lead Kitchen

A Python/Django B2B website-design automation company built around eight cooperating employees:

1. **Scout** — discovers operating businesses from Google Places.
2. **Researcher** — verifies public business, website, and contact evidence.
3. **Qualifier** — deterministically rejects weak/unsupported leads.
4. **Personalizer** — prepares grounded outreach from verified evidence.
5. **Sales Bot** — revalidates clearance and sends through Yahoo Business SMTP.
6. **Manager** — enforces pipeline order and exposes the true blocker.
7. **Closer** — classifies replies, persists suppression, and prepares approved Stripe invoice links.
8. **Boss** — supervises outcomes without bypassing evidence, compliance, or human approval gates.

## Kitchen dashboard

`/dashboard/` presents the company as an eight-station kitchen. Persisted leads are the dishes moving through the company. Rejections are represented by the **Rejection Furnace**. Counts and lead rows come from the database; the UI does not invent performance numbers when no data exists.

## Runtime safety

External side effects fail closed. Stub mode never reports a customer email or delivery as completed. Sales Bot and asynchronous operator email alerts use the same Yahoo SMTP boundary with deployment-only credentials. Stripe creates hosted invoice links but does not send the customer email. Stripe webhooks and generic pipeline events are authenticated. Suppression is durable, outbound handoffs are digest-bound, website research blocks private-network SSRF paths, and release retries stay bound to the originally approved recipient, archive URL, and delivery identity.

`python manage.py check --deploy --fail-level ERROR` includes a B2B-specific live-readiness check. In live mode, production deployment is blocked until Google Places, independent web search, outbound identity, Yahoo SMTP, Stripe/webhook verification, pipeline-event authentication, an external Celery broker/result backend, production Django settings, and live adapter mode are configured.

## Production topology

`render.yaml` defines:

- Django/Gunicorn web service on `main`, gated on passing GitHub checks.
- PostgreSQL 16.
- Render Key Value as the Celery queue with `noeviction` (jobs are never discarded to make room).
- A Celery worker for asynchronous notifications.
- A Celery Beat worker for reservation reconciliation and scheduled consistency/retention tasks.

The Key Value instance is intentionally declared on Render's free plan in the repository, so persistence is off. For stronger queue durability across Key Value restarts/upgrades, move that instance to a paid persistent plan in Render. Background workers require a paid Render compute plan.

## Secrets

Never commit credentials. Copy `.env.example` only for local setup and supply real values through the private deployment environment. Existing Render Blueprints do not re-prompt when new `sync: false` variables are added, so missing secret values must be entered in Render manually before the live-readiness check will allow deployment.
