# Mouvements et Valorisation

## Objectif

Le projet conserve les fichiers YAML comme surface éditable et snapshot lisible, mais le coeur métier repose désormais sur une couche domaine explicite :

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

La nouvelle couche sépare ces responsabilités sans casser la compatibilité avec le YAML existant.

## Flux de données

1. `positions.yaml` sert de snapshot lisible/exporté
2. `MovementNormalizer` transforme les lots en `NormalizedMovement`
3. `SQLiteMovementLedger` persiste ces mouvements dans une base SQLite locale
4. `PositionProjectionService` reconstruit l'état économique à une date donnée
5. les vues et moteurs de valorisation lisent cette projection

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

- `data/.portfolio_tracker.sqlite`

Cette base est :

- locale
- gratuite
- transactionnelle
- reconstruisible depuis le YAML

Elle est désormais la source opérationnelle des mouvements ; `positions.yaml` reste un export/snapshot.

Après une édition manuelle de `positions.yaml`, on peut forcer une resynchronisation explicite avec :

- `python -m portfolio_tracker.cli rebuild-ledger`

## Compatibilité

Le YAML reste compatible.

La stratégie actuelle est maintenant en place :

- on conserve `positions.yaml`
- on normalise les mouvements
- on fiabilise les calculs critiques autour des mouvements
- les moteurs et lectures métier consomment les projections

## Points d'attention restants

- distinguer explicitement `market_value` et `realized_exit_value` dans l'API de valorisation
- continuer à réduire le code de présentation restant dans `cli.py`
- faire évoluer le format d'export YAML si l'on veut refléter plus finement les projections
