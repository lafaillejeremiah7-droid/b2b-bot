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

## Company Bot 1 — Discovery

`dashboard/discovery/` contains the first company bot as a pure, authority-
bounded pipeline. Every successful discovery action runs in this exact order:

1. **Luna** (`gpt-5.6-luna`) extracts candidate Lead claims quickly from
   explicitly supplied, untrusted public-source snapshots.
2. **Terra** (`gpt-5.6-terra`) independently verifies every Luna claim and
   records contradictions.
3. **Sol** (`gpt-5.6-sol`) adjudicates the verified record and returns
   `ACCEPTED`, `REVIEW_REQUIRED`, or `REJECTED`.

Every stage has an exact runtime schema and is chained to its parent by a
canonical SHA-256 digest. Unknown fields, changed model identities, malformed
outputs, unverified citations, provider failures, and idempotency-key conflicts
fail closed. Source text remains separate from the trusted directive so a future
provider adapter can preserve the system/user prompt boundary.

The bot has discovery authority only. It cannot import Django models or the
dashboard service/adapter packages, send outreach, alter pipeline state, create
deals, issue invoices, move money, or release a website. Its accepted result is
a sealed `DiscoveryPacket`, not a database write. Lead persistence remains
deliberately disabled until task 6.2 supplies the dashboard-owned transaction
that creates both the `New_Lead` genesis history and matching
`last_activity_at`; bypassing that seam would create corrupt pipeline history.

Model-provider clients are injected through `StructuredModelPort`. The port
contract requires the fixed directive to be sent as trusted system/developer
instructions and source snapshots as untrusted user/tool data. No live provider
or credentials are embedded in this repository.

## Company Bot 2 — Qualification Intelligence

`dashboard/qualification/` accepts only a complete, `ACCEPTED` Bot 1 packet and
runs a second Luna → Terra → Sol chain. Luna extracts opportunity evidence,
Terra independently audits every claim, and Sol applies deterministic gates to
return `QUALIFIED`, `MANUAL_REVIEW`, or `REJECTED`.

The five 1–5 scores are explicitly bounded heuristics, never probabilities.
`QUALIFIED` requires a score total of at least 18, offer fit of at least 4,
ability to pay and contactability of at least 3, evidence quality of at least 4,
and no contradicted evidence or unresolved conflict. Every exposed profile field
must reproduce a Terra-verified Luna claim and cite its content digest.

Bot 2 cannot contact anyone, select or promise a price, write a Lead or Pipeline
State, create a Deal or invoice, move money, or authorize delivery. Its only
output is a sealed `QualificationPacket` for the future outreach-strategy bot.

## Company Bot 3 — Outreach Strategy

`dashboard/outreach_strategy/` accepts only matching, fully chained Bot 1 and
`QUALIFIED` Bot 2 packets. Luna drafts a personalized email or call strategy,
Terra audits every factual message claim against the verified upstream fields,
and Sol may return `READY_FOR_HUMAN_APPROVAL` only when the audit is complete
and clean. The exact Luna draft is content-addressed, so Sol cannot silently
rewrite it after Terra's review.

The runtime contract rejects unsupported profile fields, prices, numerical
probabilities, guarantees, duplicate or incomplete claim audits, malformed
follow-up sequences, upstream packet mismatches, and model identity changes.
Every successful packet permanently carries `human_approval_required=True`.

Bot 3 cannot send or schedule outreach, contact a person, select or promise a
price, change pipeline state, create a Deal or invoice, move money, or authorize
delivery. A future dashboard-owned controller must obtain human approval before
submitting any strategy to an adapter.

## Company Bot 4 — Deal & Compliance

`dashboard/deal_compliance/` accepts only a fully audited Bot 3 draft plus an
operator approval whose digest matches that exact draft. Luna extracts deal
facts from an untrusted prospect event, Terra audits every fact and compliance
condition, and Sol may recommend no action, human review, a response, quote,
contract preparation, or invoice preparation.

Suggested prices are explicitly heuristic. An agreed price can only be copied
unchanged from a $550–$1,000 operator authorization. Contract or invoice
preparation additionally requires verified prospect acceptance. Email or call
opt-outs force `NO_ACTION`, and any dollar amount in a draft must match an
authorized suggested or agreed price.

Bot 4 prepares artifacts only. It cannot send outreach, bind a contract, create
or send an invoice, record payment, change pipeline state, or deliver a website.
Every returned `DealPacket` requires a fresh human approval before external use.

## Company Bot 5 — Website Production

`dashboard/site_production/` requires a complete Bot 4 packet plus an operator
authorization confirming the contract and targeting that exact deal digest.
Luna creates the conversion and luxury blueprint, Terra scores conversion
clarity, luxury coherence, mobile usability, accessibility readiness, and
evidence integrity independently, and Sol produces a bounded multi-file static
website package only when every score is at least 4/5 with no violation or
required change.

The artifact contract requires a home page and stylesheet, safe relative paths,
bounded file and total sizes, the approved company identity and primary CTA,
and only operator-approved business claims. It rejects remote executable scripts,
inline event handlers, `javascript:` URLs, fabricated guarantees, and numerical
conversion promises. Conversion choices remain hypotheses until measured.

Bot 5 can generate the website files, but it cannot publish, deploy, transfer,
email, deliver, mark payment, or mutate pipeline state. Its only successful
destination is a sealed `ProductionPacket` for Bot 6's independent QA and
release gate.

## Company Bot 6 — Quality Assurance & Release Gate

`dashboard/release_quality/` accepts only a complete, digest-chained Bot 5
packet whose build outcome is `READY_FOR_QA`. Luna inventories functional,
responsive, accessibility, content, security, and evidence-integrity findings
against immutable file digests. Terra independently verifies every finding and
scores all six dimensions. Sol applies deterministic gates and returns
`APPROVED_FOR_HUMAN_RELEASE`, `REWORK_REQUIRED`, or `REJECTED`.

Approval requires every score to be at least 4/5, no confirmed finding, and no
required change. A confirmed blocking security or evidence-integrity finding is
rejected; other failures require rework. Exact schemas, content-addressed stage
handoffs, fixed model identities, and idempotency claims fail closed.

Bot 6 is a release **recommendation** gate, not a deployment actor. It cannot
edit Bot 5 files, publish, deploy, contact a prospect, record or verify payment,
create a release authorization, deliver a website, or mutate pipeline state.
Every outcome permanently requires a human operator to make the final release
decision through the dashboard's payment and release safeguards.

## Local development

PostgreSQL 16 is the only supported backend — the schema depends on partial
unique indexes, generated stored columns, plpgsql triggers, JSONB and
`ON CONFLICT`. There is no SQLite fallback.

```sh
make install          # uv venv + `pip install -e ".[dev]"`
make migrate
```

Creating the `pg_trgm` extension needs a role with CREATE on the database; that
is a migration-time privilege, separate from the restricted application role the
release and audit safeguards rely on (design §3.1, §3.7.3).

### Running the tests

**Use the `make` targets.** In the development sandbox, background processes do
not survive between shell invocations, so a PostgreSQL server started by one
command is gone by the next. Every database-touching target starts the server
itself, in its own recipe — which is why `make db-up && make test` from a fresh
shell does *not* work and `make test` alone does. The incantation is
`$(PG_ENSURE)` at the top of the Makefile; there is no need to rediscover it.

```sh
make test                                  # the whole suite
make test PYTEST_ARGS="-k operator -x"     # forward any pytest flags
make ci                                    # everything CI runs, in CI's order
make help                                  # all targets
```

Connection defaults match `config/settings/base.py`: host `127.0.0.1`, database
`deal_room`, role `deal_room_app`.

`make ci` runs the five build steps, each of which is also a separate step in
`.github/workflows/ci.yml` so a red build names the guarantee that broke:

| Target | What it checks |
|---|---|
| `make test` | the pytest suite (design §7.1–§7.5) |
| `make lint-imports` | the §3.0.1 / §3.7.2 layering contracts in `.importlinter` |
| `make check-import-assertions` | the §3.5.1 / §3.5.5 import-time assertions (§7.6) |
| `make check-activations` | nothing task 1.4 deferred has been forgotten |
| `make test-migrations` | task 3.5's fresh-migrate schema and privilege assertions |

Two of those are currently **switched-off-but-wired**, and deliberately so.
`.importlinter` declares its three contracts with empty `source_modules` because
the modules they constrain do not exist yet, and no test carries the
`migrations` marker until task 3.5. Both states report green, and both are
indistinguishable in a build log from "the rule has subjects and is not being
enforced" — so `make check-activations` exists to tell them apart. It fails the
build the moment a rule acquires subjects while still switched off, and names
the owning task and the exact edit. Do not silence it.

Hypothesis iteration budgets live in `dashboard/tests/hypothesis_profiles.py` as
named profiles encoding design §7.2, so a property test selects a budget rather
than restating `max_examples`:

```python
from dashboard.tests.hypothesis_profiles import Profile, use

@use(Profile.PURE)          # 200 examples — cheap pure-function properties
@given(...)
def test_property_20_...(...): ...
```

Registered: `ci` (the 100-example floor, and the default), `pure` / `pure_thorough`
(200 / 1000), `stateful` / `stateful_deep` (100 examples at 10 / 50 steps), and
`concurrency`. Select one for a whole run with `--hypothesis-profile=<name>` or
`HYPOTHESIS_PROFILE=<name>`. The seed is pinned to `0` in `pytest.ini` so a CI
counterexample replays locally; widen the search with `--hypothesis-seed=random`.

Tests needing genuine concurrent transactions (Properties 10, 24, 42, 45) must
be marked `@pytest.mark.concurrency` and run under `TransactionTestCase` or
`@pytest.mark.django_db(transaction=True)`. `conftest.py` fails a marked test
that runs inside a wrapping transaction, because such a test *passes* — the
racers cannot see each other's uncommitted data, so the race never happens.
