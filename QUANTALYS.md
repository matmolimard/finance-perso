# Notes Quantalys

## Description

Les notes Quantalys sont affichées automatiquement dans les sorties des commandes `make swisslife` et `make himalia` pour tous les fonds UC (Unités de Compte).

**Nouveauté** : Les notes Quantalys sont désormais **récupérées automatiquement** lors de la mise à jour des valeurs liquidatives avec `make update-navs` ! Plus besoin de saisie manuelle.

## Format d'affichage

Les notes sont affichées sous forme d'étoiles à côté du nom du fonds :
- ⭐ (1/5) : Note minimale
- ⭐⭐ (2/5)
- ⭐⭐⭐ (3/5)
- ⭐⭐⭐⭐ (4/5)
- ⭐⭐⭐⭐⭐ (5/5) : Note maximale

## Fichier de données

Les notes sont stockées dans le fichier :
```
portfolio_tracker/data/market_data/quantalys_ratings.yaml
```

## Structure du fichier

```yaml
ratings:
  - isin: "LU1331971769"
    name: "Eleva Absolute Return Europe"
    quantalys_rating: 4
    quantalys_category: "Actions Long/Short Europe"
    last_update: "2025-12-28"
    notes: "Note globale Quantalys"

  - isin: "FR0010174144"
    name: "BDL Rempart C"
    quantalys_rating: 4
    quantalys_category: "Obligations Diversifiées"
    last_update: "2025-12-28"
    notes: "Note globale Quantalys"
```

## Mise à jour automatique (recommandé)

### Récupération automatique avec les VL

La méthode la plus simple est de laisser le système récupérer automatiquement les notes lors de la mise à jour des VL :

```bash
make update-navs
```

Cette commande va :
1. ✅ Récupérer les valeurs liquidatives depuis Quantalys
2. ✅ **Extraire automatiquement les notes Quantalys** (1-5 étoiles)
3. ✅ **Extraire la catégorie du fonds**
4. ✅ **Sauvegarder ces informations dans `quantalys_ratings.yaml`**

Le fichier sera automatiquement mis à jour avec les dernières notes disponibles sur Quantalys.

### Prérequis

Pour que la récupération automatique fonctionne, vous devez avoir Playwright installé :

```bash
pip install playwright
python -m playwright install chromium
```

> **Note** : Playwright est nécessaire car Quantalys utilise du JavaScript pour afficher les données.

## Mise à jour manuelle (optionnel)

Si vous préférez mettre à jour les notes manuellement (ou si Playwright n'est pas installé), vous pouvez le faire directement dans le fichier YAML.

### 1. Consulter Quantalys

Rendez-vous sur [Quantalys.com](https://www.quantalys.com) et recherchez le fonds par son ISIN ou son nom.

### 2. Mettre à jour le fichier YAML

Modifiez le fichier `quantalys_ratings.yaml` :

```yaml
- isin: "VOTRE_ISIN"
  name: "Nom du fonds"
  quantalys_rating: 4  # Note de 1 à 5
  quantalys_category: "Catégorie du fonds"
  last_update: "YYYY-MM-DD"  # Date de mise à jour
  notes: "Note globale Quantalys"
```

### 3. Ajouter un nouveau fonds

Pour ajouter un nouveau fonds non présent dans le fichier :

```yaml
ratings:
  # ... fonds existants ...
  
  - isin: "NOUVEL_ISIN"
    name: "Nom du nouveau fonds"
    quantalys_rating: 3
    quantalys_category: "Catégorie"
    last_update: "2025-12-28"
    notes: "Note globale Quantalys"
```

### 4. Fonds non notés

Pour les fonds qui ne sont pas notés par Quantalys (ex: SCPI) :

```yaml
- isin: "FR0014009IF7"
  name: "SCI Cap Santé"
  quantalys_rating: null  # Pas de note
  quantalys_category: "Immobilier"
  last_update: "2025-12-28"
  notes: "Non noté par Quantalys (SCPI)"
```

## Vérification

Après modification, testez l'affichage avec :

```bash
make himalia
# ou
make swisslife
```

Les notes doivent apparaître à côté du nom de chaque fonds UC.

## Fréquence de mise à jour recommandée

Avec la **récupération automatique** :
- ✅ Les notes sont mises à jour **automatiquement** à chaque fois que vous lancez `make update-navs`
- ✅ Pas besoin de mise à jour manuelle !
- ✅ Toujours synchronisé avec Quantalys

Si vous utilisez la **mise à jour manuelle** :
- **Mensuelle** : Pour suivre l'évolution des performances des fonds
- **Trimestrielle** : Pour une surveillance normale
- **À chaque ajout/retrait** : Lors de l'ajout d'un nouveau fonds au portefeuille

## Notes techniques

- Les notes sont chargées automatiquement au démarrage du CLI
- Le système utilise le cache en mémoire pour éviter de relire le fichier à chaque affichage
- Seuls les fonds UC (type `uc_fund`) ayant un ISIN valide peuvent avoir une note Quantalys
- Les produits structurés et fonds euros ne sont pas notés par Quantalys

## Module Python

Le module de gestion des notes Quantalys se trouve dans :
```
portfolio_tracker/market/quantalys.py
```

Il expose la classe `QuantalysProvider` qui permet de :
- Charger les notes depuis le fichier YAML
- Récupérer la note d'un fonds par son ISIN
- Formater l'affichage des notes (étoiles)
- Vérifier la disponibilité des données

## Exemple d'utilisation programmatique

```python
from portfolio_tracker.market.quantalys import QuantalysProvider
from pathlib import Path

provider = QuantalysProvider(Path("portfolio_tracker/data/market_data"))

# Récupérer la note d'un fonds
rating_info = provider.get_rating("LU1331971769")
print(rating_info)
# {'name': 'Eleva Absolute Return Europe', 'rating': 4, 'category': '...', ...}

# Affichage formaté
display = provider.get_rating_display("LU1331971769")
print(display)  # "⭐⭐⭐⭐ (4/5)"
```

## Source des données

Les notes Quantalys proviennent de [www.quantalys.com](https://www.quantalys.com), un site de référence pour l'analyse et la notation de fonds d'investissement en France.

### Récupération automatique

Le système récupère automatiquement les notes lorsque vous mettez à jour les VL avec `make update-navs`. Les notes sont extraites directement depuis les pages Quantalys en même temps que les valeurs liquidatives.

**Technique** : 
- Utilise Playwright (mode headless) pour naviguer sur les pages Quantalys
- Extrait les notes via des expressions régulières depuis le HTML
- Sauvegarde automatiquement dans `quantalys_ratings.yaml`
- Ne modifie pas les notes existantes si elles sont identiques (évite les mises à jour inutiles)

### Configuration

Les URLs Quantalys sont configurées dans `portfolio_tracker/data/market_data/nav_sources.yaml` :

```yaml
nav_sources:
  uc_eleva_absolute_return_europe:
    kind: html_regex
    url: "https://www.quantalys.com/Fonds/405242"
    # ... autres paramètres
```

Lors de la récupération de la VL, le système extrait automatiquement :
- 📊 La valeur liquidative
- ⭐ La note Quantalys (1-5 étoiles)
- 📁 La catégorie du fonds

