# Application V2 - Specification des Donnees

Derniere mise a jour: 2026-03-26
Statut: brouillon a valider

Ce document decrit les objets de donnees de la V2.
Il complete :

- [RULES.md](/Users/mathieu/Documents/Developpement/finance-perso/RULES.md)
- [v2_global_spec.md](/Users/mathieu/Documents/Developpement/finance-perso/docs/v2_global_spec.md)
- [v2_functional_spec.md](/Users/mathieu/Documents/Developpement/finance-perso/docs/v2_functional_spec.md)

## 1. Principes de modelisation

La V2 suit 5 principes simples :

- separer les donnees officielles, les donnees calculees et les donnees estimees
- privilegier le niveau contrat pour les apports et les retraits externes
- privilegier le niveau position pour la valorisation
- donner une vraie place aux documents dans le modele
- rendre chaque objet versionnable, explicable et validable

## 2. Typologie des donnees

La V2 manipule 4 familles de donnees.

### 2.1 Donnees de reference

Donnees relativement stables :

- contrats
- actifs
- regles de support
- types documentaires

### 2.2 Donnees historiques officielles

Donnees venant de l'assureur :

- snapshots annuels
- etats de positions au 31/12
- taux officiels
- coupons confirmes
- mouvements visibles sur releves

### 2.3 Donnees de pilotage

Donnees saisies ou calculees pour l'annee en cours :

- taux de pilotage fonds euro
- valeur de pilotage
- confirmations manuelles de coupons
- arbitrages proposes / valides
- retraits

### 2.4 Donnees documentaires

Donnees GED :

- PDF
- index documentaire
- rattachements contrat / actif / annee / evenement

## 3. Objets metier principaux

## 3.1 Contract

Represente un contrat suivi dans l'application.

Champs cibles :

- `contract_id`
- `contract_name`
- `insurer`
- `wrapper_type`
- `holder_type`
- `fiscal_applicability`
- `currency`
- `opened_on`
- `status`
- `notes`

Contraintes :

- un contrat doit avoir un identifiant stable
- `status` distingue au minimum `active` et `archived`

Valeurs minimales de `holder_type` :

- `individual`
- `holding`
- `legal_entity`

Valeurs minimales de `fiscal_applicability` :

- `applicable`
- `not_applicable`
- `unknown`

Commentaires :

- `holder_type` permet de ne pas reutiliser aveuglement les blocs fiscaux standard des releves
- cas connus :
  - `HIMALIA` : detention en personnel, fiscalite applicable
  - `SwissLife Capi Strategique Premium` : detention via holding, fiscalite non applicable pour le suivi patrimonial v2
- la V2 ne doit pas reutiliser les blocs fiscaux assureur ni deriver une lecture "benefice holding" a partir de ces releves pour SwissLife

## 3.2 Asset

Represente un support financier abstrait.

Champs cibles :

- `asset_id`
- `asset_type`
- `name`
- `isin`
- `currency`
- `status`
- `metadata`

Valeurs minimales de `asset_type` :

- `fonds_euro`
- `uc`
- `structured_product`

## 3.3 Position

Represente une detention d'un actif dans un contrat.

Champs cibles :

- `position_id`
- `contract_id`
- `asset_id`
- `opened_on`
- `closed_on`
- `status`
- `notes`

Contraintes :

- une position appartient a un contrat
- une position reference un actif
- une position closee sort de la vue principale

## 3.4 Document

Represente un document GED.

Champs cibles :

- `document_id`
- `document_type`
- `insurer`
- `contract_id` optionnel
- `asset_id` optionnel
- `document_date`
- `coverage_year` optionnel
- `status`
- `filepath`
- `original_filename`
- `sha256`
- `notes`
- `imported_at`

Valeurs minimales de `document_type` :

- `insurer_statement`
- `arbitration_letter`
- `contract_document`
- `endorsement`
- `structured_brochure`
- `other_insurer_letter`

Valeurs minimales de `status` :

- `active`
- `archived`

## 3.5 SnapshotYear

Represente un etat annuel officiel d'un contrat.

Champs cibles :

- `snapshot_id`
- `contract_id`
- `reference_date`
- `statement_date`
- `validated_at`
- `source_document_id`
- `status`
- `official_total_value`
- `official_uc_value` optionnel
- `official_fonds_euro_value` optionnel
- `official_previous_year_total_value` optionnel
- `official_year_delta_value` optionnel
- `official_since_inception_delta_value` optionnel
- `official_uc_share_pct` optionnel
- `official_fonds_euro_share_pct` optionnel
- `official_surrender_value` optionnel
- `official_euro_interest_net` optionnel
- `official_social_levies_already_deducted` optionnel
- `visible_operations_scope`
- `official_notes`

Valeurs minimales de `status` :

- `proposed`
- `validated`
- `superseded`

Commentaires :

- `reference_date` correspond typiquement au `31/12/N`
- `statement_date` correspond a la date du releve recu
- `validated_at` correspond a la date de validation dans l'application
- `visible_operations_scope` distingue ce que le releve montre reellement :
  - `full_year_operations`
  - `partial_changes_only`
  - `none`
- exemples observes :
  - les releves HIMALIA donnent un vrai bloc `OPERATIONS REALISEES DU 01/01/N AU 31/12/N`
  - les releves SwissLife donnent surtout un etat au 31/12, des changements de supports et des detachements de coupons, mais pas un journal annuel complet

## 3.6 SnapshotPosition

Represente l'etat d'une position dans un snapshot annuel.

Champs cibles :

- `snapshot_position_id`
- `snapshot_id`
- `position_id` optionnel si la position existe deja
- `asset_id`
- `asset_type`
- `asset_name_raw`
- `isin` optionnel
- `valuation_date` optionnel
- `quantity` optionnel
- `unit_value` optionnel
- `official_value`
- `official_cost_basis` optionnel
- `official_profit_sharing_amount` optionnel
- `official_average_purchase_price` optionnel
- `status`
- `notes`

Utilite :

- figer l'etat d'un support tel qu'il apparait sur le releve
- permettre un rapprochement avec la position interne de l'application
- conserver les colonnes vraiment visibles dans les releves :
  - SwissLife : support, ISIN, date de valorisation, montant net, nombre de parts, valeur de la part, prix moyen d'investissement
  - HIMALIA : support, date, valeur de la part, nombre de parts, PAM, epargne atteinte
  - fonds euro : support, date, participation aux benefices, epargne atteinte

## 3.6.1 SnapshotOperationVisible

Represente une operation explicitement visible dans un releve annuel.

Champs cibles :

- `snapshot_operation_id`
- `snapshot_id`
- `operation_label_raw`
- `operation_type`
- `effective_date`
- `fees_amount` optionnel
- `gross_amount` optionnel
- `source_document_id`
- `notes`

Valeurs minimales de `operation_type` :

- `initial_contribution`
- `external_contribution`
- `arbitration`
- `security_redemption`
- `management_fee`
- `dividend_distribution`
- `withdrawal`
- `other`

Commentaires :

- cet objet ne doit contenir que des operations explicitement visibles dans le PDF
- il ne faut pas reconstruire ici des mouvements supposes
- HIMALIA permet deja de remplir cet objet richement
- SwissLife ne le permet que partiellement selon les annees

## 3.6.2 SnapshotOperationLegVisible

Represente une ligne support a l'interieur d'une operation visible.

Champs cibles :

- `snapshot_operation_leg_id`
- `snapshot_operation_id`
- `asset_id` optionnel
- `asset_name_raw`
- `asset_type` optionnel
- `direction`
- `amount`
- `valuation_date` optionnel
- `unit_value` optionnel
- `quantity` optionnel
- `notes`

Valeurs minimales de `direction` :

- `in`
- `out`
- `unknown`

Commentaires :

- cet objet est indispensable pour les arbitrages et remboursements visibles sur releve
- exemple HIMALIA 2025 :
  - remboursement du `Callable Note Taux Fixe Dec 23`
  - passage par `GENERALI Tresorerie ISR Act B`
  - arbitrage ensuite vers trois produits structures

## 3.6.3 SnapshotFondsEuroTerms

Represente les informations annuelles officielles visibles sur le fonds euro dans le releve.

Champs cibles :

- `snapshot_fonds_euro_terms_id`
- `snapshot_id`
- `asset_id` optionnel
- `official_rate_gross` optionnel
- `official_rate_net_fees` optionnel
- `official_rate_net_fees_social` optionnel
- `guaranteed_rate` optionnel
- `profit_sharing_rate` optionnel
- `bonus_rate` optionnel
- `bonus_amount` optionnel
- `management_fee_rate` optionnel
- `social_levy_rate` optionnel
- `social_levy_amount` optionnel
- `interest_amount_net` optionnel
- `average_uc_share_pct` optionnel
- `notes`

Commentaires :

- cet objet garde les termes officiels de l'annee, sans recalcul
- exemples observes :
- SwissLife 2025 : taux brut, taux de participation aux benefices, majoration, taux net de frais, taux net de frais et de prelevements sociaux
- HIMALIA 2025 : taux brut de participation aux benefices, frais de gestion, taux net, complement de participation, taux net distribue, taux de prelevements sociaux

## 3.6.4 SnapshotFiscalInfo

Represente les informations fiscales visibles sur un releve annuel quand elles sont pertinentes pour le contrat.

Champs cibles :

- `snapshot_fiscal_info_id`
- `snapshot_id`
- `contract_id`
- `applicability_status`
- `primes_not_reimbursed_pre_2017` optionnel
- `primes_not_reimbursed_post_2017` optionnel
- `social_levy_base` optionnel
- `social_levy_amount` optionnel
- `social_levy_rate` optionnel
- `tax_notes` optionnel
- `source_document_id`

Valeurs minimales de `applicability_status` :

- `applicable`
- `not_applicable`
- `unknown`

Commentaires :

- cet objet est volontairement separe du `SnapshotYear`
- il sert a conserver les informations fiscales visibles sur le releve sans les melanger a la valorisation
- il n'est rempli que si les donnees sont utiles pour le contrat concerne
- exemples cibles :
  - `HIMALIA` : objet rempli et exploitable
  - `SwissLife` via holding : objet absent ou present avec `applicability_status = not_applicable`
- pour SwissLife via holding, ces donnees ne doivent entrer ni dans les calculs patrimoniaux ni dans une lecture comptable specifique de la holding

## 3.7 ContractCashFlow

Represente un flux au niveau contrat.

Champs cibles :

- `cashflow_id`
- `contract_id`
- `effective_date`
- `cashflow_type`
- `amount`
- `source_document_id` optionnel
- `notes`
- `status`

Valeurs minimales de `cashflow_type` :

- `external_contribution`
- `external_withdrawal`
- `internal_transfer`
- `fee`
- `tax`
- `other`

Commentaires :

- cet objet sert a sortir du piege "chaque buy de position = apport externe"
- les flux externes se suivent au niveau contrat

## 3.8 Arbitration

Represente un arbitrage explicite.

Champs cibles :

- `arbitration_id`
- `contract_id`
- `effective_date`
- `source_document_id`
- `gross_amount`
- `fees_amount`
- `status`
- `notes`

Valeurs minimales de `status` :

- `proposed`
- `validated`
- `cancelled`

## 3.9 ArbitrationLeg

Represente une jambe d'un arbitrage.

Champs cibles :

- `arbitration_leg_id`
- `arbitration_id`
- `direction`
- `position_id` optionnel
- `asset_id`
- `amount`
- `quantity` optionnel

Valeurs minimales de `direction` :

- `from`
- `to`

## 3.10 Withdrawal

Represente un retrait explicite.

Champs cibles :

- `withdrawal_id`
- `contract_id`
- `request_date` optionnel
- `document_date` optionnel
- `effective_date`
- `withdrawal_type`
- `gross_amount`
- `net_amount` optionnel
- `fees_amount`
- `tax_amount`
- `source_document_id` optionnel
- `notes`
- `status`

Valeurs minimales de `withdrawal_type` :

- `external_withdrawal`
- `position_exit_inside_contract`

Commentaires :

- `external_withdrawal` = sortie hors contrat
- `position_exit_inside_contract` = sortie d'un support avec reallocation interne
- un remboursement de produit structure n'est pas un retrait
- un arbitrage n'est pas un retrait

## 3.10.1 WithdrawalLeg

Represente une jambe d'un retrait.

Champs cibles :

- `withdrawal_leg_id`
- `withdrawal_id`
- `position_id` optionnel
- `asset_id` optionnel
- `asset_name_raw`
- `amount`
- `quantity` optionnel
- `unit_value` optionnel
- `valuation_date` optionnel
- `direction`
- `notes`

Valeurs minimales de `direction` :

- `out_of_position`
- `to_internal_cash`
- `out_of_contract`

Commentaires :

- `out_of_position` = sortie d'un support
- `to_internal_cash` = l'argent reste dans le contrat
- `out_of_contract` = l'argent quitte reellement le contrat
- un `external_withdrawal` doit avoir au moins une jambe `out_of_contract`
- un `position_exit_inside_contract` ne doit jamais creer de flux externe

## 3.11 StructuredRule

Represente une fiche de regles lisibles pour un produit structure.

Champs cibles :

- `structured_rule_id`
- `asset_id`
- `rule_source_mode`
- `coupon_payment_mode`
- `coupon_mode`
- `coupon_frequency_months`
- `coupon_formula_text`
- `autocall_formula_text`
- `capital_formula_text`
- `strict_mode_policy`
- `pilotage_mode_policy`
- `brochure_document_id`
- `status`
- `notes`

Valeurs minimales de `rule_source_mode` :

- `document_derived`
- `manual`
- `mixed`

Valeurs minimales de `coupon_mode` :

- `periodic`
- `in_fine`
- `conditional_periodic`

Valeurs minimales de `coupon_payment_mode` :

- `periodic_distributed`
- `periodic_reinvested`
- `in_fine`
- `none`
- `unknown`

Commentaires :

- cet objet est central pour expliquer le calcul
- il doit privilegier le texte lisible plutot qu'une formule trop opaque
- `coupon_mode` decrit la structure du mecanisme
- `coupon_payment_mode` decrit la facon dont le coupon est effectivement traite dans le contrat
- il doit etre editable depuis l'interface
- `rule_source_mode` permet de distinguer :
  - une regle derivee de la brochure
  - une regle saisie manuellement
  - une regle mixte brochure + corrections utilisateur

## 3.12 StructuredCouponEvent

Represente un coupon attendu ou constate pour un produit structure.

Champs cibles :

- `coupon_event_id`
- `position_id`
- `asset_id`
- `observation_date` optionnel
- `payment_date`
- `coupon_type`
- `coupon_rate` optionnel
- `coupon_amount` optionnel
- `coupon_status`
- `source_document_id` optionnel
- `notes`

Valeurs minimales de `coupon_type` :

- `expected`
- `recorded`
- `manual_override`

Valeurs minimales de `coupon_status` :

- `paid`
- `not_paid`
- `unknown`

Commentaires :

- c'est l'objet cle pour les produits CMS
- pour les produits `in_fine`, cet objet ne doit pas inventer des coupons verses avant remboursement
- en mode strict :
  - seuls les coupons confirmes sont comptes
- en mode pilotage :
  - les coupons theoriques peuvent etre projetes, mais doivent rester distingues des coupons confirmes

## 3.12.1 StructuredRedemptionEvent

Represente un evenement de remboursement, d'autocall ou d'echeance d'un produit structure.

Champs cibles :

- `structured_redemption_event_id`
- `position_id`
- `asset_id`
- `contract_id`
- `event_type`
- `event_status`
- `trigger_date` optionnel
- `value_date` optionnel
- `settlement_date` optionnel
- `gross_redemption_amount`
- `net_redemption_amount` optionnel
- `redeemed_quantity` optionnel
- `unit_redemption_value` optionnel
- `capital_gain_amount` optionnel
- `capital_gain_pct` optionnel
- `coupon_amount_included` optionnel
- `coupon_count_included` optionnel
- `destination_asset_id` optionnel
- `destination_asset_name_raw` optionnel
- `destination_amount` optionnel
- `source_document_id`
- `notes`

Valeurs minimales de `event_type` :

- `autocall_redemption`
- `maturity_redemption`
- `issuer_redemption`
- `manual_redemption`

Valeurs minimales de `event_status` :

- `expected`
- `confirmed`
- `settled`
- `cancelled`

Commentaires :

- cet objet est distinct d'un `Arbitration`
- un remboursement de structure ne doit pas etre force dans le modele d'arbitrage
- il peut etre suivi d'un arbitrage ou d'un reinvestissement, mais ce sont des evenements distincts
- les dates doivent rester separees car les documents peuvent mentionner :
  - une date d'echeance ou de constatation
  - une date de valeur
  - une date effective de reinvestissement
- si le gain annonce est connu et fiable, on peut le stocker dans `capital_gain_amount` ou `capital_gain_pct`
- si le document mentionne seulement un montant reinvesti, on stocke ce montant sans reconstruire artificiellement le gain

## 3.13 FondsEuroRule

Represente la regle de pilotage d'un fonds euro.

Champs cibles :

- `fonds_euro_rule_id`
- `asset_id`
- `current_year`
- `official_rate_previous_year`
- `official_rate_source_document_id` optionnel
- `pilotage_rate_current_year`
- `pilotage_rate_set_at`
- `notes`

Commentaires :

- on distingue bien le taux officiel et le taux de pilotage

## 3.14 MarketNavPoint

Represente un point de VL pour une UC.

Champs cibles :

- `nav_point_id`
- `asset_id`
- `nav_date`
- `nav_value`
- `source`
- `staleness_status`
- `imported_at`

## 3.15 DerivedMetric

Represente une valeur calculee et audit-able.

Champs cibles :

- `metric_id`
- `scope_type`
- `scope_id`
- `metric_name`
- `period_type`
- `as_of_date`
- `value`
- `calculation_mode`
- `explanation`
- `inputs_payload`
- `status`

Utilite :

- stocker ou recalculer des chiffres explicables
- fournir le "comment ce chiffre est calcule"

## 4. Relations principales

Relations cibles :

- un `Contract` a plusieurs `Position`
- un `Asset` peut etre lie a plusieurs `Position`
- un `Contract` a plusieurs `Document`
- un `SnapshotYear` appartient a un `Contract`
- un `SnapshotYear` a plusieurs `SnapshotPosition`
- un `SnapshotYear` peut avoir un `SnapshotFiscalInfo`
- un `Arbitration` appartient a un `Contract`
- un `Arbitration` a plusieurs `ArbitrationLeg`
- un `Withdrawal` appartient a un `Contract`
- un `Withdrawal` a plusieurs `WithdrawalLeg`
- un `StructuredRule` appartient a un `Asset`
- un `StructuredCouponEvent` appartient a une `Position`
- un `StructuredRedemptionEvent` appartient a une `Position`

## 5. Donnees officielles vs donnees de pilotage

La V2 doit marquer explicitement la nature d'une valeur.

Types de nature retenus :

- `official`
- `calculated`
- `estimated`
- `manual_override`

Exemples :

- valeur de releve au 31/12 : `official`
- valeur fonds euro en cours d'annee : `calculated` ou `estimated`
- coupon CMS confirme par l'utilisateur : `manual_override`
- VL UC venue d'une source de marche : `calculated` avec source externe

## 6. Etats minimum a gerer

### 6.1 Etat d'une position

- `active`
- `closed`
- `archived`

### 6.2 Etat d'un document

- `active`
- `archived`

### 6.3 Etat d'un snapshot

- `proposed`
- `validated`
- `superseded`

### 6.4 Etat d'un coupon conditionnel

- `paid`
- `not_paid`
- `unknown`

## 7. Rangement documentaire cible

La GED locale doit suivre une arborescence simple.

Exemple :

- `data/documents/insurer/swisslife/releves/`
- `data/documents/insurer/swisslife/courriers/arbitrages/`
- `data/documents/insurer/swisslife/courriers/contractuel/`
- `data/documents/insurer/generali/himalia/releves/`
- `data/documents/structured/brochures/`

Important :

- l'arborescence physique ne suffit pas
- chaque document doit etre indexe

## 8. Donnees a ne pas confondre

La V2 doit distinguer explicitement :

- `apports externes cumules`
- `apports externes nets`
- `capital encore expose`
- `valeur officielle`
- `valeur de pilotage`
- `performance simple`
- `TRI`

Aucun de ces champs ne doit etre reutilise pour un autre sens implicite.

## 9. Definitions retenues pour les performances

### 9.1 Performance simple

La performance simple est une mesure non annualisee de la richesse creee sur la periode.

Formule retenue :

```text
performance_simple_montant
= valeur_fin
- valeur_debut
- apports_externes_periode
+ retraits_externes_periode
+ revenus_distribues_hors_contrat_periode
```

Base retenue :

```text
base_performance_simple
= valeur_debut + apports_externes_periode
```

Pourcentage retenu :

```text
performance_simple_pct
= performance_simple_montant / base_performance_simple
```

Regles :

- les arbitrages internes ne comptent pas
- un revenu reinvesti dans le contrat reste dans `valeur_fin`
- un revenu distribue hors contrat entre comme flux positif
- `depuis l'ouverture` : `valeur_debut = 0`
- `depuis le 1er janvier` : `valeur_debut = valeur officielle au 31/12/N-1`
- `par annee` : `valeur_debut = valeur officielle au 31/12/N-1` et `valeur_fin = valeur officielle au 31/12/N`

### 9.2 TRI

Le TRI affiche est un XIRR base sur les flux externes reels et la valeur de fin de periode.

Formule conceptuelle retenue :

```text
Σ flux_i / (1 + r)^(jours_i / 365) = 0
```

Convention de signe :

- apport externe : flux negatif
- retrait externe : flux positif
- revenu verse hors contrat : flux positif
- valeur finale : flux positif a la date de fin
- valeur initiale non nulle : flux negatif a la date de debut

Regles :

- `depuis l'ouverture` : tous les flux externes depuis l'origine + valeur finale
- `depuis le 1er janvier` : valeur au `01/01/N` + flux externes de l'annee + valeur finale
- `par annee` : valeur officielle au `01/01/N` + flux externes de l'annee + valeur officielle au `31/12/N`
- les arbitrages internes, reinvestissements internes et capitalisations internes ne comptent pas comme flux TRI

## 10. Schema exact retenu pour le snapshot annuel

Le snapshot annuel officiel ne doit pas etre un simple total de contrat.
Il doit normaliser ce que les releves montrent reellement, sans inventer le reste.

Le schema retenu est donc :

1. un `SnapshotYear`
   - resume officiel du contrat au `31/12/N`
   - date de releve
   - date de validation
   - indicateur de couverture des operations visibles

2. une liste de `SnapshotPosition`
   - une ligne par support visible sur le releve
   - quantite, valeur unitaire, valeur totale, PAM si present
   - montant de participation aux benefices pour le fonds euro si present

3. une liste optionnelle de `SnapshotOperationVisible`
   - seulement quand le releve contient un vrai bloc d'operations
   - jamais deduite par heuristique

4. une liste de `SnapshotOperationLegVisible`
   - pour decomposer les arbitrages, remboursements, frais et distributions visibles

5. un bloc optionnel `SnapshotFondsEuroTerms`
   - pour figer les taux officiels et leur decomposition annuelle

6. des `notes`
   - pour garder les cas particuliers visibles seulement en texte libre

Critere important :

- un snapshot annuel doit pouvoir etre valide meme si toutes les positions internes ne sont pas encore rapprochees
- il prime sur l'historique interne si les deux divergent sur l'etat officiel au `31/12`
- il ne doit pas embarquer de valorisation de pilotage de l'annee suivante

## 10.1 Niveau de granularite retenu pour les mouvements visibles

La V2 doit rester simple :

- le snapshot annuel ne stocke que les mouvements explicitement visibles dans les documents
- il ne reconstruit pas un ledger complet a partir d'heuristiques
- chaque famille d'evenement garde son propre objet metier

Granularite retenue :

1. `SnapshotOperationVisible`
   - trace documentaire brute et normalisee
   - utile pour auditer ce que montre un releve annuel

2. `Arbitration` et `ArbitrationLeg`
   - pour les arbitrages explicites confirmes
   - en general sources par les courriers d'arbitrage

3. `StructuredRedemptionEvent`
   - pour les remboursements, echeances et autocalls de produits structures

4. `Withdrawal` et `WithdrawalLeg`
   - pour les sorties reelles du contrat ou les sorties de support restant internes au contrat

5. `ContractCashFlow`
   - uniquement pour raisonner sur les apports et retraits externes au niveau contrat

Regle importante :

- un meme document peut alimenter plusieurs objets
- mais on ne doit pas forcer tous les mouvements dans un objet unique universel
- la simplicite et la fidelite documentaire priment sur la reconstruction exhaustive

## 11. Questions ouvertes de modelisation

Points encore a arbitrer :

- aucun a ce stade sur le modele coeur

## 11.1 Decision retenue sur `PositionValuationSnapshot`

La V2 phase 1 ne retient pas d'objet `PositionValuationSnapshot`.

Raison :

- le suivi des UC est deja couvert par :
  - `SnapshotPosition` pour les points officiels annuels
  - `MarketNavPoint` pour l'historique de VL
  - `Position` pour la quantite detenue
  - `DerivedMetric` pour les chiffres calcules et auditables
- ajouter un snapshot de valorisation des positions en phase 1 complexifierait inutilement le modele

Consequence :

- les valorisations sont recalculees a la demande
- si un besoin de figer une valeur affichee apparait plus tard, il sera traite en phase 2

## 12. Ordre recommande d'implementation du modele

1. `Contract`
2. `Asset`
3. `Position`
4. `Document`
5. `SnapshotYear`
6. `SnapshotPosition`
7. `StructuredRule`
8. `StructuredCouponEvent`
9. `StructuredRedemptionEvent`
10. `FondsEuroRule`
11. `Arbitration`
12. `ArbitrationLeg`
13. `Withdrawal`
14. `WithdrawalLeg`
15. `DerivedMetric`
