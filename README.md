# Portfolio Tracker

Outil Python de suivi patrimonial multi-actifs, orienté long terme, sans logique de trading.

## 🎯 Objectif

Suivre des actifs financiers détenus en **assurance vie** et **contrat de capitalisation**, avec un focus particulier sur :
- Produits structurés
- Fonds euros (opaques)
- Unités de compte cotées ou peu liquides

L'outil est **indépendant des assureurs**, lisible, extensible, et repose sur un stockage **100% local et gratuit** :
- des fichiers YAML versionnables pour la configuration et les snapshots lisibles
- un ledger SQLite local pour les mouvements normalisés et les projections métier

## 📋 Prérequis

- Python ≥ 3.11
- pip

## 🚀 Installation

```bash
# Cloner le projet
cd finance-perso

# Installer les dépendances
pip install -r requirements.txt
```

## 🐳 Exécution avec Docker

```bash
# Build
make docker-build

# Vue globale
make docker-global

# Historique d'une valeur
make docker-history VALUE=bdl_rempart
```

Guide complet serveur: `DEPLOY_SERVER.md`

Architecture mouvements / valorisation renforcée: `docs/movements_valuation_architecture.md`

## 🏗️ Architecture

```
portfolio_tracker/
├── data/                       # Données locales
│   ├── assets.yaml            # Définition des actifs
│   ├── positions.yaml         # Snapshot lisible/exporté des positions
│   ├── .portfolio_tracker.sqlite  # Ledger SQLite local des mouvements
│   └── market_data/           # Données de marché horodatées
│       ├── nav_*.yaml         # Valeurs liquidatives
│       ├── rates_*.yaml       # Taux et indices
│       ├── fonds_euro_*.yaml  # Taux déclarés fonds euros
│       └── events_*.yaml      # Événements produits structurés
│
├── core/                       # Classes de base et chargement
│   ├── asset.py               # Définition des actifs
│   ├── position.py            # Détention des actifs
│   └── portfolio.py           # Gestion du portefeuille
│
├── domain/                     # Coeur métier mouvements / projections / ledger
│   ├── movements.py           # Normalisation des mouvements
│   ├── projection.py          # Projection économique d'une position
│   ├── analytics.py           # Calculs métier partagés
│   └── ledger.py              # Ledger SQLite local
│
├── valuation/                  # Moteurs de valorisation
│   ├── base.py                # Interface commune
│   ├── event_based.py         # Produits structurés
│   ├── declarative.py         # Fonds euros
│   ├── mark_to_market.py      # UC cotées
│   └── hybrid.py              # UC illiquides
│
├── market/                     # Données de marché
│   ├── providers.py           # Interface fournisseurs
│   ├── rates.py               # Gestion des taux
│   └── nav.py                 # Gestion des VL
│
├── alerts/                     # Système d'alertes
│   ├── rules.py               # Règles d'alerte
│   └── notifier.py            # Notifications
│
├── cli.py                      # Interface ligne de commande
└── main.py                     # Point d'entrée principal
```

## 💡 Concepts Clés

### Séparation Asset / Position

**Asset** = Définition financière abstraite d'un actif (ex: un fonds, un produit structuré)

**Position** = Détention réelle de cet actif dans un contexte spécifique (enveloppe, détenteur)

➡️ Un même actif peut être détenu dans plusieurs positions.

### Moteurs de Valorisation

Chaque type d'actif utilise un moteur spécifique :

| Type d'actif | Moteur | Comportement |
|--------------|--------|--------------|
| `structured_product` | `event_based` | Identifie les événements (coupons, autocalls) sans mark-to-market |
| `fonds_euro` | `declarative` | Utilise uniquement les taux déclarés, accepte l'opacité |
| `uc_fund` | `mark_to_market` | Valorisation simple via VL × nombre de parts |
| `uc_illiquid` | `hybrid` | Mark-to-market si disponible, sinon estimation |

## 📝 Utilisation

### Interface CLI

```bash
# Vue globale du portefeuille
python -m portfolio_tracker.cli global

# Alias de compatibilité
python -m portfolio_tracker.cli status

# Payload JSON pour une future web view
python -m portfolio_tracker.cli web-payload
python -m portfolio_tracker.cli web-payload --output /tmp/portfolio-web.json

# Petite app web locale
python -m portfolio_tracker.cli web
python -m portfolio_tracker.cli web --host 127.0.0.1 --port 8765

# État par type d'actif
python -m portfolio_tracker.cli type
python -m portfolio_tracker.cli type --type structured_product

# Vues spécialisées
python -m portfolio_tracker.cli uc
python -m portfolio_tracker.cli structured
python -m portfolio_tracker.cli fonds-euro

# Vérifier les alertes
python -m portfolio_tracker.cli alerts
python -m portfolio_tracker.cli alerts --severity warning

# Lister les actifs
python -m portfolio_tracker.cli list-assets

# Lister les positions
python -m portfolio_tracker.cli list-positions
```

### Utilisation programmatique

```python
from pathlib import Path
from portfolio_tracker.core import Portfolio
from portfolio_tracker.valuation import MarkToMarketEngine

# Charger le portefeuille
portfolio = Portfolio(Path("data"))

# Récupérer un actif et ses positions
asset = portfolio.get_asset("uc_msci_world")
positions = portfolio.get_positions_by_asset("uc_msci_world")

# Valoriser une position
engine = MarkToMarketEngine(Path("data"))
result = engine.valuate(asset, positions[0])

print(f"Valeur: {result.current_value} €")
print(f"P&L: {result.unrealized_pnl} €")
```

## 📊 Format des Données

### assets.yaml

Définit les actifs financiers :

```yaml
assets:
  - asset_id: uc_msci_world
    type: uc_fund
    name: "Amundi MSCI World UCITS ETF"
    isin: "LU1681043599"
    valuation_engine: mark_to_market
    metadata:
      fund_type: "etf"
      geographic_zone: "world"
      asset_class: "equity"
```

### positions.yaml

Définit les positions détenues et sert de snapshot lisible/exportable :

```yaml
positions:
  - position_id: pos_003
    asset_id: uc_msci_world
    holder_type: individual
    wrapper:
      type: assurance_vie
      insurer: "Assureur A"
      contract_name: "Contrat Patrimoine Plus"
    investment:
      subscription_date: "2022-06-15"
      invested_amount: 30000.0
      units_held: 750.0
```

### `.portfolio_tracker.sqlite`

Le ledger SQLite local stocke les mouvements normalisés et sert de source prioritaire dès qu'il existe.

- Les commandes métier écrivent d'abord dans SQLite
- `positions.yaml` est ensuite exporté comme snapshot versionnable
- `Portfolio(...)` recharge automatiquement les lots depuis SQLite s'il est déjà présent
- Si vous modifiez manuellement `positions.yaml`, utilisez `python -m portfolio_tracker.cli rebuild-ledger`

### market_data/

Données de marché horodatées :

**nav_[asset_id].yaml** - Valeurs liquidatives :
```yaml
nav_history:
  - date: "2024-12-20"
    value: 51.8
    currency: EUR
```

**fonds_euro_[asset_id].yaml** - Taux déclarés :
```yaml
declared_rates:
  - year: 2024
    rate: 2.70
    source: "Lettre aux assurés 2024"
    date: "2024-11-30"
```

**events_[asset_id].yaml** - Événements produits structurés :
```yaml
events:
  - type: coupon
    date: "2024-07-20"
    amount: 2125.0
    description: "Coupon semestriel #1"
```

**rates_[identifier].yaml** - Taux et indices :
```yaml
history:
  - date: "2024-12-20"
    value: 4950.0
```

## 🔔 Alertes

Le système inclut des règles d'alerte configurables :

- **DataFreshnessRule** : Alerte si les données de marché sont trop anciennes
- **StructuredProductObservationRule** : Alerte avant une date d'observation
- **UnderlyingThresholdRule** : Alerte si un sous-jacent approche d'un seuil
- **MissingValuationRule** : Alerte si une position ne peut être valorisée

Les alertes peuvent être affichées en console, écrites dans un log, ou envoyées par email.

## ⭐ Notes Quantalys (mise à jour automatique)

Les fonds UC (Unités de Compte) sont automatiquement enrichis avec leurs notes Quantalys (de 1 à 5 étoiles) dans les sorties des commandes `make swisslife` et `make himalia`.

### Exemple d'affichage

```
✓ Eleva Absolute Return Europe | Quantalys: ⭐⭐⭐⭐ (4/5)
  Valeur: 6,709.17 € | Investi: 6,483.58 € | P&L: +225.59 € (+3.48%)
```

### Récupération automatique

Les notes Quantalys sont **récupérées automatiquement** lors de la mise à jour des VL :

```bash
make update-navs
```

Cette commande récupère :
- ✅ Les valeurs liquidatives des fonds
- ✅ **Les notes Quantalys (1-5 étoiles)**
- ✅ **Les catégories de fonds**

Tout est automatiquement sauvegardé dans `portfolio_tracker/data/market_data/quantalys_ratings.yaml`.

### Prérequis pour la récupération automatique

**Important** : Playwright doit être installé pour récupérer automatiquement les notes depuis Quantalys.

#### Installation rapide (recommandé)

```bash
./QUICK_INSTALL_QUANTALYS.sh
```

#### Installation manuelle

```bash
pip install playwright
python -m playwright install chromium
```

#### Sans Playwright

Si Playwright n'est pas installé :
- ❌ `make update-navs` ne pourra pas récupérer les notes automatiquement
- ✅ Les notes peuvent être saisies manuellement dans `quantalys_ratings.yaml`
- ✅ `make himalia` et `make swisslife` fonctionnent normalement

📖 Documentation complète : voir [QUANTALYS.md](QUANTALYS.md) et [INSTALLATION_PLAYWRIGHT.md](INSTALLATION_PLAYWRIGHT.md)

## 🎨 Choix de Design

### Pourquoi YAML ?

- ✅ Lisible par un humain
- ✅ Versionnable avec Git
- ✅ Commentaires possibles
- ✅ Structure hiérarchique claire
- ✅ Pas de base de données à maintenir

### Pourquoi pas de trading ?

Ce n'est **pas** un outil de trading actif, mais un **outil de suivi patrimonial long terme**. Les cas d'usage sont :
- Suivre la performance d'actifs détenus sur plusieurs années
- Identifier les dates importantes (observations, échéances)
- Gérer l'opacité des fonds euros
- Avoir une vision consolidée multi-enveloppes

### Acceptation de l'opacité des fonds euros

Les fonds euros sont **opaques par nature**. L'outil :
- ❌ Ne tente **pas** de recalculer les rendements
- ✅ Stocke les taux déclarés avec leur source
- ✅ Marque explicitement les rendements inconnus
- ✅ N'extrapole **jamais**

### Pas de scraping assureur

L'outil ne scrappe **pas** les sites des assureurs pour :
- Éviter la fragilité (changements de site)
- Respecter les CGU
- Garder le contrôle sur les données
- Permettre la vérification manuelle

Les données sont **saisies manuellement** à partir des relevés officiels.

## 🔧 Extensibilité

### Ajouter un nouveau type d'actif

1. Définir le type dans `AssetType` (`core/asset.py`)
2. Créer un nouveau moteur héritant de `BaseValuationEngine`
3. Implémenter la méthode `valuate()`
4. Référencer le moteur dans `ValuationEngine`

### Ajouter une nouvelle règle d'alerte

1. Créer une classe héritant de `AlertRule`
2. Implémenter la méthode `check()`
3. L'ajouter dans `AlertManager.add_default_rules()` si pertinent

### Ajouter un nouveau provider de données

1. Créer une classe héritant de `MarketDataProvider`
2. Implémenter les méthodes abstraites
3. L'utiliser dans les moteurs de valorisation

## 🚫 Hors Périmètre

Ce que l'outil ne fait **PAS** :

- ❌ Trading automatique
- ❌ Recommandations d'investissement
- ❌ Recalcul des fonds euros
- ❌ Simulation fiscale avancée (IFI, plus-values)
- ❌ Scraping des sites assureurs
- ❌ API en temps réel

## 🧪 Tests

```bash
# Lancer les tests
pytest

# Avec couverture
pytest --cov=portfolio_tracker --cov-report=html
```

## 📦 Exemple de Flux

1. **Saisie** : Ajouter un actif dans `assets.yaml`
2. **Saisie** : Ajouter une position initiale dans `positions.yaml`
3. **Mise à jour** : Mettre à jour les données de marché dans `market_data/`
4. **Consultation** : Lancer `python -m portfolio_tracker.cli global`
5. **Alertes** : Vérifier `python -m portfolio_tracker.cli alerts`

## 🤝 Contribution

Le projet est conçu pour être **lisible et maintenable**. Les contributions sont les bienvenues pour :
- Nouveaux moteurs de valorisation
- Nouvelles règles d'alerte
- Nouveaux providers de données
- Améliorations de la CLI
- Documentation

## 📄 Licence

Ce projet est un outil personnel de gestion patrimoniale.

## ⚠️ Avertissement

Cet outil est fourni à titre informatif. Il ne constitue en aucun cas un conseil en investissement. Les valorisations sont indicatives et peuvent contenir des erreurs. Vérifiez toujours vos positions avec vos relevés officiels.
