# Mouvements et Valorisation

## Objectif

Le projet converge vers une base SQLite canonique pour les mouvements, avec les YAML encore présents comme seeds/snapshots de transition pour certains objets :

- `portfolio_tracker/domain/movements.py`
- `portfolio_tracker/domain/projection.py`
- `portfolio_tracker/domain/ledger.py`

Cette architecture introduit deux idées :

- un **mouvement normalisé** : représentation métier stable d'un lot historique
- une **projection de position** : état économique calculé à une date donnée

## Pourquoi

Le modèle historique mélangeait plusieurs notions dans `investment.lots` :

- mouvement brut importé
- cashflow économique
- source de vérité sur les quantités
- base de calcul de performance

La nouvelle couche sépare ces responsabilités sans casser la compatibilité avec les données historiques déjà présentes.

## Flux de données

1. le catalogue d'actifs et les positions sont reconstruits depuis `market_data`, les PDF et les corrections manuelles persistées en base
2. les corrections manuelles et arbitrages PDF sont persistés en SQLite
3. la base SQLite porte les actifs, positions, lots de seed et mouvements documentaires/manuels
4. `MovementNormalizer` transforme les lots en `NormalizedMovement`
5. `PositionProjectionService` reconstruit l'état économique à une date donnée depuis le portefeuille chargé en base

## Concepts clés

### NormalizedMovement

Un mouvement normalisé contient :

- `position_id`
- `asset_id`
- `effective_date`
- `raw_lot_type`
- `movement_kind`
- `cash_amount`
- `units_delta`
- `unit_price`
- `external`

`movement_kind` est explicite :

- `external_contribution`
- `internal_capitalization`
- `withdrawal`
- `fee`
- `tax`
- `other`

### PositionProjection

Une projection calcule :

- `open_units`
- `external_contributions_total`
- `internal_capitalizations_total`
- `withdrawals_total`
- `fees_total`
- `taxes_total`
- `external_capital_remaining`
- `realized_exit_value`
- `close_date`

## SQLite locale

Le ledger est stocké dans une base SQLite locale créée dans le répertoire de données :

- `data/.portfolio_tracker_v2.sqlite`

Cette base est :

- locale
- gratuite
- transactionnelle
- reconstruisible depuis les PDF, les métadonnées marché et les corrections manuelles

Elle est désormais la source opérationnelle du portefeuille V2. Le YAML n’est plus dans le chemin runtime principal et n’est plus la cible d’écriture pour les arbitrages PDF.

## Compatibilité

La compatibilité YAML reste assurée pour les seeds existants, mais le runtime et les nouveaux mouvements métier passent par SQLite.

## Points d'attention restants

- distinguer explicitement `market_value` et `realized_exit_value` dans l'API de valorisation
- continuer à réduire le code de présentation restant dans `cli.py`
- faire évoluer le format d'export YAML si l'on veut refléter plus finement les projections

## Valeurs officielles assureur vs modèle interne

Pour les **produits structurés**, la valorisation « réelle » côté assureur n’est en général pas reconstituable à la demande : le relevé annuel reste la référence comptable.

Le dashboard expose donc en parallèle :

- des champs **officiels** issus des snapshots importés (`official_total_value`, `official_uc_value`, `official_fonds_euro_value`, et lorsque dérivable `official_structured_value`) ;
- une valorisation **modèle** à la date du snapshot (`model_structured_value`) et l’écart (`structured_model_gap_*`).

Les **transactions** documentées (arbitrages PDF appliqués, corrections manuelles justifiées) ancrent le modèle sur les montants assureur ; les lots runtime peuvent porter des métadonnées (`source`, `model_anchor`) pour la traçabilité.

Voir aussi **README.md** (section *GED, snapshots, mouvements et arbitrages*).
