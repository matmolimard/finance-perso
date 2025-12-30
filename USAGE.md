# Guide d'Utilisation

## Démarrage Rapide

### 1. Vérifier l'installation

```bash
python -m portfolio_tracker.cli --help
```

### 2. Consulter l'état global

```bash
python -m portfolio_tracker.cli status
```

Sortie attendue :
```
======================================================================
PORTFOLIO TRACKER - État Global
======================================================================

📊 6 actifs, 6 positions

💰 Valeur totale: 415,500.00 €
📈 Capital investi: 405,000.00 €
📈 P&L: +10,500.00 € (+2.59%)

✓ 6 positions valorisées
```

### 3. Consulter par enveloppe

```bash
# Toutes les enveloppes
python -m portfolio_tracker.cli wrapper

# Uniquement assurance vie
python -m portfolio_tracker.cli wrapper --type assurance_vie

# Uniquement contrat de capitalisation
python -m portfolio_tracker.cli wrapper --type contrat_de_capitalisation
```

### 4. Consulter par type d'actif

```bash
# Tous les types
python -m portfolio_tracker.cli type

# Uniquement produits structurés
python -m portfolio_tracker.cli type --type structured_product

# Uniquement fonds euros
python -m portfolio_tracker.cli type --type fonds_euro

# Uniquement UC cotées
python -m portfolio_tracker.cli type --type uc_fund

# Uniquement UC illiquides
python -m portfolio_tracker.cli type --type uc_illiquid
```

### 5. Vérifier les alertes

```bash
# Toutes les alertes
python -m portfolio_tracker.cli alerts

# Uniquement les erreurs
python -m portfolio_tracker.cli alerts --severity error

# Uniquement les warnings
python -m portfolio_tracker.cli alerts --severity warning

# Uniquement les infos
python -m portfolio_tracker.cli alerts --severity info
```

### 6. Mettre à jour les séries des sous-jacents (produits structurés)

Le projet peut stocker des séries temporelles de sous-jacents (indices) dans `portfolio_tracker/data/market_data/`.

- **Configuration**: `portfolio_tracker/data/market_data/underlyings.yaml`
- **Stockage**: un fichier par sous-jacent: `underlying_<underlying_id_sanitized>.yaml`

Commande:

```bash
python -m portfolio_tracker.cli --data-dir portfolio_tracker/data update-underlyings
```

Option headless (si certaines pages nécessitent du JavaScript):

```bash
python -m portfolio_tracker.cli --data-dir portfolio_tracker/data update-underlyings --headless
```

Pour activer le headless, installer Playwright (optionnel):

```bash
pip install playwright
python -m playwright install chromium
```

### 6quater. Tâche quotidienne - enregistrer la VL du jour des UC

Objectif:
- **Stocker la VL d'achat** sur la position (`investment.purchase_nav`) quand c'est possible (déduit de `invested_amount / units_held` si manquant)
- **Stocker un point de VL par jour** dans `portfolio_tracker/data/market_data/nav_<asset_id>.yaml`

Commande (manuel):

```bash
python3 -m portfolio_tracker.cli --data-dir portfolio_tracker/data update-uc-navs \
  --date 2025-12-27 \
  --set uc_eleva_absolute_return_europe=153.58 \
  --set uc_helium_selection_b_eur=1742.12
```

Notes:
- `--set` est **répétable** (une fois par UC).
- La commande initialise aussi (si possible) un point à la **date de souscription** avec la VL d'achat (`source: purchase_nav`).

#### Mode automatique (sans `--set`)

Tu peux configurer des sources web dans `portfolio_tracker/data/market_data/nav_sources.yaml` :

```yaml
nav_sources:
  uc_eleva_absolute_return_europe:
    kind: url_json
    url: "https://example.com/api/nav"
    value_path: "nav.value"
    date_path: "nav.date"     # optionnel
    currency: "EUR"           # optionnel

  uc_helium_selection_b_eur:
    kind: url_csv
    url: "https://example.com/nav.csv"
    delimiter: ";"
    date_column: "date"
    value_column: "value"
    date_format: "%Y-%m-%d"   # optionnel
    decimal: "."              # "." ou ","

  uc_bdl_rempart_c:
    kind: html_regex
    url: "https://example.com/page.html"
    value_regex: "VL\\s*:\\s*([0-9]+[\\.,][0-9]+)"
    date_regex: "Date\\s*:\\s*([0-9]{4}-[0-9]{2}-[0-9]{2})"  # optionnel
    headless: false           # optionnel (Playwright)
```

Ensuite tu peux lancer:

```bash
python3 -m portfolio_tracker.cli --data-dir portfolio_tracker/data update-uc-navs
```

### 6quinquies. Achats multiples UC + frais (PRU net par enveloppe)

Quand tu achètes plusieurs fois la même UC au fil du temps (versements / arbitrages), il faut raisonner par **position** :
- une **position** = une enveloppe (assurance vie / contrat de capi) + un détenteur
- un **asset** peut exister dans plusieurs positions (donc plusieurs enveloppes)

Pour gérer correctement le **PRU** et les **frais**, tu peux enregistrer des **lots d’achat** sur la position (date, quantité, cours, montant net, frais).

Commande:

```bash
python3 -m portfolio_tracker.cli --data-dir portfolio_tracker/data add-uc-lot \
  --position-id pos_011 \
  --date 2025-09-04 \
  --units 43.7946 \
  --nav 148.42 \
  --net-amount 6500.00
```

Champs:
- `--net-amount`: **montant net investi** (après frais) — recommandé
- `--fees-amount`: frais en € (optionnel, si tu as le détail)
- `--gross-amount`: montant brut (optionnel)

Ensuite:
- la vue `uc` calcule la **VL d'achat (PRU net)** à partir des lots et affiche la mention **(lots)**.
- la performance "depuis achat" est calculée vs ce PRU net.

### 6sexies. Import régulier des mouvements (Generali/Swiss Life)

Tu peux importer régulièrement les mouvements depuis les exports texte de tes assureurs ou depuis MoneyPitch. La commande détecte automatiquement les doublons et n'ajoute que les nouveaux lots.

**Format supporté :** Le parser accepte les exports texte au format Generali/Swiss Life (lignes "TYPE - dd/mm/yyyy" suivies de blocs ISIN/Nom/Quantité/Cours/Montant net).

#### Import complet (première fois)

```bash
# Generali HIMALIA (export depuis l'assureur)
python3 -m portfolio_tracker.cli --data-dir portfolio_tracker/data import-movements \
  --file portfolio_tracker/data/mouvements_himalia.txt \
  --insurer Generali \
  --contract HIMALIA \
  --all-assets \
  --apply

# Swiss Life (export depuis MoneyPitch)
python3 -m portfolio_tracker.cli --data-dir portfolio_tracker/data import-movements \
  --file portfolio_tracker/data/mouvements_swisslife.txt \
  --insurer "Swiss Life" \
  --contract "SwissLife Capi Stratégic Premium" \
  --all-assets \
  --apply
```

#### Import incrémental (régulier)

Pour importer uniquement les nouveaux mouvements depuis une date donnée :

```bash
# Generali HIMALIA - nouveaux mouvements depuis le 1er décembre 2025
python3 -m portfolio_tracker.cli --data-dir portfolio_tracker/data import-movements \
  --file portfolio_tracker/data/mouvements_himalia.txt \
  --insurer Generali \
  --contract HIMALIA \
  --since 2025-12-01 \
  --all-assets \
  --apply

# Swiss Life - nouveaux mouvements depuis le 1er décembre 2025 (depuis MoneyPitch)
python3 -m portfolio_tracker.cli --data-dir portfolio_tracker/data import-movements \
  --file portfolio_tracker/data/mouvements_swisslife.txt \
  --insurer "Swiss Life" \
  --contract "SwissLife Capi Stratégic Premium" \
  --since 2025-12-01 \
  --all-assets \
  --apply
```

**Avantages :**
- ✅ **Détection automatique des doublons** : les lots déjà importés sont ignorés
- ✅ **Import incrémental** : option `--since` pour n'importer que les nouveaux mouvements
- ✅ **Dry-run par défaut** : teste d'abord sans modifier `positions.yaml` (ajouter `--apply` pour appliquer)
- ✅ **Tous types d'actifs** : `--all-assets` pour importer UC, structurés, fonds euro
- ✅ **Recalcul automatique** : `units_held` est recalculé à partir de la somme des lots
- ✅ **Multi-wrappers** : fonctionne pour Generali HIMALIA, Swiss Life, et tout autre wrapper

**Workflow recommandé :**

1. **Récupérer les mouvements** :
   - Generali HIMALIA : export depuis l'espace client Generali
   - Swiss Life : export depuis MoneyPitch (copier/coller dans le fichier texte)

2. **Copier/coller les nouveaux mouvements** dans le fichier texte correspondant :
   - `portfolio_tracker/data/mouvements_himalia.txt` (Generali)
   - `portfolio_tracker/data/mouvements_swisslife.txt` (Swiss Life)

3. **Lancer en dry-run** pour vérifier :
   ```bash
   python3 -m portfolio_tracker.cli --data-dir portfolio_tracker/data import-movements \
     --file portfolio_tracker/data/mouvements_swisslife.txt \
     --insurer "Swiss Life" \
     --contract "SwissLife Capi Stratégic Premium" \
     --all-assets
   ```

4. **Si OK, relancer avec `--apply`** pour importer :
   ```bash
   python3 -m portfolio_tracker.cli --data-dir portfolio_tracker/data import-movements \
     --file portfolio_tracker/data/mouvements_swisslife.txt \
     --insurer "Swiss Life" \
     --contract "SwissLife Capi Stratégic Premium" \
     --all-assets \
     --apply
   ```

5. **Vérifier** avec `status` ou `wrapper`

**Note :** L'ancienne commande `import-himalia-movements` fonctionne toujours (alias pour compatibilité). La commande `import-movements` est générique et fonctionne pour tous les wrappers.

### 6bis. Ajouter/maintenir une série de taux (CMS, etc.)

Les produits structurés de type **CMS** utilisent des séries de taux stockées dans `portfolio_tracker/data/market_data/` au format:

- `rates_<IDENTIFIER>.yaml` (ex: `rates_CMS_EUR_10Y.yaml`)

Format attendu:

```yaml
identifier: CMS_EUR_10Y
source: manual
units: pct
history:
  - date: "2025-12-24"
    value: 2.10   # en % (2.10 = 2,10%)
```

Ces taux sont lus via `RatesProvider` et permettent d’évaluer les conditions du type `CMS_EUR_10Y <= 2.20%` (notamment pour la colonne **Remb. si ajd ?** de la commande `structured`).

### 6ter. Sous-jacents “manuels” (indices non supportés par `update-underlyings`)

Certains sous-jacents (ex: indices propriétaires **MerQube**, **iEdge**) ne peuvent pas être téléchargés automatiquement avec `update-underlyings`.
Dans ce cas, on les gère **exactement comme Bouygues/Engie/ENI**: un `underlying_id` dans `assets.yaml`, et un fichier de série dans `portfolio_tracker/data/market_data/`:

- `underlying_<underlying_id_sanitized>.yaml`

Exemple (à remplacer avec tes niveaux réels):

```yaml
underlying_id: MerQube_CA_SA_0.9_Point_Decrement_EUR
source: manual
identifier: MerQube_CA_SA_0.9_Point_Decrement_EUR
history:
  - date: "2025-02-07"   # niveau initial (strike)
    value: 100.0
  - date: "2025-12-24"   # dernier niveau connu
    value: 103.2
```

Une fois ce fichier alimenté, la commande `structured` peut calculer **Remb. si ajd ?** (ex: “Index >= Initial”).

Alternative rapide (sans historique complet):

- Dans `assets.yaml`, tu peux définir une valeur du jour manuellement:
  - `metadata.underlying_current_level` (float)
  - `metadata.underlying_current_date` (optionnel, `YYYY-MM-DD`)

La commande `structured` affichera alors le sous-jacent avec la mention `(manual)` et pourra calculer **Remb. si ajd ?** tant que le **niveau initial** est connu, via au choix:

- un point d’historique à la date de `metadata.initial_observation_date`, ou
- `metadata.initial_level` (float) si tu n’as pas l’historique complet.

### 7. Vue synthèse des produits structurés

Affiche, par position de produit structuré:
- le nom
- le nombre de mois écoulés depuis la souscription
- la valeur actuelle (event-based)
- la valeur du strike du sous-jacent (si le sous-jacent est configuré et l'historique disponible)
- la prochaine date de constatation (prochain événement d'observation attendu)

```bash
python -m portfolio_tracker.cli --data-dir portfolio_tracker/data structured
```

## Ajouter un Nouvel Actif

### Étape 1 : Définir l'actif dans assets.yaml

```yaml
assets:
  - asset_id: mon_nouveau_fonds
    type: uc_fund
    name: "Mon Nouveau Fonds"
    isin: "FR0123456789"
    valuation_engine: mark_to_market
    metadata:
      fund_type: "mutual_fund"
      asset_class: "mixed"
```

### Étape 2 : Créer une position dans positions.yaml

```yaml
positions:
  - position_id: pos_007
    asset_id: mon_nouveau_fonds
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

### Étape 3 : Ajouter les données de marché

Créer `data/market_data/nav_mon_nouveau_fonds.yaml` :

```yaml
nav_history:
  - date: "2024-01-01"
    value: 100.0
    currency: EUR
  
  - date: "2024-12-20"
    value: 105.0
    currency: EUR
```

### Étape 4 : Vérifier

```bash
python -m portfolio_tracker.cli status
```

## Mise à Jour des Données de Marché

### Fonds Euros

Mettre à jour `data/market_data/fonds_euro_[asset_id].yaml` :

```yaml
declared_rates:
  - year: 2024
    rate: 2.70
    source: "Lettre aux assurés 2024"
    date: "2024-11-30"
```

### UC Cotées

Ajouter une nouvelle VL dans `data/market_data/nav_[asset_id].yaml` :

```yaml
nav_history:
  - date: "2024-12-24"
    value: 52.0
    currency: EUR
```

### Produits Structurés

#### Mettre à jour le sous-jacent

Dans `data/market_data/rates_[underlying].yaml` :

```yaml
history:
  - date: "2024-12-24"
    value: 5000.0
```

#### Enregistrer un événement

Dans `data/market_data/events_[asset_id].yaml` :

```yaml
events:
  - type: coupon
    date: "2024-12-20"
    amount: 2125.0
    description: "Coupon semestriel #2"
    metadata:
      period: 2
```

## Scénarios d'Usage

### Scénario 1 : Bilan Mensuel

```bash
# 1. Mettre à jour les données de marché (VL récentes)
# 2. Consulter l'état global
python -m portfolio_tracker.cli status

# 3. Vérifier les alertes
python -m portfolio_tracker.cli alerts
```

### Scénario 2 : Suivi Produit Structuré

```bash
# 1. Vérifier les prochaines dates d'observation
python -m portfolio_tracker.cli alerts --severity info

# 2. Mettre à jour le niveau du sous-jacent
# Éditer data/market_data/rates_[underlying].yaml

# 3. Vérifier si proche d'un seuil
python -m portfolio_tracker.cli alerts --severity warning

# 4. Enregistrer un événement si coupon versé
# Éditer data/market_data/events_[asset_id].yaml
```

### Scénario 3 : Comparaison Assurance Vie vs Contrat de Capitalisation

```bash
# Assurance vie
python -m portfolio_tracker.cli wrapper --type assurance_vie

# Contrat de capitalisation
python -m portfolio_tracker.cli wrapper --type contrat_de_capitalisation
```

### Scénario 4 : Analyse par Classe d'Actifs

```bash
# Produits structurés
python -m portfolio_tracker.cli type --type structured_product

# Fonds euros (sécuritaire)
python -m portfolio_tracker.cli type --type fonds_euro

# Actions internationales (UC)
python -m portfolio_tracker.cli type --type uc_fund
```

## Utilisation Programmatique

### Script Personnalisé

Créer `mon_script.py` :

```python
from pathlib import Path
from portfolio_tracker.core import Portfolio
from portfolio_tracker.alerts import AlertManager, ConsoleNotifier

# Charger le portefeuille
portfolio = Portfolio(Path("data"))

# Afficher les actifs
print(f"Nombre d'actifs: {len(portfolio.assets)}")
print(f"Nombre de positions: {len(portfolio.positions)}")

# Vérifier les alertes
alert_manager = AlertManager(portfolio, Path("data/market_data"))
alert_manager.add_default_rules()
triggers = alert_manager.check_all()

# Afficher
notifier = ConsoleNotifier()
notifier.notify(triggers)
```

### Notebook Jupyter

```python
import pandas as pd
from pathlib import Path
from portfolio_tracker.core import Portfolio
from portfolio_tracker.valuation import MarkToMarketEngine

# Charger
portfolio = Portfolio(Path("../data"))

# Créer un DataFrame des positions
data = []
engine = MarkToMarketEngine(Path("../data"))

for position in portfolio.list_all_positions():
    asset = portfolio.get_asset(position.asset_id)
    result = engine.valuate(asset, position)
    
    data.append({
        'Position': position.position_id,
        'Actif': asset.name,
        'Type': asset.asset_type.value,
        'Valeur': result.current_value,
        'Investi': result.invested_amount,
        'P&L': result.unrealized_pnl,
    })

df = pd.DataFrame(data)
print(df)
```

## Automatisation

### Cron Job - Alerte Quotidienne

```bash
# Éditer crontab
crontab -e

# Ajouter (chaque jour à 9h)
0 9 * * * cd /path/to/finance-perso && python -m portfolio_tracker.cli alerts >> logs/alerts.log 2>&1
```

### Script Shell - Mise à Jour VL

Créer `scripts/update_nav.sh` :

```bash
#!/bin/bash

# Récupérer les VL depuis une source externe
# (exemple fictif - adapter à votre source)

DATE=$(date +%Y-%m-%d)

# Mettre à jour un fichier YAML
echo "  - date: \"$DATE\"" >> data/market_data/nav_uc_msci_world.yaml
echo "    value: 52.5" >> data/market_data/nav_uc_msci_world.yaml
echo "    currency: EUR" >> data/market_data/nav_uc_msci_world.yaml

# Vérifier le portefeuille
python -m portfolio_tracker.cli status
```

## Dépannage

### Erreur "FileNotFoundError: assets.yaml"

Vérifier que vous êtes dans le bon répertoire :

```bash
ls data/
# Doit afficher: assets.yaml  positions.yaml  market_data/
```

### Erreur "Asset [...] inconnu"

Vérifier que l'`asset_id` dans `positions.yaml` correspond bien à un actif défini dans `assets.yaml`.

### Valorisation à 0 ou None

Vérifier que les données de marché existent pour cet actif :

```bash
ls data/market_data/
```

Pour une UC : fichier `nav_[asset_id].yaml` doit exister.
Pour un fonds euro : fichier `fonds_euro_[asset_id].yaml` doit exister.

### Alerte "VL datée de X jours"

Mettre à jour le fichier de VL avec une valeur plus récente.

## Bonnes Pratiques

1. **Versionner les données** : Commiter régulièrement `assets.yaml`, `positions.yaml` et `market_data/` dans Git
2. **Documenter les sources** : Toujours indiquer la source dans les taux déclarés
3. **Vérifier avec les relevés** : Comparer régulièrement avec les relevés officiels de vos assureurs
4. **Sauvegarder** : Faire des backups réguliers du dossier `data/`
5. **Consulter les alertes** : Vérifier quotidiennement ou hebdomadairement les alertes



