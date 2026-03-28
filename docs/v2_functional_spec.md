# Application V2 - Specification Fonctionnelle

Derniere mise a jour: 2026-03-26
Statut: brouillon a valider

Ce document formalise la specification fonctionnelle de la V2.
Il doit etre valide avant l'implementation.

Documents lies:
- [RULES.md](/Users/mathieu/Documents/Developpement/finance-perso/RULES.md)
- [v2_global_spec.md](/Users/mathieu/Documents/Developpement/finance-perso/docs/v2_global_spec.md)

## 1. Objectif

La V2 est une application personnelle de suivi patrimonial.

Elle doit permettre a l'utilisateur de suivre ses contrats financiers avec :

- ses propres regles de calcul
- ses propres indicateurs
- une vision lisible des performances
- une vision lisible des frais
- une relation explicite entre les chiffres, les regles et les documents source

La V2 remplit un role proche de Moneypitch, mais :

- sans dependre de ses calculs
- sans opacite sur les indicateurs
- avec une capacite d'audit fine

## 2. Perimetre initial

La V2 demarre uniquement avec les contrats suivants :

- `HIMALIA`
- `SwissLife Capi Stratégic Premium`

Types d'actifs couverts :

- fonds euro
- UC
- produits structures

## 3. Sources de verite

Ordre de priorite retenu :

1. releve assureur
2. courrier assureur
3. saisie manuelle
4. scraping automatise

Sources non retenues comme source primaire :

- MoneyPitch
- back-office assureur

## 4. Principes structurants

- la V2 se construit a cote de la V1
- la V1 reste operationnelle tant que la V2 n'est pas validee
- la vue principale montre uniquement les positions actives
- les positions closes sont historisees dans une vue separee
- les calculs doivent etre simples, lisibles et auditables
- une saisie manuelle explicite est preferable a une heuristique fragile

## 5. Concepts metier

### 5.1 Contrat

Le contrat est l'unite principale de pilotage.

Au niveau contrat, on suit notamment :

- apports externes cumules
- apports externes nets
- valeur actuelle
- performance simple
- TRI
- frais identifies
- coupons verses

### 5.2 Position

La position est l'unite principale de valorisation.

Chaque position appartient a un contrat et correspond a un support.

### 5.3 Snapshot annuel assureur

Le snapshot annuel assureur est la source de verite historique.

Il contient :

- une date de reference, typiquement `31/12/N`
- une date de reception du document
- une date de validation dans l'application
- l'etat du contrat a cette date
- le detail par support
- les frais visibles
- les mouvements visibles
- les documents rattaches

Workflow :

1. document GED recu
2. extraction automatique si possible
3. proposition de snapshot
4. validation manuelle
5. snapshot fige

### 5.4 GED

La GED locale est une brique metier de premier rang.

Elle contient :

- releves annuels assureur
- courriers d'arbitrage
- avenants
- conditions contractuelles
- brochures produits structures

Chaque document doit etre indexe avec au minimum :

- type
- assureur
- contrat
- date
- statut
- chemin
- hash
- notes

### 5.5 Arbitrage

Un arbitrage doit etre modelise explicitement.

Il ne doit pas etre traite comme une simple juxtaposition de mouvements bruts.

Structure cible :

- contrat
- date
- sources
- destinations
- montant
- frais
- document source
- statut de validation

### 5.6 Retrait

Le modele doit distinguer :

- retrait externe hors contrat
- sortie partielle d'un support avec maintien dans le contrat
- arbitrage interne

## 6. Regles de valorisation retenues

### 6.1 Fonds euro

Objectif :

- figer l'historique a partir des releves assureur
- calculer l'annee en cours a partir du dernier snapshot officiel

Regles retenues :

- l'etat officiel au `31/12/N-1` vient du releve assureur
- pour l'annee en cours, l'application affiche :
  - la derniere valeur officielle connue
  - une valeur de pilotage
- la valeur de pilotage repose sur un `taux de pilotage` saisi dans l'interface
- le modele est defini par fonds euro

Decision retenue pour la periode de janvier a fevrier / mars :

- afficher a la fois :
  - la valeur officielle du dernier releve
  - la valeur provisoire de pilotage

Point encore a preciser :

- formule exacte de calcul intra-annuel

### 6.2 UC

Regle retenue :

- valeur a date = nombre de parts x derniere VL connue inferieure ou egale a la date de valorisation

L'interface doit montrer :

- la date de la derniere VL
- un indicateur de fraicheur

### 6.3 Produits structures

Objectif :

- fournir une valeur de pilotage lisible
- ne pas faire semblant de retrouver une vraie valeur liquidative assureur

Regles retenues :

- base = valeur d'achat
- on ajoute les coupons selon les conventions de calcul de l'utilisateur
- on distingue deux modes :
  - `strict`
  - `pilotage`

#### Mode strict

- seuls les coupons confirmes sont comptabilises
- les coupons inconnus ne sont pas supposes

#### Mode pilotage

- les coupons confirmes sont comptabilises
- les coupons theoriques sont ajoutes selon les regles du produit et les conventions utilisateur

#### Cas particulier CMS

Chaque coupon doit avoir un statut explicite :

- verse
- non verse
- inconnu

L'utilisateur doit pouvoir le modifier dans l'interface.

Regle supplementaire retenue :

- un produit structure doit pouvoir etre edite depuis l'interface
- la brochure reste la source documentaire principale
- mais les champs metier du produit ne doivent pas etre bloques dans du YAML ou du code

### 6.4 Performance

La V2 affichera au minimum :

- performance simple
- TRI

Definitions detaillees a preciser avant implementation.

## 7. Temporalites d'analyse

La V2 doit permettre au minimum trois vues temporelles :

- depuis l'ouverture
- depuis le 1er janvier
- par annee passee

Ces vues doivent etre disponibles au niveau :

- global
- contrat
- support

## 8. Indicateurs cibles

L'utilisateur veut une vision de pilotage et d'analyse.

Les indicateurs cibles a afficher sont :

- apports externes cumules
- apports externes nets
- valeur actuelle
- performance simple
- TRI
- frais identifies
- coupons verses
- valeur officielle
- valeur de pilotage

Chaque indicateur doit pouvoir etre explique.

## 9. Auditabilite

Chaque chiffre important doit pouvoir ouvrir un detail `comment ce chiffre est calcule`.

Ce detail doit afficher au minimum :

- la formule ou la regle appliquee
- les donnees source utilisees
- les documents relies si applicable
- les hypotheses de calcul
- le statut du chiffre :
  - officiel
  - calcule
  - estime

## 10. Interface web cible

### 10.1 Role de l'interface

L'interface web est l'interface principale d'usage.

Elle sert a :

- consulter
- piloter
- valider
- corriger
- expliquer

### 10.2 Pages / sections cibles

La V2 doit comporter a terme :

- un tableau de bord global
- une vue contrats
- une vue fonds euro
- une vue UC
- une vue produits structures
- une vue historique
- une vue GED
- une vue regles / parametres

### 10.3 Tableau de bord global

Le tableau de bord global doit afficher :

- les contrats actifs
- les indicateurs principaux
- les alertes
- les valeurs officielles et de pilotage
- le filtre temporel

### 10.4 Vue produits structures

Chaque produit structure doit avoir une fiche lisible avec :

- regle de coupon
- frequence
- mode de paiement :
  - au fil du temps
  - in fine
  - conditionnel
- regle d'autocall
- source brochure
- etat des coupons

### 10.5 Vue historique

Les positions closes ne doivent pas apparaitre dans la vue principale.

Elles doivent apparaitre dans une vue historique dediee avec :

- date de cloture
- valeur de sortie
- performance realisee si calculable
- documents associes

### 10.6 Edition manuelle

L'interface doit permettre au minimum :

- de saisir le taux de pilotage d'un fonds euro
- de confirmer ou refuser un coupon structure
- de saisir / modifier / supprimer un retrait
- de valider un arbitrage detecte
- de valider un snapshot annuel

## 11. GED fonctionnelle

### 11.1 Types documentaires minimum

- releve assureur
- courrier d'arbitrage
- avenant
- document contractuel
- brochure produit structure
- autre courrier assureur

### 11.2 Fonctions attendues

- importer un document
- l'indexer
- le rattacher a un contrat
- le rattacher a un support si pertinent
- le marquer actif / archive
- l'utiliser comme source dans un calcul ou une validation

## 12. Strategie de livraison

La V2 doit etre construite a cote de la V1.

### 12.1 Ce qui reste en V1

- outil courant temporaire
- anciennes commandes et moteurs tant que la V2 n'est pas prete

### 12.2 Ce qui devient V2

- GED structurée
- snapshots assureur
- moteur de pilotage simplifie
- interface web principale
- modele explicite des arbitrages, retraits et coupons

### 12.3 Coexistence

La V1 et la V2 coexistent jusqu'a validation des cas reels.

## 13. Commandes make

Decision retenue :

- on garde des commandes `make`
- mais pas comme interface metier principale

Usage cible de `make` :

- lancer l'app web
- lancer les tests
- importer des documents
- reconstruire des index
- valider les donnees
- lancer des traitements techniques

L'interface web reste l'entree principale pour l'usage metier quotidien.

## 14. Decisions deja validees

- V2 demarre uniquement avec `HIMALIA` et `SwissLife Capi Stratégic Premium`
- priorite des sources fixee
- snapshot annuel detaille valide
- workflow documentaire valide
- affichage officiel + pilotage pour les fonds euros valide
- taux de pilotage par fonds euro retenu
- double mode `strict` / `pilotage` pour les structures retenu
- statuts `verse / non verse / inconnu` pour les coupons conditionnels retenus
- arbitrages explicites retenus
- retraits distingues explicitement retenus
- positions closes hors vue principale retenu
- extraction auto + validation manuelle retenue
- vues temporelles `depuis l'ouverture`, `depuis le 1er janvier`, `par annee` retenues
- auditabilite obligatoire retenue

## 15. Questions encore ouvertes

Les points suivants restent a trancher ou a formaliser plus precisement :

- formule exacte de valorisation intrannuelle des fonds euros
- schema exact des retraits
- schema exact des arbitrages
- regles par type de produit structure
- difference fonctionnelle complete entre coupon verse au fil du temps et coupon verse in fine

## 16. Prochaine etape recommandee

Avant implementation, il faut produire trois specs detaillees :

1. specification des donnees V2
2. specification des ecrans V2
3. specification des workflows documentaires et de validation
