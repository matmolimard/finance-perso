# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install (creates venv, installs deps in dev mode)
make install

# Install with chart support (plotext)
make install-charts

# Run tests
make test
make test-cov

# Lint
make lint

# View portfolio (main use cases)
make global
make uc
make structured
make fonds-euro
make alerts

# Update market data
make update-navs
make update-underlyings
make backfill-market-history YEARS=3

# AI advisory (requires OPENROUTER_API_KEY in .env)
make advice
make advice PROFILE=HIMALIA

# Docker
make docker-build
make docker-global

# See all commands
make help
```

The CLI entry point is `portfolio-tracker` (defined in `setup.py`), mapping to `portfolio_tracker/cli.py:main`.

## Architecture

This is a **personal portfolio tracker** for French insurance contracts (assurance vie, contrats de capitalisation) with 4 specialized valuation engines. All data is stored in YAML files — there is no database.

### Core Domain

- **Asset** (`core/asset.py`): Financial instrument definition. 4 types: `structured_product`, `fonds_euro`, `uc_fund`, `uc_illiquid`.
- **Position** (`core/position.py`): Actual ownership of an asset in a contract (wrapper). Contains lots, purchase details, investment amounts.
- **Portfolio** (`core/portfolio.py`): Loads all assets + positions from YAML, provides query methods.

### Valuation Engines (`valuation/`)

Each asset type maps to an engine implementing `BaseValuationEngine.valuate()` → `ValuationResult`:

| Engine | Asset Type | Logic |
|--------|-----------|-------|
| `EventBasedEngine` | `structured_product` | Coupon/autocall events, no mark-to-market |
| `DeclarativeEngine` | `fonds_euro` | Accepts declared rates as-is (opacity by design) |
| `MarkToMarketEngine` | `uc_fund` | NAV × units |
| `HybridEngine` | `uc_illiquid` | Mark-to-market with estimation fallback |

### Market Data (`market/`)

- `nav.py` / `nav_store.py` / `nav_daily.py` — NAV history for UC funds
- `rates.py` — Interest rates (CMS, EURIBOR)
- `underlyings.py` — Underlying indices for structured products
- `fetch_underlyings.py` — Scrapers using Playwright (Solactive, Euronext, MerQube, Natixis, Investing.com)
- `quantalys.py` — Fund ratings from Quantalys (auto-fetched during `update-navs`)

### Data Files (`portfolio_tracker/data/`)

- `assets.yaml` — Asset definitions (structured products, funds, etc.)
- `positions.yaml` — Holdings per contract (39 KB, real user data)
- `market_data/` — NAV history (`nav_*.yaml`), events (`events_*.yaml`), rates, underlyings, snapshots, Quantalys ratings

### Alert System (`alerts/`)

Rules: `DataFreshnessRule`, `StructuredProductObservationRule`, `UnderlyingThresholdRule`, `MissingValuationRule`. Notifiers: console, file, email.

### Advisory System (`advisory/`)

Uses OpenRouter API (key in `.env`). Model configured via `OPENROUTER_MODEL` env var. Supports profile-based analysis (HIMALIA, SWISSLIFE).

### CLI (`cli.py` ~1500 lines)

`PortfolioCLI` class. Key features: lot classification, XIRR calculation, contract snapshots, Himalia movement import, optional terminal charting via `plotext`.

### Validation

`schemas.py` (Pydantic v2) + `validation.py` validate all YAML data on load. Run `make validate` to check data files standalone.

## Key Design Decisions

- **No database**: All state in YAML files under `portfolio_tracker/data/`. Market data is committed to the repo.
- **Euro fund opacity**: The system deliberately does not recalculate euro fund values — it trusts declared insurer rates.
- **Playwright is optional**: Web scraping for underlyings requires `playwright install chromium`. Not in default `requirements.txt`.
- **French domain**: All user-facing text, YAML keys, and domain concepts use French terminology (enveloppe, UC, fonds euro, versement, etc.).
- **Long-term focus**: No trading logic. The system tracks wealth evolution over years, not days.
