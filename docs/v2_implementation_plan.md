# Application V2 - Plan d'Implementation Technique Phase 1

Derniere mise a jour: 2026-03-26
Statut: brouillon a valider

Ce document decrit comment implementer la V2 phase 1 sans casser la V1.

Il complete :

- [v2_global_spec.md](/Users/mathieu/Documents/Developpement/finance-perso/docs/v2_global_spec.md)
- [v2_functional_spec.md](/Users/mathieu/Documents/Developpement/finance-perso/docs/v2_functional_spec.md)
- [v2_data_spec.md](/Users/mathieu/Documents/Developpement/finance-perso/docs/v2_data_spec.md)
- [v2_screens_spec.md](/Users/mathieu/Documents/Developpement/finance-perso/docs/v2_screens_spec.md)
- [v2_workflows_spec.md](/Users/mathieu/Documents/Developpement/finance-perso/docs/v2_workflows_spec.md)

## 1. Strategie generale

La V2 se construit a cote de la V1.

Principes retenus :

- ne pas rebrancher brutalement le coeur v1
- creer une couche v2 isolee et testable
- reutiliser les documents deja ranges dans la GED
- reutiliser la collecte de VL si elle est fiable
- ne pas heriter des heuristiques v1 quand elles sont en conflit avec la spec v2

## 2. Ce qu'on garde de la V1

Peut etre reutilise :

- la GED deja alimentee
- certains actifs et ISIN existants
- l'historique de VL UC si la source est saine
- certaines briques de lecture PDF ou parsing existantes
- la petite web app uniquement comme support technique, pas comme base produit

## 3. Ce qu'on ne garde pas comme socle v2

Ne doit pas servir de coeur v2 :

- la logique metier concentree dans `portfolio_tracker/cli.py`
- les heuristiques implicites sur les lots pour deduire les apports
- le modele v1 centré sur `positions.yaml` comme verite principale
- les conventions implicites actuelles sur `invested_amount`

## 4. Arborescence technique cible

Recommandation :

- `portfolio_tracker/v2/domain/`
- `portfolio_tracker/v2/application/`
- `portfolio_tracker/v2/infrastructure/`
- `portfolio_tracker/v2/web/`
- `portfolio_tracker/v2/tests/`

Decoupage :

- `domain`
  - modeles metier v2
  - regles de calcul
- `application`
  - use cases
  - workflows
  - queries
- `infrastructure`
  - persistence
  - lecture GED
  - extracteurs PDF
  - connecteurs VL
- `web`
  - routes
  - templates / API
  - formulaires

## 5. Ordre recommande d'implementation

## Phase A - Socle donnees et persistence

Objectif :

- poser les modeles et la base locale v2

Livrables :

- schema de persistence v2
- modeles `Contract`, `Asset`, `Position`, `Document`
- modeles snapshots et evenements
- migrations / bootstrap

Sorties attendues :

- base v2 initialisable
- documents GED referencables

## Phase B - GED et index documentaire

Objectif :

- brancher la GED actuelle sur la v2

Livrables :

- repository `Document`
- lecture des index YAML existants
- page GED minimale
- filtres assureur / contrat / type / annee

Sorties attendues :

- tous les documents visibles dans la v2
- ouverture des documents depuis l'interface

## Phase C - Snapshots annuels

Objectif :

- rendre les releves assureur exploitables

Livrables :

- extracteur SwissLife
- extracteur HIMALIA
- creation de `SnapshotYear`
- creation de `SnapshotPosition`
- validation manuelle dans l'interface

Sorties attendues :

- snapshots 2023/2024/2025 validables
- base officielle historique disponible

## Phase D - Fonds euro

Objectif :

- afficher les fonds euro selon la logique v2

Livrables :

- lecture des `SnapshotFondsEuroTerms`
- regle de valeur officielle
- regle de valeur de pilotage intra-annuelle
- formulaire de saisie du taux de pilotage

Sorties attendues :

- detail fonds euro exploitable
- valeur pilotage lisible et audit-able

## Phase E - UC

Objectif :

- afficher les UC avec valeur a date et historique

Livrables :

- repository `MarketNavPoint`
- calcul `quantite x derniere VL connue`
- indicateur de fraicheur
- detail support UC

Sorties attendues :

- vues UC par contrat
- detail UC avec serie de VL

## Phase F - Produits structures

Objectif :

- rendre les produits structures lisibles, editables et validables

Livrables :

- `StructuredRule`
- edition de la fiche produit
- `StructuredCouponEvent`
- `StructuredRedemptionEvent`
- vue detail support structure
- mode strict et mode pilotage

Sorties attendues :

- fiche produit editable
- coupons confirmables
- remboursements visualisables

## Phase G - Evenements de contrat

Objectif :

- structurer ce qui se passe dans le contrat

Livrables :

- `Arbitration` + `ArbitrationLeg`
- `Withdrawal` + `WithdrawalLeg`
- workflows de validation

Sorties attendues :

- evenements recents affichables
- historique coherent

## Phase H - Tableau de bord et ecrans principaux

Objectif :

- brancher les ecrans de pilotage phase 1

Livrables :

- tableau de bord
- detail contrat
- detail support
- GED
- validation et evenements
- historique

Sorties attendues :

- v2 utilisable en lecture
- puis en validation / edition limitee

## 6. Priorites de livraison

Priorite 1 :

- GED
- snapshots annuels
- fonds euro
- UC

Priorite 2 :

- produits structures
- validation des coupons
- remboursements

Priorite 3 :

- arbitrages
- retraits
- historique complet

## 7. Strategie de tests

La phase 1 doit etre testee a 3 niveaux :

## 7.1 Tests unitaires

- parsing snapshots SwissLife
- parsing snapshots HIMALIA
- calcul fonds euro pilotage
- calcul performance simple
- calcul TRI
- mapping de documents

## 7.2 Tests d'integration

- document -> snapshot propose
- document -> arbitrage propose
- document -> remboursement propose
- validation -> objet metier actif

## 7.3 Tests de non regression

- les chiffres v2 attendus sur les cas reels
- les deux contrats actuels
- cas de remboursement structure
- cas de coupon confirme

## 8. Contraintes de phase 1

Pour tenir le scope :

- pas d'edition libre de tous les evenements
- pas d'OCR complexe obligatoire
- pas de moteur avancé de rapprochement
- pas de migration automatique complete de la v1
- pas de vue comptable holding

## 9. Point d'entree recommande

Je recommande de commencer par :

1. base v2 minimale
2. GED branchee
3. extraction et validation des snapshots annuels

Pourquoi :

- c'est la vraie source de verite
- cela debloque ensuite les fonds euro, UC et historique officiel
- cela evite de coder trop tot une logique de calcul sur des donnees encore instables

## 10. Definition of done phase 1

La phase 1 est consideree reussie si :

- la GED est exploitable
- les snapshots annuels sont gerables dans l'interface
- les deux contrats sont consultables
- les UC sont correctement valorisees
- les fonds euro affichent officiel + pilotage
- les produits structures ont une fiche editable
- les coupons ambigus sont validables
- les remboursements visibles sont historises
- chaque chiffre principal peut etre relie a une source ou a une formule
