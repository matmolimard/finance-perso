# 🚀 Démarrage Rapide

## Installation en 2 minutes

```bash
# 1. Installer les dépendances
pip install -r requirements.txt

# 2. Tester l'installation avec les données d'exemple
python3 -m portfolio_tracker.cli --data-dir portfolio_tracker/data global
```

## Sortie Attendue

```
======================================================================
PORTFOLIO TRACKER - État Global
======================================================================

📊 6 actifs, 6 positions

💰 Valeur totale: 421,275.00 €
📈 Capital investi: 405,000.00 €
📈 P&L: 16,275.00 € (+4.02%)

✓ 6 positions valorisées
```

## Utiliser Vos Propres Données

### 1. Créer votre dossier de données

```bash
mkdir -p data/market_data
```

### 2. Copier les exemples comme point de départ

```bash
cp portfolio_tracker/data/assets.yaml data/
cp portfolio_tracker/data/positions.yaml data/
```

### 3. Éditer avec vos données

Éditer `data/assets.yaml` et `data/positions.yaml` avec vos actifs et positions réels.
Ensuite, les commandes métier alimenteront automatiquement `data/.portfolio_tracker.sqlite` et réexporteront `positions.yaml`.
Si vous retouchez manuellement les lots dans `positions.yaml`, relancez `python3 -m portfolio_tracker.cli rebuild-ledger`.

### 4. Ajouter les données de marché

Créer les fichiers dans `data/market_data/` selon vos besoins :
- `nav_[asset_id].yaml` pour les VL
- `fonds_euro_[asset_id].yaml` pour les taux déclarés
- `events_[asset_id].yaml` pour les événements
- `rates_[identifier].yaml` pour les sous-jacents

### 5. Lancer

```bash
python3 -m portfolio_tracker.cli global
```

## Commandes Utiles

```bash
# Vue d'ensemble
python3 -m portfolio_tracker.cli global

# Alias compatible
python3 -m portfolio_tracker.cli status

# Par type d'actif
python3 -m portfolio_tracker.cli type

# Vues spécialisées
python3 -m portfolio_tracker.cli uc
python3 -m portfolio_tracker.cli structured
python3 -m portfolio_tracker.cli fonds-euro

# Alertes
python3 -m portfolio_tracker.cli alerts

# Liste des actifs
python3 -m portfolio_tracker.cli list-assets

# Liste des positions
python3 -m portfolio_tracker.cli list-positions
```

## Structure Minimale

Voici la structure minimale pour démarrer :

```
data/
├── assets.yaml          # Vos actifs
├── positions.yaml       # Snapshot lisible des positions
├── .portfolio_tracker.sqlite  # Ledger local des mouvements
└── market_data/         # Données de marché
    ├── nav_*.yaml      # VL pour UC cotées
    ├── fonds_euro_*.yaml  # Taux pour fonds euros
    └── ...
```

## Exemple Minimal

### data/assets.yaml

```yaml
assets:
  - asset_id: mon_fonds
    type: uc_fund
    name: "Mon Fonds Actions"
    isin: "FR0123456789"
    valuation_engine: mark_to_market
```

### data/positions.yaml

```yaml
positions:
  - position_id: pos_001
    asset_id: mon_fonds
    holder_type: individual
    wrapper:
      type: assurance_vie
      insurer: "Mon Assureur"
      contract_name: "Mon Contrat"
    investment:
      subscription_date: "2024-01-01"
      invested_amount: 10000.0
      units_held: 100.0
```

### data/market_data/nav_mon_fonds.yaml

```yaml
nav_history:
  - date: "2024-01-01"
    value: 100.0
    currency: EUR
  - date: "2024-12-24"
    value: 105.0
    currency: EUR
```

## Et Ensuite ?

Consultez :
- [README.md](README.md) pour la documentation complète
- [USAGE.md](USAGE.md) pour les scénarios d'usage détaillés

## Support

En cas de problème, vérifiez :
1. Version de Python : `python3 --version` (doit être ≥ 3.11)
2. Dépendances installées : `pip list | grep -E "pyyaml|pandas"`
3. Structure des fichiers YAML (indentation, syntaxe)









