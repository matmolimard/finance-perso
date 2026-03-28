# Application V2 - Specification des Workflows Documentaires et de Validation

Derniere mise a jour: 2026-03-26
Statut: brouillon a valider

Ce document decrit comment un document entre dans la V2 et devient une donnee metier validee.
Il complete :

- [RULES.md](/Users/mathieu/Documents/Developpement/finance-perso/RULES.md)
- [v2_global_spec.md](/Users/mathieu/Documents/Developpement/finance-perso/docs/v2_global_spec.md)
- [v2_functional_spec.md](/Users/mathieu/Documents/Developpement/finance-perso/docs/v2_functional_spec.md)
- [v2_data_spec.md](/Users/mathieu/Documents/Developpement/finance-perso/docs/v2_data_spec.md)
- [v2_screens_spec.md](/Users/mathieu/Documents/Developpement/finance-perso/docs/v2_screens_spec.md)

## 1. Principes generaux

Les workflows V2 suivent 6 regles simples :

- aucun PDF n'est utilise directement dans les calculs sans indexation minimale
- l'extraction automatique est utile, mais ne remplace pas la validation
- les donnees officielles doivent pouvoir etre rattachees a un document source
- une donnee proposee n'est jamais consideree comme verite tant qu'elle n'est pas validee
- en cas de conflit, le document officiel prime sur l'historique interne
- il vaut mieux laisser un objet en attente que de le valider sur une mauvaise hypothese

## 2. Etats transverses

## 2.1 Etat d'un document

- `imported`
- `indexed`
- `classified`
- `archived`

## 2.2 Etat d'un objet extrait

- `extracted`
- `proposed`
- `validated`
- `rejected`
- `superseded`

## 2.3 Regle de base

Les etats doivent rester simples :

- `extracted` = lu automatiquement ou prepare depuis un document
- `proposed` = visible dans l'interface et pret a validation
- `validated` = retenu comme verite metier
- `rejected` = extrait incorrect ou non pertinent
- `superseded` = remplace par une version plus fiable

## 3. Workflow 1 - Ingestion GED

## 3.1 Objectif

Faire entrer un document dans l'application sans encore produire de donnees metier fragiles.

## 3.2 Etapes

1. depot du PDF
2. calcul du hash
3. creation ou mise a jour d'un objet `Document`
4. indexation minimale
5. classement documentaire

## 3.3 Metadonnees minimales exigees

- type de document
- assureur
- contrat si connu
- date du document
- statut
- chemin
- hash

## 3.4 Sortie attendue

Le document devient :

- consultable dans la GED
- filtrable
- utilisable comme source pour extraction

## 4. Workflow 2 - Releve assureur vers snapshot annuel

## 4.1 Objectif

Transformer un releve annuel en `SnapshotYear` valide.

## 4.2 Etapes

1. selection du document type `insurer_statement`
2. extraction automatique si possible :
   - total contrat
   - detail par support
   - fonds euro
   - taux officiels
   - mouvements visibles
   - informations fiscales si applicables
3. creation d'un `SnapshotYear` en statut `proposed`
4. creation des `SnapshotPosition`
5. creation optionnelle de :
   - `SnapshotOperationVisible`
   - `SnapshotOperationLegVisible`
   - `SnapshotFondsEuroTerms`
   - `SnapshotFiscalInfo`
6. verification utilisateur
7. validation

## 4.3 Regles de validation

Avant validation, l'utilisateur doit pouvoir verifier :

- que le contrat est le bon
- que la date de reference est correcte
- que le total contrat est correct
- que les positions visibles sont plausibles
- que les taux fonds euro n'ont pas ete mal lus
- que les donnees fiscales sont bien applicables ou non applicables selon le contrat

## 4.4 Effets de la validation

Quand un snapshot est valide :

- il devient la verite officielle pour la date `31/12/N`
- il peut supplanter une version precedente
- il alimente les vues officielles
- il sert de base aux calculs YTD de l'annee suivante

## 4.5 Cas de divergence

Si le snapshot ne colle pas avec l'historique interne :

- le snapshot valide prime pour l'etat officiel au `31/12`
- l'historique interne reste present pour audit
- l'ecart doit etre visible dans l'interface

## 5. Workflow 3 - Courrier d'arbitrage vers arbitrage valide

## 5.1 Objectif

Transformer un courrier d'arbitrage en evenement `Arbitration`.

## 5.2 Etapes

1. selection d'un document type `arbitration_letter` ou `endorsement`
2. extraction automatique :
   - contrat
   - date
   - montant
   - frais
   - lignes de desinvestissement
   - lignes de reinvestissement
3. creation d'un `Arbitration` en statut `proposed`
4. creation des `ArbitrationLeg`
5. tentative de rapprochement avec les `Asset` et `Position`
6. validation utilisateur

## 5.3 Regles de validation

L'utilisateur doit pouvoir verifier :

- la date retenue
- le montant global
- les jambes `from`
- les jambes `to`
- les frais
- le mapping support interne

## 5.4 Cas particulier

Si le document montre en realite un remboursement structure puis un reinvestissement :

- ne pas forcer l'objet `Arbitration`
- creer d'abord un `StructuredRedemptionEvent`
- puis un arbitrage ou un reinvestissement distinct si le document le montre

## 6. Workflow 4 - Courrier ou releve vers remboursement de structure

## 6.1 Objectif

Transformer un courrier ou un releve en `StructuredRedemptionEvent`.

## 6.2 Etapes

1. selection d'un document source
2. extraction automatique :
   - produit concerne
   - contrat
   - date d'echeance ou de constatation
   - date de valeur
   - montant rembourse ou reinvesti
   - destination eventuelle
   - gain annonce si visible
3. creation d'un `StructuredRedemptionEvent` en statut `proposed`
4. tentative de liaison a la position interne
5. validation utilisateur

## 6.3 Regles de validation

L'utilisateur doit pouvoir verifier :

- le produit concerne
- la bonne nature de l'evenement :
  - autocall
  - echeance
  - remboursement emetteur
- la ou les dates
- le montant
- le support de destination si visible

## 6.4 Effets de la validation

Quand le remboursement est valide :

- la position peut devenir `closed` si le support est integralement sorti
- l'evenement apparait dans l'historique
- la valorisation future du support cesse d'etre produite comme position active

## 7. Workflow 5 - Coupon conditionnel

## 7.1 Objectif

Donner a l'utilisateur un moyen simple de confirmer les cas ambigus, surtout CMS.

## 7.2 Etapes

1. la regle du produit cree des `StructuredCouponEvent` attendus
2. l'application detecte les coupons encore `unknown`
3. l'utilisateur ouvre la file de validation
4. il choisit :
   - `paid`
   - `not_paid`
   - `unknown`
5. il peut rattacher un document source ou une note
6. l'evenement passe en etat valide

## 7.3 Effets de la validation

- mode strict : seuls les coupons `paid` comptent
- mode pilotage : les coupons valides comptent comme confirmes, les autres restent theoriques si la regle le permet

## 7.1 bis - Edition d'une fiche produit structure

## Objectif

Permettre a l'utilisateur de completer ou corriger les caracteristiques metier d'un produit structure depuis l'interface.

## Etapes

1. ouverture de la fiche produit
2. pre-remplissage a partir de :
   - la brochure si elle existe
   - l'ISIN
   - les regles deja presentes
3. edition manuelle des champs utiles :
   - mode coupon
   - mode de paiement du coupon
   - frequence
   - regles lisibles
   - notes
4. sauvegarde en brouillon
5. validation de la fiche produit

## Regles

- la brochure reste le document de reference principal
- l'utilisateur doit pouvoir corriger une extraction imparfaite
- la fiche produit validee devient la base de calcul du support
- toute modification importante doit etre tracable
- en phase 1, cette edition concerne la fiche produit et non l'edition libre de tous les evenements historiques

## Effets de la validation

- mise a jour ou creation du `StructuredRule`
- mise a jour du `rule_source_mode`
- recalcul possible des vues structures en mode strict et pilotage

## 8. Workflow 6 - Taux de pilotage fonds euro

## 8.1 Objectif

Permettre de piloter l'annee en cours simplement.

## 8.2 Etapes

1. l'utilisateur ouvre le contrat
2. il saisit ou modifie le `taux de pilotage`
3. l'application recalcule la valeur de pilotage
4. l'application affiche clairement :
   - la derniere valeur officielle
   - la valeur de pilotage
   - la date de calcul

## 8.3 Regles

- ce taux n'est pas une donnee officielle
- il doit etre trace avec sa date de saisie
- il ne modifie jamais un snapshot valide

## 9. Workflow 7 - Retrait

## 9.1 Objectif

Gerer explicitement une sortie de contrat ou une sortie de support.

## 9.2 Etapes

1. creation manuelle ou extraction documentaire
2. choix du type :
   - `external_withdrawal`
   - `position_exit_inside_contract`
3. saisie de la date et des montants
4. saisie ou validation des jambes `WithdrawalLeg`
5. validation utilisateur

## 9.3 Regles

- un retrait externe impacte les `apports externes nets` et le `TRI`
- une sortie de support restant dans le contrat n'est pas un flux externe
- un remboursement de structure n'est pas un retrait

## 10. Workflow 8 - Rapprochement support / document

## 10.1 Objectif

Faire correspondre les libelles documentaires aux objets internes.

## 10.2 Etapes

1. l'extraction propose un `asset_name_raw`
2. l'application tente un matching sur :
   - ISIN
   - nom exact
   - alias connus
3. si le matching n'est pas fiable :
   - laisser l'objet en attente
   - demander une validation utilisateur

## 10.3 Regle de securite

- ne jamais valider automatiquement un mapping douteux

## 11. Workflow 9 - Historisation

## 11.1 Objectif

Retirer les positions closes des vues principales sans les perdre.

## 11.2 Declencheurs

Une position peut devenir historique apres :

- validation d'un `StructuredRedemptionEvent`
- validation d'un retrait total
- validation d'une cloture explicite

## 11.3 Effets

- la position n'apparait plus dans les ecrans actifs
- elle reste visible dans l'historique
- ses documents restent accessibles
- ses evenements restent auditables

## 12. Files d'attente de validation

La V2 phase 1 doit proposer au minimum 5 files :

1. snapshots annuels proposes
2. arbitrages proposes
3. remboursements de structures proposes
4. coupons conditionnels a confirmer
5. mappings support/document a valider

## 13. Rejet et correction

Si un objet est rejete :

- il garde son lien documentaire
- il sort du calcul
- la raison du rejet doit etre conservee

Si un objet est corrige :

- la version corrigee devient `proposed`
- l'ancienne version peut etre `rejected` ou `superseded`

## 14. Traces d'audit minimum

Chaque validation importante doit garder :

- qui a valide
- quand
- a partir de quel document
- avec quelle note eventuelle

Pour la v2 personnelle, `qui` peut rester simple :

- `user`
- `system`

## 15. Regles de simplification retenues

Pour rester simple en phase 1 :

- pas de validation automatique finale sans revue utilisateur
- pas de moteur heuristique complexe de rapprochement
- pas de reconstruction exhaustive du ledger depuis les documents
- pas de dependance a un OCR complexe si le texte PDF est deja exploitable

## 16. Critere de succes phase 1

Les workflows sont juges suffisants si l'utilisateur peut :

1. importer et indexer un document
2. valider un snapshot annuel
3. valider un arbitrage
4. valider un remboursement de structure
5. confirmer un coupon ambigu
6. saisir un taux de pilotage
7. retrouver le document source derriere chaque chiffre important
