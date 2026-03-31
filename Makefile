# Portfolio Tracker — Makefile aligné sur la CLI V2
PYTHON := $(shell if [ -f .venv/bin/python ]; then echo .venv/bin/python; else echo python3; fi)
DATA_DIR := portfolio_tracker/data
MODULE := portfolio_tracker.cli

.PHONY: help install install-dev setup global structured web web-payload bootstrap demo update-navs update-underlyings backfill-market-history manual-movement-add manual-movement-list manual-movement-delete pdf-contract-audit himalia-setup-session himalia-scrape swisslife-scrape docker-build docker-run docker-global docker-update-navs test test-cov lint clean

help:
	@echo "Portfolio Tracker (CLI V2)"
	@echo ""
	@echo "Installation:"
	@echo "  make install / make setup  — venv + pip install -e \".[dev]\""
	@echo ""
	@echo "Consultation:"
	@echo "  make global       — synthèse texte"
	@echo "  make structured   — produits structurés"
	@echo "  make web          — http://127.0.0.1:8765"
	@echo "  make web-payload  — JSON dashboard"
	@echo "  make bootstrap    — JSON bootstrap données"
	@echo "  make demo         — run_example.sh"
	@echo ""
	@echo "Données marché:"
	@echo "  make update-navs"
	@echo "  make update-underlyings"
	@echo "  make backfill-market-history [YEARS=3]  (sans YEARS = historique complet, optionnel: HEADLESS=1)"
	@echo "  make manual-movement-list [CONTRACT=...]"
	@echo "  make manual-movement-add CONTRACT=... ASSET=... DATE=... TYPE=... KIND=... AMOUNT=... REASON=..."
	@echo "  make manual-movement-delete ID=..."
	@echo "  make pdf-contract-audit CONTRACT=... [YEAR=2025]"
	@echo "  make himalia-setup-session [OUTPUT=...] [TIMEOUT_MS=300000]"
	@echo "  make himalia-scrape [CONTRACT_ID=222387113] [STORAGE_STATE=...] [USER_DATA_DIR=...] [OUTPUT=...]"
	@echo "  make swisslife-scrape [CONTRACT_ID=5542AHD34] [HEADED=1] [OUTPUT=...]"
	@echo ""
	@echo "Docker:"
	@echo "  make docker-build"
	@echo "  make docker-run ARGS='global'"
	@echo "  make docker-global"
	@echo "  make docker-update-navs"
	@echo ""
	@echo "Qualité:"
	@echo "  make test / make test-cov / make clean"

.venv/bin/python:
	@echo "Création du virtualenv .venv..."
	python3 -m venv .venv

install: .venv/bin/python
	.venv/bin/python -m pip install -e ".[dev]"

install-dev: install

setup:
	@test -f .venv/bin/python || (python3 -m venv .venv)
	.venv/bin/python -m pip install -e ".[dev]"

global:
	$(PYTHON) -m $(MODULE) --data-dir $(DATA_DIR) global

structured:
	$(PYTHON) -m $(MODULE) --data-dir $(DATA_DIR) structured

web:
	@lsof -ti tcp:8765 | xargs kill -9 2>/dev/null || true
	$(PYTHON) -m $(MODULE) --data-dir $(DATA_DIR) web

web-payload:
	$(PYTHON) -m $(MODULE) --data-dir $(DATA_DIR) web-payload

bootstrap:
	$(PYTHON) -m $(MODULE) --data-dir $(DATA_DIR) bootstrap

demo:
	@bash run_example.sh

update-navs:
	$(PYTHON) -m $(MODULE) --data-dir $(DATA_DIR) update-uc-navs

update-underlyings:
	$(PYTHON) -m $(MODULE) --data-dir $(DATA_DIR) update-underlyings

backfill-market-history:
	$(PYTHON) -m $(MODULE) --data-dir $(DATA_DIR) backfill-market-history \
		$(if $(YEARS),--years $(YEARS),) \
		$(if $(HEADLESS),--headless,)

manual-movement-list:
	$(PYTHON) -m $(MODULE) --data-dir $(DATA_DIR) manual-movement-list \
		$(if $(CONTRACT),--contract "$(CONTRACT)",)

manual-movement-add:
	@if [ -z "$(CONTRACT)" ] || [ -z "$(ASSET)" ] || [ -z "$(DATE)" ] || [ -z "$(TYPE)" ] || [ -z "$(KIND)" ] || [ -z "$(AMOUNT)" ] || [ -z "$(REASON)" ]; then \
		echo "Usage: make manual-movement-add CONTRACT=... ASSET=... DATE=YYYY-MM-DD TYPE=buy|sell|fee|tax|other KIND=external_contribution|internal_capitalization|withdrawal|fee|tax|other AMOUNT=... REASON=... [POSITION=...] [UNITS=...] [NAV=...] [EXTERNAL=1|INTERNAL=1] [DOCUMENT_ID=...] [NOTES=...]"; \
		exit 1; \
	fi
	$(PYTHON) -m $(MODULE) --data-dir $(DATA_DIR) manual-movement-add \
		--contract "$(CONTRACT)" \
		--asset-id "$(ASSET)" \
		$(if $(POSITION),--position-id "$(POSITION)",) \
		--date "$(DATE)" \
		--type "$(TYPE)" \
		--kind "$(KIND)" \
		--amount "$(AMOUNT)" \
		$(if $(UNITS),--units "$(UNITS)",) \
		$(if $(NAV),--nav "$(NAV)",) \
		$(if $(EXTERNAL),--external,) \
		$(if $(INTERNAL),--internal,) \
		$(if $(DOCUMENT_ID),--document-id "$(DOCUMENT_ID)",) \
		--reason "$(REASON)" \
		$(if $(NOTES),--notes "$(NOTES)",)

manual-movement-delete:
	@if [ -z "$(ID)" ]; then \
		echo "Usage: make manual-movement-delete ID=manual_xxx"; \
		exit 1; \
	fi
	$(PYTHON) -m $(MODULE) --data-dir $(DATA_DIR) manual-movement-delete --id "$(ID)"

pdf-contract-audit:
	@if [ -z "$(CONTRACT)" ]; then \
		echo "Usage: make pdf-contract-audit CONTRACT=... [YEAR=2025]"; \
		exit 1; \
	fi
	$(PYTHON) -m $(MODULE) --data-dir $(DATA_DIR) pdf-contract-audit \
		--contract "$(CONTRACT)" \
		$(if $(YEAR),--year "$(YEAR)",)

himalia-scrape:
	$(PYTHON) -m $(MODULE) --data-dir $(DATA_DIR) himalia-scrape \
		$(if $(CONTRACT_ID),--contract-id "$(CONTRACT_ID)",) \
		$(if $(STORAGE_STATE),--storage-state "$(STORAGE_STATE)",) \
		$(if $(USER_DATA_DIR),--user-data-dir "$(USER_DATA_DIR)",) \
		$(if $(OUTPUT),--output "$(OUTPUT)",) \
		$(if $(HEADED),--headed,)

swisslife-scrape:
	$(PYTHON) -m $(MODULE) --data-dir $(DATA_DIR) swisslife-scrape \
		$(if $(CONTRACT_ID),--contract-id "$(CONTRACT_ID)",) \
		$(if $(HEADED),--headed,) \
		$(if $(OUTPUT),--output "$(OUTPUT)",)

docker-build:
	docker compose build

docker-run:
	@if [ -z "$(ARGS)" ]; then \
		echo "Usage: make docker-run ARGS='global'"; \
		exit 1; \
	fi
	docker compose run --rm portfolio-tracker $(ARGS)

docker-global:
	docker compose run --rm portfolio-tracker global

docker-update-navs:
	docker compose run --rm portfolio-tracker update-uc-navs

test:
	$(PYTHON) -m pytest

test-cov:
	$(PYTHON) -m pytest --cov=portfolio_tracker --cov-report=term-missing

lint:
	@echo "Linting non configuré (ajouter ruff/flake8 si besoin)"

clean:
	find . -type d -name "__pycache__" -exec rm -r {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type f -name ".coverage" -delete
	find . -type d -name "*.egg-info" -exec rm -r {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -r {} + 2>/dev/null || true
