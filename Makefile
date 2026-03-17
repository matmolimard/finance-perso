# Makefile pour Portfolio Tracker
# Variables
# Utilise le venv si disponible, sinon python3 système
PYTHON := $(shell if [ -f .venv/bin/python ]; then echo .venv/bin/python; else echo python3; fi)
DATA_DIR := portfolio_tracker/data
MODULE := portfolio_tracker.cli

.PHONY: help install install-dev install-charts setup alerts uc structured fonds-euro global contract-performance perf demo update-navs update-underlyings backfill-market-history advice advice-dry-run history docker-build docker-run docker-global docker-update-navs docker-history test tests test-cov lint clean

# Commande par défaut
help:
	@echo "Portfolio Tracker - Commandes disponibles:"
	@echo ""
	@echo "📦 Installation:"
	@echo "  make install          - Installe le package en mode développement (recommandé)"
	@echo "  make install-charts   - Comme install + plotext (graphiques dans make history)"
	@echo "  make setup            - Alias pour make install"
	@echo ""
	@echo "📊 Consultation:"
	@echo "  make alerts           - Affiche les alertes"
	@echo "  make uc               - Vue performance des unités de compte"
	@echo "  make structured       - Vue des produits structurés"
	@echo "  make fonds-euro       - Vue des fonds euros"
	@echo "  make global           - Vue globale (fonds euros + UC + structurés + récapitulatif)"
	@echo "  make global PORTFOLIO=HIMAL - Vue globale filtrée par portefeuille (ex: HIMAL, Swiss)"
	@echo "  make contract-performance - Performance du contrat Swiss Life année par année"
	@echo "  make perf             - Alias pour make contract-performance"
	@echo "  make history                 - Historique : menu pour choisir la série (NAV UC, sous-jacent, taux)"
	@echo "  make history ALL=1            - Historique + graphe pour toutes les séries"
	@echo "  make history ALL=1 FROM=2026-03-01 TO=2026-03-15  - Toutes les séries avec filtre de date"
	@echo "  make history VALUE=bdl_rempart  - Historique d'une valeur donnée"
	@echo "  make history CHART_TYPE=bar  - Diagramme à bâtons au lieu de la courbe"
	@echo "  make history CHART_MARKER=braille CHART_COLOR=blue  - Courbe fine (dot/sd/braille), couleur"
	@echo "  make demo             - Lance la démonstration complète"
	@echo ""
	@echo "🤖 Conseils IA:"
	@echo "  make advice           - Analyse tous les profils et génère des recommandations IA"
	@echo "  make advice PROFILE=HIMALIA   - Recommandations pour un profil spécifique"
	@echo "  make advice PROFILE=HIMALIA INTERACTIVE=1   - Mode conversationnel après les recommandations"
	@echo "  make advice-dry-run   - Teste le prompt sans appeler l'API (ex: make advice-dry-run PROFILE=HIMALIA)"
	@echo ""
	@echo "🔄 Mise à jour des données:"
	@echo "  make update-navs      - Met à jour les VL quotidiennes des UC"
	@echo "  make update-underlyings - Met à jour les séries de sous-jacents"
	@echo "  make backfill-market-history YEARS=3 - Tente de récupérer 3 ans d'historique UC + sous-jacents"
	@echo ""
	@echo "🐳 Docker:"
	@echo "  make docker-build     - Build l'image Docker locale"
	@echo "  make docker-run ARGS='global' - Lance une commande CLI dans Docker"
	@echo "  make docker-global    - Vue globale via Docker"
	@echo "  make docker-update-navs - update-uc-navs via Docker"
	@echo "  make docker-history VALUE=bdl_rempart - historique via Docker"
	@echo ""
	@echo "🧪 Tests et qualité:"
	@echo "  make test             - Lance les tests avec pytest"
	@echo "  make test-cov         - Tests avec couverture de code"
	@echo "  make lint             - Vérification du code (si configuré)"
	@echo ""
	@echo "🧹 Utilitaires:"
	@echo "  make clean             - Nettoie les fichiers temporaires"
	@echo "  make help              - Affiche cette aide"

# Installation
install:
	$(PYTHON) -m pip install -e ".[dev]"
	@echo "✓ Installation complète terminée"

# Installation avec graphiques terminal (plotext pour make history)
install-charts:
	$(PYTHON) -m pip install -e ".[dev,charts]"
	@echo "✓ Installation terminée (avec plotext pour les graphiques history)"

# Alias pour compatibilité
install-dev: install
	@echo "✓ Installation complète terminée"

setup: install

# Consultation
alerts:
	$(PYTHON) -m $(MODULE) --data-dir $(DATA_DIR) alerts

uc:
	$(PYTHON) -m $(MODULE) --data-dir $(DATA_DIR) uc

structured:
	$(PYTHON) -m $(MODULE) --data-dir $(DATA_DIR) structured

fonds-euro:
	$(PYTHON) -m $(MODULE) --data-dir $(DATA_DIR) fonds-euro

global:
	@if [ -z "$(PORTFOLIO)" ]; then \
		$(PYTHON) -m $(MODULE) --data-dir $(DATA_DIR) global; \
	else \
		$(PYTHON) -m $(MODULE) --data-dir $(DATA_DIR) global --portfolio $(PORTFOLIO); \
	fi

contract-performance:
	@if [ -z "$(CONTRACT)" ]; then \
		$(PYTHON) -m $(MODULE) --data-dir $(DATA_DIR) contract-performance; \
	else \
		$(PYTHON) -m $(MODULE) --data-dir $(DATA_DIR) contract-performance --contract "$(CONTRACT)"; \
	fi

history:
	@$(PYTHON) -m $(MODULE) --data-dir $(DATA_DIR) history \
		$(if $(VALUE),$(VALUE),) \
		$(if $(FROM),--from $(FROM),) \
		$(if $(TO),--to $(TO),) \
		$(if $(NO_CHART),--no-chart,) \
		$(if $(ALL),--all,) \
		$(if $(CHART_TYPE),--chart-type $(CHART_TYPE),) \
		$(if $(CHART_MARKER),--chart-marker $(CHART_MARKER),) \
		$(if $(CHART_COLOR),--chart-color $(CHART_COLOR),)

# Alias court pour contract-performance
perf: contract-performance

demo:
	@bash run_example.sh

# Conseils IA
advice:
	@if [ -z "$(PROFILE)" ]; then \
		$(PYTHON) -m $(MODULE) --data-dir $(DATA_DIR) advice --all $(if $(INTERACTIVE),--interactive,); \
	else \
		$(PYTHON) -m $(MODULE) --data-dir $(DATA_DIR) advice --profile $(PROFILE) $(if $(INTERACTIVE),--interactive,); \
	fi

advice-dry-run:
	@if [ -z "$(PROFILE)" ]; then \
		echo "⚠️  Mode dry-run sans profil spécifié (tous les profils)"; \
		$(PYTHON) -m $(MODULE) --data-dir $(DATA_DIR) advice --all --dry-run; \
	else \
		echo "⚠️  Mode dry-run pour le profil: $(PROFILE)"; \
		$(PYTHON) -m $(MODULE) --data-dir $(DATA_DIR) advice --profile $(PROFILE) --dry-run; \
	fi

# Mise à jour des données
update-navs:
	$(PYTHON) -m $(MODULE) --data-dir $(DATA_DIR) update-uc-navs

update-underlyings:
	$(PYTHON) -m $(MODULE) --data-dir $(DATA_DIR) update-underlyings

backfill-market-history:
	$(PYTHON) -m $(MODULE) --data-dir $(DATA_DIR) backfill-market-history \
		--years $(if $(YEARS),$(YEARS),3) \
		$(if $(HEADLESS),--headless,)

# Docker
docker-build:
	docker compose build

docker-run:
	@if [ -z "$(ARGS)" ]; then \
		echo "Usage: make docker-run ARGS='global'"; \
		echo "Ex:    make docker-run ARGS='history bdl_rempart --from 2026-01-01'"; \
		exit 1; \
	fi
	docker compose run --rm portfolio-tracker $(ARGS)

docker-global:
	docker compose run --rm portfolio-tracker global

docker-update-navs:
	docker compose run --rm portfolio-tracker update-uc-navs

docker-history:
	@if [ -z "$(VALUE)" ]; then \
		echo "Usage: make docker-history VALUE=<terme>"; \
		echo "Ex:    make docker-history VALUE=CMS_EUR FROM=2026-01-01"; \
		exit 1; \
	fi
	docker compose run --rm portfolio-tracker history $(VALUE) \
		$(if $(FROM),--from $(FROM),) \
		$(if $(TO),--to $(TO),) \
		$(if $(NO_CHART),--no-chart,)

# Tests
test:
	$(PYTHON) -m pytest

tests:
	$(PYTHON) -m pytest tests/test_structured_products_features.py tests/test_cli_commands.py tests/test_fees_display.py tests/test_uc_view_features.py -v
	@echo "✓ Tests des fonctionnalités structurées, commandes CLI, affichage des frais et vue UC exécutés"

test-cov:
	$(PYTHON) -m pytest --cov=portfolio_tracker --cov-report=term-missing

lint:
	@echo "⚠ Linting non configuré pour le moment"
	@echo "Vous pouvez ajouter flake8, black, pylint, etc."

# Nettoyage
clean:
	find . -type d -name "__pycache__" -exec rm -r {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete
	find . -type f -name ".coverage" -delete
	find . -type d -name "*.egg-info" -exec rm -r {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -r {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -r {} + 2>/dev/null || true
	@echo "✓ Nettoyage terminé"

