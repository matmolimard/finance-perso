# CLAUDE.md

Guide rapide pour les assistants IA travaillant sur ce dépôt.

## Documentation

Toute la documentation utilisateur et métier est centralisée dans **[README.md](README.md)** (installation, CLI, Docker, règles métier, Quantalys, index de `docs/`).

## Commandes utiles

```bash
make install          # venv + pip install -e ".[dev]"
make test             # pytest
make global           # synthèse CLI
make structured       # tableau structurés
make web              # interface locale
make update-navs      # update-uc-navs
make docker-build && make docker-global
make help
```

Point d’entrée CLI : `portfolio-tracker` → `portfolio_tracker.cli:main` ; `python -m portfolio_tracker.cli` est équivalent.

## Architecture (rappel)

- Données : YAML de seed + SQLite opérationnelle sous `portfolio_tracker/data/`.
- Cœur applicatif : modules racine `portfolio_tracker/` (runtime, dashboard, marché, CLI).
- Valorisation : `portfolio_tracker/valuation.py` (event_based, declarative, mark_to_market, hybrid).
- Domaine : `portfolio_tracker/domain/` (lots, analytics, projections).
- GED / documents : `document_ingest.py`, `ged.py`, upload web → index + SQLite.
- Snapshots annuels : `bootstrap.py` / `annual_snapshots` ; validation via `manual.save_snapshot_validation`.
- Arbitrages PDF : `arbitration.py` (Generali + SwissLife) ; API `/api/documents/.../arbitration-*`, persistance SQLite.
- Dashboard : valeurs `official_*` vs `model_structured_value` / écarts structurés (`dashboard.py`, UI `web/static/v2*.js`).

Specs détaillées V2 : répertoire `docs/`. Parcours GED / snapshots / arbitrages : **README.md** (section dédiée).
