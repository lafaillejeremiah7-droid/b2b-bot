# Deal Room Dashboard — local development and CI parity (task 1.4).
#
# THE ONE THING TO KNOW ABOUT THIS FILE
# -------------------------------------
# In the development sandbox, background processes do not survive between shell
# invocations. The PostgreSQL server started by one command is gone by the next,
# so `make db-up && make test` does not work: `db-up` starts a server that dies
# with its shell, and `test` then fails to connect.
#
# Every target that touches the database therefore starts the server *itself*,
# in its own recipe, via $(PG_ENSURE). That is why the startup incantation is a
# variable and not a prerequisite target — a prerequisite runs in a different
# shell, which is exactly the thing that does not work here.
#
# `pg_ctl -w start` is idempotent enough for this: it exits non-zero when a
# server is already running, and $(PG_ENSURE) swallows that. So calling a target
# twice, or calling `make ci` which chains several, costs nothing.
#
# PostgreSQL 16 is the only supported backend (design §2.2) and there is no
# SQLite fallback. If a target cannot reach a server it fails; it does not
# degrade to something that runs.

SHELL := /bin/bash
.ONESHELL:
.SHELLFLAGS := -eu -o pipefail -c

PYTHON  ?= .venv/bin/python
# The console script, NOT `python -m importlinter.cli`: that form resolves to a
# click group with no command attached, prints nothing, and exits 0 — a check
# that silently passes, which is the one thing this harness must not ship.
LINT_IMPORTS ?= .venv/bin/lint-imports
PGDATA  ?= /var/lib/pgsql/data
PGSOCK  ?= /var/run/postgresql
PGUSER_SYS ?= postgres

# Matches the defaults in config/settings/base.py, so the environment below is
# documentation as much as configuration.
export POSTGRES_HOST ?= 127.0.0.1
export POSTGRES_PORT ?= 5432
export POSTGRES_DB   ?= deal_room
export POSTGRES_USER ?= deal_room_app

# Extra arguments forwarded to pytest, e.g.
#   make test PYTEST_ARGS="-k operator -x"
#   make test PYTEST_ARGS="--reuse-db"          # fast loop; see pytest.ini
#   make test PYTEST_ARGS="--hypothesis-seed=random"
PYTEST_ARGS ?=

# Start PostgreSQL if it is not already up. Must be the first line of any recipe
# that talks to the database — see the note at the top of this file.
PG_ENSURE = su $(PGUSER_SYS) -c "/usr/bin/pg_ctl -D $(PGDATA) -o '-k $(PGSOCK)' -w start" >/dev/null 2>&1 || true

.PHONY: help install db-up psql migrate test test-migrations lint-imports \
        check-import-assertions check-activations checks ci clean

help:
	@echo "Deal Room Dashboard — make targets"
	@echo
	@echo "  make install                 create .venv and install the package + [dev] extras"
	@echo "  make db-up                   start PostgreSQL 16 (dies with this shell — see Makefile header)"
	@echo "  make psql                    interactive psql against $(POSTGRES_DB)"
	@echo "  make migrate                 apply migrations"
	@echo
	@echo "  make test                    the full pytest suite (starts PostgreSQL itself)"
	@echo "  make test-migrations         only the task 3.5 fresh-migrate assertions (-m migrations)"
	@echo "  make lint-imports            the §3.0.1 / §3.7.2 import-linter contracts"
	@echo "  make check-import-assertions the §7.6 import-time assertion check"
	@echo "  make check-activations       nothing task 1.4 deferred has been forgotten"
	@echo "  make checks                  the three non-pytest checks above"
	@echo "  make ci                      everything CI runs, in CI's order"
	@echo
	@echo "  Forward pytest flags with PYTEST_ARGS, e.g."
	@echo "    make test PYTEST_ARGS='-k operator -x'"

install:
	uv venv --python 3.11 .venv
	uv pip install --python $(PYTHON) -e ".[dev]"

# Starting the server on its own is useful only for a single follow-on command
# in the SAME shell, e.g. `make db-up && psql ...` will not work from a fresh
# make invocation. Prefer `make psql` / `make test`, which are self-contained.
db-up:
	$(PG_ENSURE)
	pg_isready -h $(POSTGRES_HOST) -p $(POSTGRES_PORT)

psql:
	$(PG_ENSURE)
	psql -h $(POSTGRES_HOST) -p $(POSTGRES_PORT) -U $(POSTGRES_USER) -d $(POSTGRES_DB)

migrate:
	$(PG_ENSURE)
	$(PYTHON) manage.py migrate

# --------------------------------------------------------------------------
# The four CI steps, each runnable on its own
# --------------------------------------------------------------------------
test:
	$(PG_ENSURE)
	$(PYTHON) -m pytest $(PYTEST_ARGS)

# Task 3.5's fresh-migrate schema and privilege assertions (§7.4, §7.6).
# Tolerates an empty selection (pytest exit code 5) for exactly as long as the
# marker is unused; `make check-activations` fails the build once trigger
# migrations exist and nothing carries the marker, so this cannot stay empty
# unnoticed.
test-migrations:
	$(PG_ENSURE)
	set +e
	$(PYTHON) -m pytest -m migrations $(PYTEST_ARGS)
	code=$$?
	set -e
	if [ $$code -eq 5 ]; then
	  echo "note: no tests marked 'migrations' yet — task 3.5 owns them."
	  exit 0
	fi
	exit $$code

lint-imports:
	$(LINT_IMPORTS)

check-import-assertions:
	$(PYTHON) scripts/check_import_time_assertions.py

check-activations:
	$(PYTHON) scripts/check_deferred_activations.py

checks: lint-imports check-import-assertions check-activations

# Everything CI runs, in CI's order. Run this before pushing.
ci:
	$(MAKE) test
	$(MAKE) lint-imports
	$(MAKE) check-import-assertions
	$(MAKE) check-activations
	$(MAKE) test-migrations

clean:
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
	rm -rf .pytest_cache .hypothesis
