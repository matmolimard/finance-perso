# Note de cadrage - Connecteur SwissLife

## Objectif

Construire un connecteur SwissLife autonome, fiable et traçable, capable de :

- se connecter à l'espace client SwissLife
- récupérer la valorisation assureur courante
- récupérer les positions par support
- récupérer les opérations récentes disponibles via l'API
- récupérer l'historique long via les documents SwissLife
- produire un export structuré réutilisable par un moteur de valorisation "source assureur"

Cette note ne branche rien à l'application. Elle fixe seulement les règles métier et techniques pour le futur connecteur SwissLife.

## Sources disponibles

### 1. API contrat et répartition

Endpoints observés :

- `GET /api/v4/nest/contrats/{contract_id}`
- `GET /api/v4/nest/contrats/{contract_id}/repartition`

Informations utiles :

- métadonnées contrat
- numéro de contrat
- libellé contrat
- date d'effet
- valorisation officielle
- plus ou moins-value
- performance
- positions par support
- détails support avec ISIN, quantité, VL, plus-value

### 2. API opérations

Endpoint observé :

- `GET /api/v4/nest/contrats/{contract_id}/operations/{offset}`

Comportement observé :

- pagination réelle
- dans notre cas :
  - `operations/0`
  - `operations/11`
- fin de pagination indiquée par `nextOperationAvailable = false`
- limitation observée via `historiqueOperationKlifeEnJours = 400`

Conclusion :

- cette API est bonne pour les flux récents
- elle ne suffit pas pour reconstruire seule toute la vie du contrat

### 3. API documents

Endpoints observés :

- `GET /api/v4/nest/documents/contrat/{contract_id}?...`
- `GET /api/v4/nest/documents/{fichierID}`

Le connecteur peut y trouver :

- relevés de situation
- arbitrages
- documents contractuels

Historique actuellement visible sur le contrat :

- `2026`: relevé de situation, garanties et options financières
- `2025`: relevé de situation, arbitrages
- `2024`: relevé de situation, arbitrages
- `2023`: relevé de situation, arbitrages
- `2022`: arbitrage, dispositions particulières

Conclusion :

- l'historique long est reconstructible via les documents SwissLife
- le connecteur doit donc combiner API récentes + documents PDF

## Règle de fiabilité principale

Le moteur assureur SwissLife doit être un moteur **à points d'ancrage assureur**.

### Principe

- un relevé de situation ou une valorisation web est un **checkpoint officiel**
- les opérations servent à expliquer les flux entre deux checkpoints
- une opération antérieure à un checkpoint ne doit jamais être ajoutée au stock de ce checkpoint

### Conséquence

On ne doit jamais faire :

- `valorisation snapshot + somme des opérations passées`

On doit faire :

1. prendre le dernier checkpoint assureur connu
2. considérer que tous les flux antérieurs sont déjà absorbés dans ce checkpoint
3. appliquer uniquement les flux postérieurs à ce checkpoint
4. dès qu'un checkpoint plus récent existe, recalage complet sur ce nouveau stock officiel

## Gestion anti double comptage

### Checkpoints

Sources de checkpoint possibles :

- relevé annuel PDF
- valorisation web courante SwissLife

Chaque checkpoint doit porter :

- `reference_date`
- `official_total_value`
- `positions`
- `source_type`
- `source_document_id` ou `source_api_payload`

### Flux

Chaque flux doit porter :

- `effective_date`
- `operation_type`
- `gross_amount`
- `status`
- `source_kind`
- `included_in_next_snapshot`

### Règle

Un flux `F` est :

- `active_for_rollforward = true` s'il est postérieur au dernier checkpoint de référence
- `already_absorbed = true` s'il est antérieur ou égal au checkpoint suivant connu

## Cas important : coupons de produits structurés

### Principe métier

Un coupon doit être vu sous deux angles :

- angle comptable assureur : destination technique du flux
- angle économique produit : origine réelle de la performance

### Règle de modélisation

Le coupon doit être :

- **attribué économiquement** au produit structuré qui le génère
- **tracé comptablement** sur le support de destination SwissLife

Autrement dit :

- le support monétaire ou fonds euro receveur n'est pas la source de rentabilité
- c'est seulement le support d'atterrissage du flux

### Champs recommandés

Pour un coupon détecté :

- `cashflow_type = coupon`
- `source_asset_id = produit_structure`
- `destination_asset_id = support_reception`
- `source_asset_isin`
- `destination_asset_isin`
- `observation_date`
- `payment_date`
- `coupon_amount`
- `coupon_rate`
- `coupon_condition`
- `is_confirmed_paid`

## Cas CMS observé sur SwissLife

Produit observé :

- `D RENDEMENT DISTRIBUTION FEVRIER 2025`
- ISIN `FR001400TBR1`
- sous-jacent `CMS_EUR_10Y`

Règle produit actuellement renseignée dans le repo :

- coupon semestre 1
  - constatation `2025-08-21`
  - paiement `2025-08-28`
- coupon semestre 2
  - constatation `2026-02-23`
  - paiement `2026-03-02`
- condition coupon : `CMS_EUR_10Y <= 3.20%`

Constatations vérifiées dans les données :

- `2025-08-21`: CMS EUR 10Y = `2.8212`
- `2026-02-23`: CMS EUR 10Y = `2.7857`

Conclusion :

- coupon semestre 1 dû et payé
- coupon semestre 2 dû et payé

Opérations SwissLife observées :

- `2025-08-28` `Détachement coupon` `2 711,58 €`
- `2026-03-02` métier `Détachement coupon` `2 698,43 €`

Note importante :

- les timestamps API SwissLife semblent être en fin de journée UTC
- il faut convertir les dates en `Europe/Paris`
- sinon certaines opérations sortent avec un jour de moins

Exemple :

- timestamp API du coupon de mars 2026 -> `2026-03-01` en UTC brut
- mais `2026-03-02` en heure de Paris

### Attribution correcte du coupon CMS

Les deux coupons ont été versés sur :

- `SLF (F) ESG Short Term Euro P1`
- ISIN `FR0013301629`

Mais économiquement ils doivent rester rattachés à :

- `FR001400TBR1`

Donc :

- le support monétaire ne doit pas capter la performance économique du coupon
- le produit structuré doit enregistrer le coupon comme performance réalisée

## Reconstitution de la ligne monétaire

Observation utile :

- le support `SLF (F) ESG Short Term Euro P1` sert de poche de réception
- il peut ensuite être arbitré, partiellement ou totalement

Conséquence :

- on ne peut pas raisonner "le coupon est encore là parce qu'on voit une petite ligne monétaire"
- le coupon est un flux, pas un stock durable garanti

Exemple observé :

- coupon août 2025 reçu sur le monétaire
- arbitrage automatique entrant en novembre 2025 sur le monétaire
- arbitrage sortant important en décembre 2025 depuis le monétaire
- coupon mars 2026 reçu sur le monétaire
- la ligne monétaire visible fin mars 2026 correspond presque uniquement au coupon de mars 2026

## Modèle recommandé pour le connecteur

### 1. Collecte brute

Sortie brute par run :

- `contract.json`
- `repartition.json`
- `operations_page_{offset}.json`
- `documents_index_{year}.json`
- PDFs téléchargés
- captures HTML / PNG optionnelles

### 2. Collecte normalisée

Sortie normalisée par run :

- `contract_summary`
- `positions`
- `recent_operations`
- `documents_index`
- `coupon_cashflows`
- `arbitration_cashflows`
- `snapshots`

### 3. Objets métier minimaux

#### `insurer_snapshot`

- `snapshot_id`
- `contract_id`
- `reference_date`
- `official_total_value`
- `official_plus_minus_value`
- `official_performance_pct`
- `source_kind`
- `source_ref`

#### `insurer_position`

- `snapshot_id`
- `asset_name`
- `asset_isin`
- `valuation`
- `units`
- `nav`
- `performance_pct`
- `weight_pct`
- `support_type`

#### `insurer_operation`

- `operation_id`
- `contract_id`
- `effective_date`
- `label`
- `operation_type`
- `status`
- `gross_amount`
- `source_kind`
- `raw_ref`

#### `insurer_operation_leg`

- `operation_id`
- `asset_name`
- `asset_isin`
- `direction`
- `gross_amount`
- `net_amount`
- `units`
- `nav`
- `support_type`

#### `economic_cashflow_attribution`

- `cashflow_id`
- `effective_date`
- `cashflow_type`
- `source_asset_isin`
- `source_asset_name`
- `destination_asset_isin`
- `destination_asset_name`
- `amount`
- `evidence_operation_id`
- `evidence_rule_ref`

## Règles pratiques à implémenter

### Dates

- convertir tous les timestamps SwissLife en `Europe/Paris`
- stocker aussi le timestamp brut si nécessaire pour audit

### Snapshots

- un snapshot PDF ou web remplace toute reconstruction antérieure du stock à cette date
- ne jamais rejouer des flux antérieurs sur un snapshot déjà connu

### Coupons

- rattacher économiquement le coupon au structuré
- rattacher comptablement le flux au support de destination

### Arbitrages

- les arbitrages sont des flux internes
- ils modifient la composition du portefeuille
- ils ne changent pas la valeur totale sauf frais associés

### Frais

- les frais de gestion diminuent la valeur
- les régularisations doivent rester des flux distincts
- ne pas les compenser implicitement sans trace

## Niveau de confiance actuel

### Fort

- connexion SwissLife
- récupération des positions courantes
- récupération des opérations récentes
- pagination récente des opérations
- récupération de l'index documentaire
- existence de téléchargements PDF directs
- détection et attribution des coupons CMS observés

### Moyen

- exhaustivité de certains historiques web non documentaires
- cartographie complète de tous les types documentaires SwissLife
- normalisation métier complète de tous les arbitrages PDF sans validation complémentaire

## Conclusion

Oui, un moteur de valorisation assureur SwissLife fiable est faisable.

La stratégie recommandée est :

- API contrat + répartition pour le stock courant
- API opérations pour les flux récents
- API documents + PDFs pour l'historique long
- moteur à checkpoints assureur pour éviter tout double comptage
- attribution économique explicite des coupons aux produits structurés

Le connecteur SwissLife ne doit pas être un simple scraper de page.
Il doit être un collecteur hybride :

- `stock officiel`
- `flux récents`
- `documents historiques`
- `règles d'attribution économique`

