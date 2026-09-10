# URLvestigia — one command to ship.
#
#   make help      list every target
#   make dev       run the Serve layer
#   make test      the Harden gate
#   make deploy    dry-run the standard deploy
#
# Every target that would change something outside this repo dry-runs by
# default. Pass --execute to the underlying script to make it real.

SHELL := /bin/bash

# Prefer the project virtualenv when one exists, on either platform.
VENV_PY := $(wildcard .venv/Scripts/python.exe) $(wildcard .venv/bin/python)
PYTHON  ?= $(if $(VENV_PY),$(firstword $(VENV_PY)),python3)

CATALOG    ?= spark_catalog
DATABASE   ?= urlvestigia
PORT       ?= 8000
BACKUP_DIR ?= backups

.DEFAULT_GOAL := help
.PHONY: help install install-notebook dev cli notebook doctor test test-live backup ingest pipelines govern provision deploy new clean

## help: list every target
help:
	@echo "URLvestigia — Cloudera Forge accelerator"
	@echo ""
	@echo "  Develop"
	@echo "    make install     install runtime + test dependencies"
	@echo "    make dev         run the Serve layer  → http://127.0.0.1:$(PORT)/"
	@echo "    make doctor      preflight every provider and engine (run before a demo)"
	@echo "    make test        the Harden gate (no network)"
	@echo "    make test-live   add the tests that call real search engines"
	@echo ""
	@echo "  Search without the dashboard"
	@echo "    python scripts/cli.py search \"your question\"   → urls on stdout, recorded"
	@echo "    make cli ARGS=--help              the whole terminal interface"
	@echo "    make install-notebook             add Jupyter"
	@echo "    make notebook                     open quickstart.ipynb"
	@echo ""
	@echo "  Keep the dev store"
	@echo "    make backup      dated local snapshot → $(BACKUP_DIR)/ (safe while running)"
	@echo ""
	@echo "  Inspect the platform layers (all dry runs)"
	@echo "    make ingest      SQLite → Iceberg load plan"
	@echo "    make pipelines   URL enrichment plan, including the literal MERGE"
	@echo "    make govern      SDX / Ranger policy import plan"
	@echo ""
	@echo "  Ship"
	@echo "    make provision   one-time platform provisioning (dry run)"
	@echo "    make deploy      govern → jobs → app (dry run)"
	@echo ""
	@echo "  Template"
	@echo "    make new VERTICAL=healthcare USECASE=readmission-risk"
	@echo "    make clean       remove caches and scratch databases"
	@echo ""
	@echo "  Nothing here changes a remote system without --execute."
	@echo "  Using: $(PYTHON)"

## install: install runtime and test dependencies
# Dependencies live with their layer. app/ pulls in retrieval/, so these two files are
# the full closure.
install:
	$(PYTHON) -m pip install -r app/requirements.txt -r tests/requirements.txt

## dev: run the Serve layer
dev:
	@echo "→ http://127.0.0.1:$(PORT)/"
	$(PYTHON) -m uvicorn app.server:app --reload --port $(PORT)

## cli: the terminal interface -- make cli ARGS="search 'iceberg' --provider arxiv"
# One ARGS target rather than a target per subcommand: a query has to survive two
# levels of quoting to reach argparse through make, and it frequently does not.
# Run `python scripts/cli.py search "..."` directly and this stays a shortcut for
# the flagless commands.
cli:
	$(PYTHON) scripts/cli.py $(ARGS)

## install-notebook: add Jupyter, for quickstart.ipynb
install-notebook:
	$(PYTHON) -m pip install -r requirements-notebook.txt

## notebook: open the quickstart notebook
notebook:
	@$(PYTHON) -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('jupyterlab') else 1)" \
	  || { echo "Jupyter is not installed. Run: make install-notebook"; exit 1; }
	$(PYTHON) -m jupyter lab quickstart.ipynb

## doctor: probe every provider and engine, and say whether this machine is demo-ready
# Calls real services, like test-live. A blocked engine here is a measurement of this
# network, not a defect in the repo — which is why it does not fail the target.
doctor:
	$(PYTHON) scripts/doctor.py

## test: the Harden gate — no network, deterministic
test:
	$(PYTHON) -m pytest tests -q

## test-live: add the tier that calls real search engines
test-live:
	@echo "Calling real search engines. Failures here are often measurements,"
	@echo "not defects — record them in governance/model_cards/."
	$(PYTHON) -m pytest tests -q --live

## backup: snapshot the SQLite dev store to a dated local file
# No --execute here, unlike the platform targets: this writes one new local file
# and refuses to overwrite, so there is nothing remote to guard.
backup:
	$(PYTHON) data/backup.py --dir $(BACKUP_DIR)

## ingest: print the SQLite → Iceberg load plan
ingest:
	$(PYTHON) data/ingest/load_to_iceberg.py --catalog $(CATALOG) --database $(DATABASE)

## pipelines: print the enrichment plan and the MERGE it would run
pipelines:
	$(PYTHON) pipelines/jobs/url_enrichment.py --catalog $(CATALOG) --database $(DATABASE)

## govern: print the SDX / Ranger policy import plan
govern:
	@bash .cicd/deploy.sh govern

## provision: one-time platform provisioning (dry run)
provision:
	@bash infra/cdp/provision.sh

## deploy: govern → jobs → app (dry run)
deploy:
	@bash .cicd/deploy.sh all

## new: scaffold a fresh accelerator from this template
new:
ifndef VERTICAL
	$(error usage: make new VERTICAL=<vertical> USECASE=<usecase>)
endif
ifndef USECASE
	$(error usage: make new VERTICAL=<vertical> USECASE=<usecase>)
endif
	@bash scripts/new-accelerator.sh --vertical $(VERTICAL) --usecase $(USECASE)

## clean: remove caches and scratch databases
clean:
	@find . -path ./.venv -prune -o -type d -name __pycache__ -print0 2>/dev/null | xargs -0 rm -rf
	@rm -rf .pytest_cache report.xml .tmp
	@echo "cleaned (data/urlvestigia.db left alone — delete it by hand if you mean to)"
