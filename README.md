# Portfolio Tracker

Outil Python de suivi patrimonial multi-actifs (assurance vie, contrats de capitalisation), orienté long terme, sans logique de trading. L’état opérationnel V2 vit désormais en SQLite sous `portfolio_tracker/data/`, avec encore quelques fichiers YAML de seed et de données marché/documentaires pendant la transition.

**Documentation unique : ce fichier.** Les spécifications détaillées de l’interface V2 et l’architecture mouvements/valorisation restent dans le dossier [`docs/`](docs/) (référence technique).

---

## Sommaire

1. [Prérequis et installation](#prérequis-et-installation)
2. [Démarrage rapide](#démarrage-rapide)
3. [CLI (V2) et interface web](#cli-v2-et-interface-web)
4. [Commandes `make`](#commandes-make)
5. [Docker et déploiement serveur](#docker-et-déploiement-serveur)
6. [Architecture du code](#architecture-du-code)
7. [Données (YAML, marché, SQLite)](#données-yaml-marché-sqlite)
8. [Moteurs de valorisation](#moteurs-de-valorisation)
9. [Alertes et fraîcheur des données](#alertes-et-fraîcheur-des-données)
10. [Quantalys et Playwright](#quantalys-et-playwright)
11. [Règles métier patrimoine](#règles-métier-patrimoine)
12. [Tests et qualité](#tests-et-qualité)
13. [Documentation technique (`docs/`)](#documentation-technique-docs)
14. [GED, snapshots, mouvements et arbitrages](#ged-snapshots-mouvements-et-arbitrages)
15. [Hors périmètre et avertissement](#hors-périmètre-et-avertissement)

---

## Prérequis et installation

- Python ≥ 3.11 recommandé (le dépôt cible 3.9+ pour compatibilité ; le `Dockerfile` utilise 3.12).
- Git

Recommandé : environnement virtuel géré par le Makefile.

```bash
git clone <URL> finance-perso
cd finance-perso
make install
```

Cela crée `.venv/`, installe le package en mode éditable avec les dépendances de développement (`pytest`, etc.).

Alternative :

```bash
pip install -e ".[dev]"
```

Point d’entrée console : `portfolio-tracker` (voir `setup.py`) → `portfolio_tracker.cli:main`.  
Module équivalent : `python -m portfolio_tracker.cli`.

---

## Démarrage rapide

```bash
make demo
# ou
./run_example.sh
```

Puis ouvrir l’interface web locale :

```bash
make web
# http://127.0.0.1:8765
```

Aperçu terminal (synthèse V2) :

```bash
make global
make structured
```

---

## CLI et interface web

Sous-commandes disponibles :

| Commande | Rôle |
|----------|------|
| `global` / `status` | Synthèse contrats / valeur / documents (sortie texte) |
| `structured` | Tableau produits structurés |
| `web-payload` | JSON pour consommation par la vue web |
| `bootstrap` | JSON du bootstrap données → SQLite V2 |
| `web` | Serveur web local (`--host`, `--port`) |
| `update-uc-navs` | Mise à jour des VL UC (options : `--target-date`, `--set`, `--headless`, …) |
| `update-underlyings` | Mise à jour des séries de sous-jacents |
| `backfill-market-history` | Remplissage complet de l’historique marché disponible (ou fenêtre via `--years`) |
| `manual-movement-add` | Ajoute une intervention manuelle en base pour un cas PDF incomplet |
| `manual-movement-list` | Liste les interventions manuelles enregistrées en base |
| `manual-movement-delete` | Supprime une intervention manuelle par identifiant |
| `pdf-contract-audit` | Audit JSON `PDF only` d’un contrat (snapshots, positions visibles, opérations visibles, corrections manuelles) |

Exemples :

```bash
portfolio-tracker --data-dir portfolio_tracker/data global
portfolio-tracker --data-dir portfolio_tracker/data web --port 8765
python -m portfolio_tracker.cli --data-dir portfolio_tracker/data web-payload
portfolio-tracker --data-dir portfolio_tracker/data manual-movement-list --contract "SwissLife Capi Stratégic Premium"
portfolio-tracker --data-dir portfolio_tracker/data pdf-contract-audit --contract HIMALIA --year 2025
```

La vue web principale est servie par `portfolio_tracker/web/` (fichiers statiques). Endpoints utiles : `POST /api/documents/upload` (GED), `GET/POST` sur arbitrage, validations et marché (voir code dans `portfolio_tracker/web/app.py`).

---

## Commandes `make`

| Cible | Effet |
|-------|--------|
| `help` | Aide |
| `install` / `setup` | Installation dev dans `.venv` |
| `global` | `global` CLI |
| `structured` | `structured` CLI |
| `web` | lance `web` |
| `web-payload` | JSON dashboard |
| `bootstrap` | JSON bootstrap |
| `update-navs` | `update-uc-navs` |
| `update-underlyings` | `update-underlyings` |
| `backfill-market-history` | `backfill-market-history` (`HEADLESS=1`, `YEARS=3` optionnel sinon historique complet) |
| `manual-movement-list` | liste les interventions manuelles stockées en base |
| `manual-movement-add` | ajoute une intervention manuelle en base |
| `manual-movement-delete` | supprime une intervention manuelle en base |
| `pdf-contract-audit` | audit PDF d’un contrat depuis SQLite (`CONTRACT=...`, `YEAR=...`) |
| `demo` | `run_example.sh` |
| `docker-build` | image Docker |
| `docker-run` | `make docker-run ARGS='global'` |
| `docker-global` | `global` dans le conteneur |
| `docker-update-navs` | `update-uc-navs` dans le conteneur |
| `test` / `test-cov` | `pytest` |
| `clean` | caches Python |

Les interventions manuelles servent uniquement pour les exceptions où les PDF ne suffisent pas à reconstruire un mouvement. Elles sont stockées en SQLite puis réinjectées dans le ledger V2 au prochain `bootstrap`.

La commande `pdf-contract-audit` sert de point d’entrée d’audit `PDF only` côté terminal: elle relit en base les snapshots, lignes de positions visibles et opérations visibles extraites des relevés, afin d’identifier ce qui est déjà couvert sans retourner fouiller dans les YAML legacy.

---

## Docker et déploiement serveur

Build :

```bash
make docker-build
```

Le `docker-compose.yml` monte `./portfolio_tracker/data` vers `/app/portfolio_tracker/data` et lance par défaut `web` sur le port 8765 (voir `Dockerfile`).

Exécuter une sous-commande CLI dans le conteneur :

```bash
make docker-run ARGS='global'
make docker-run ARGS='update-uc-navs'
```

**Sur un serveur Linux** (Docker + Git) : cloner le dépôt, copier `env.example` en `.env` si besoin, `make docker-build`, puis planifier par exemple `make docker-update-navs` via cron sur le répertoire du projet.

---

## Architecture du code

```
portfolio_tracker/
├── cli.py, bootstrap.py, dashboard.py, market.py, reporting.py, ...
├── domain/       # Lots, mouvements, analytics, projections
├── web/          # Application web + statiques
└── data/         # YAML, SQLite, market_data, documents
```

Détails d’implémentation V2 : [`docs/v2_implementation_plan.md`](docs/v2_implementation_plan.md) et fichiers `docs/v2_*.md`.

---

## Données (YAML, marché, SQLite)

- le catalogue d’actifs et les positions sont reconstruits en base à partir de `market_data/`, des PDF et des corrections manuelles  
- `data/market_data/` — NAV, événements structurés, taux, sous-jacents, `nav_sources.yaml`, etc.  
- Base SQLite V2 — état opérationnel V2 (`bootstrap`, snapshots, arbitrages PDF, corrections manuelles)

Exemples de fichiers marché : `nav_<asset_id>.yaml`, `events_<asset_id>.yaml`, `fonds_euro_<asset_id>.yaml`, `rates_*.yaml`, `underlying_*.yaml`.

---

## Moteurs de valorisation

| Type d’actif | Moteur | Comportement |
|--------------|--------|--------------|
| `structured_product` | `event_based` | Coupons / autocalls, pas de mark-to-market théorique |
| `fonds_euro` | `declarative` | Taux déclarés, opacité acceptée |
| `uc_fund` | `mark_to_market` | VL × parts |
| `uc_illiquid` | `hybrid` | VL si dispo, sinon estimation |

---

## Alertes et fraîcheur des données

La logique d’alertes (données obsolètes, observations structurés, etc.) est portée par les services V2 et l’UI ; il n’y a plus de sous-commande `alerts` dédiée sur la CLI réduite. Vérifier les indicateurs dans l’interface web et les exports `web-payload`.

---

## Quantalys et Playwright

Les notations Quantalys (étoiles) et catégories peuvent être enrichies lors des mises à jour de VL. **Playwright** (navigateur headless) est souvent nécessaire car le site Quantalys s’appuie sur du JavaScript.

Installation rapide :

```bash
./QUICK_INSTALL_QUANTALYS.sh
```

Ou manuellement :

```bash
pip install playwright
python -m playwright install chromium
```

Puis utiliser `make update-navs` (appelle `update-uc-navs`). Sans Playwright, les notes peuvent être saisies à la main dans `portfolio_tracker/data/market_data/quantalys_ratings.yaml`.

---

## Règles métier patrimoine

*Référence métier explicite — à maintenir avec le code et les données.*

### 1. Capital investi

Le capital investi correspond aux apports externes réels injectés sur les contrats.

- On raisonne d’abord au niveau **contrat**, pas au niveau position.
- Un apport externe est un versement provenant de l’extérieur du contrat.
- Un arbitrage interne ne crée pas de nouveau capital investi.
- Reinvestissement, coupon recrédité, participation aux bénéfices ou passage par un support monétaire ne doivent pas être comptés comme nouvel apport externe.

Affichage visé : apports externes cumulés ; éventuellement apports nets si l’on soustrait les rachats réellement sortis ; « coût net encore exposé » seulement si la notion est définie séparément.

### 2. Valeur à date

**Fonds euro :** figer les positions à partir des rapports annuels ; base au 1er janvier = valeur connue au 31/12 de l’année précédente ; valorisation combine capital au 1er janvier, taux de l’année précédente et plus-value théorique à date. On ne prétend pas recalculer parfaitement le fonds euro ; on s’aligne sur les relevés assureur.

**UC :** valeur = parts × VL de marché à date ; dernière VL disponible ≤ date de valorisation ; signaler si la VL est trop ancienne.

**Produits structurés :** pas de reconstruction d’une VL opaque assureur ; partir de la valeur d’achat, ajouter les coupons théoriques ; pilotage : produit « gagnant » tant qu’il n’y a pas d’information contraire. Cas CMS 10 ans : ne pas supposer les coupons sans validation ; pouvoir déclarer explicitement versement ou non (à croiser avec documentation produit et relevés).

### 3. Rapports assureur

Source de vérité pour figer positions à une date : rapports annuels (fonds euro au 31/12, mouvements, coupons, état des structurés).

### 4. Principes d’implémentation

- Séparer apports externes, arbitrages internes, revenus/coupons, frais/taxes, valeur à date.
- Ne pas sommer des « premiers achats de position » comme apports externes.
- Privilégier le niveau contrat pour les apports, le niveau position pour la valorisation.
- Tracer la source des valeurs (rapport, brochure, fichier marché, saisie manuelle).

### 5. Questions ouvertes

Formule intrannuelle fonds euro ; convention exacte des coupons CMS ; distinction fine entre apports cumulés, apports nets, coût exposé, valeur actuelle.

---

## Tests et qualité

```bash
make test
make test-cov
```

La validation de cohérence métier est couverte par les services V2 et la suite de tests.

---

## Documentation technique (`docs/`)

| Fichier | Contenu |
|---------|---------|
| [`docs/movements_valuation_architecture.md`](docs/movements_valuation_architecture.md) | Architecture mouvements / valorisation |
| [`docs/v2_functional_spec.md`](docs/v2_functional_spec.md) | Spec fonctionnelle V2 |
| [`docs/v2_data_spec.md`](docs/v2_data_spec.md) | Modèle de données V2 |
| [`docs/v2_screens_spec.md`](docs/v2_screens_spec.md) | Écrans |
| [`docs/v2_workflows_spec.md`](docs/v2_workflows_spec.md) | Workflows |
| [`docs/v2_global_spec.md`](docs/v2_global_spec.md) | Vue globale |
| [`docs/v2_implementation_plan.md`](docs/v2_implementation_plan.md) | Plan d’implémentation |
| [`docs/v2_wireframes.md`](docs/v2_wireframes.md) | Wireframes |
| [`docs/history_chart_options.md`](docs/history_chart_options.md) | Options de graphiques (historique) |

Les specs V2 référencent les **règles métier** ci-dessus (section [Règles métier patrimoine](#règles-métier-patrimoine)).

---

## GED, snapshots, mouvements et arbitrages

### Chaîne métier cible

1. **Relevé de situation annuel** : vérité de référence à la date du snapshot (import PDF → table `annual_snapshots`, statut `proposed` puis **validation** `validated` / `rejected`).
2. **Arbitrages PDF et corrections manuelles** : complètent l’historique en base SQLite avec une provenance explicite par document ou par saisie manuelle.
3. **Régularisation** : en fin d’année, rapprochement entre total officiel et reconstruction interne ; les écritures de régul restent à valider manuellement avant application durable.

### Produits structurés : deux lectures

- **Officiel assureur** : montants issus des relevés importés (`official_*` dans le dashboard). Pour les structurés, la part officielle peut être dérivée du total lorsque le PDF fournit total, UC et fonds euro.
- **Modèle interne** : valorisation moteur à la date du snapshot (`model_structured_value`, écarts `structured_model_gap_*`). Les deux sont affichées dans l’UI (cartes contrats, table snapshots, pages contrat / support).

Sur une **transaction** assureur (arbitrage PDF), les montants / unités issus du document font foi ; les mouvements persistés portent une provenance SQLite et les lots runtime gardent `source` / `model_anchor` pour tracer l’alignement modèle.

### Arbitrages PDF

- Classification `arbitration_letter` ; extraction Generali et SwissLife (`portfolio_tracker/arbitration.py`).
- Proposition persistée en base ; **mapping manuel** des jambes si l’ISIN ne correspond à aucune position.
- Application : persistance des mouvements issus du PDF en base SQLite puis `bootstrap`.

### Réinitialiser la base V2

```bash
rm -f portfolio_tracker/data/.portfolio_tracker_v2.sqlite portfolio_tracker/data/.portfolio_tracker_v2.sqlite-*
portfolio-tracker --data-dir portfolio_tracker/data bootstrap
```

---

## Hors périmètre et avertissement

L’outil ne fait pas : trading automatique, conseil en investissement automatisé, recalcul « exact » des fonds euros, fiscalité avancée, scraping des espaces assureurs, API temps réel.

**Avertissement :** outil informatif ; les valorisations sont indicatives ; vérifier systématiquement sur les relevés officiels.

---

## Licence

Usage personnel.
