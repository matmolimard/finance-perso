# Regles Metier Patrimoine

Derniere mise a jour: 2026-03-25

Ce document sert de reference metier explicite.
Il doit etre maintenu au fil du temps pour eviter que le code, la base locale et l'interface derivent.

## 1. Capital investi

Le capital investi correspond aux apports externes reels injectes sur les contrats.

Regle metier voulue:
- on raisonne d'abord au niveau contrat, pas au niveau position
- un apport externe est un versement provenant de l'exterieur du contrat
- un arbitrage interne ne cree pas de nouveau capital investi
- un reinvestissement, un coupon recredité, une participation aux benefices ou un passage par un support monetaire ne doivent pas etre comptes comme nouvel apport externe

Contexte connu a ce jour:
- il y a deux contrats HIMALIA
- il y a un contrat SwissLife
- pour SwissLife, l'utilisateur indique un apport externe total de 1 000 000 EUR

Ce qu'il faut afficher idealement:
- apports externes cumules
- apports externes nets si l'on veut soustraire les rachats reellement sortis du contrat
- cout net encore expose uniquement si cette notion est definie separement

## 2. Valeur a date

La valeur a date depend du type d'actif.

### 2.1 Fonds euro

Regle metier voulue:
- on fige les positions a partir des rapports annuels de l'assureur
- la base au 1er janvier est la valeur connue au 31/12 de l'annee precedente
- la valorisation a date combine:
  - le capital connu au 1er janvier
  - le taux de l'annee precedente
  - la plus-value theorique engrangee a date

Intention metier:
- on ne pretend pas recalculer parfaitement le fonds euro
- on s'aligne autant que possible sur les releves de l'assureur
- les donnees annuelles de l'assureur servent a figer l'etat

Points a clarifier plus tard:
- formule exacte d'accrual intrannuel
- traitement des flux intervenant en cours d'annee
- priorite entre valeur de releve, taux declare et cashflows

### 2.2 UC

Regle metier voulue:
- valeur a date = nombre d'UC detenues x valeur de marche a date

Convention pratique:
- on utilise la derniere VL disponible inferieure ou egale a la date de valorisation
- si la VL est stale, l'outil doit l'indiquer explicitement

### 2.3 Produits structures

Regle metier voulue:
- on ne cherche pas a reconstruire une valeur liquidative opaque de l'assureur
- on part de la valeur d'achat
- on ajoute les coupons theoriques
- convention de pilotage: on considere le produit comme gagnant tant qu'on n'a pas une information contraire
- on suppose qu'il sortira avant la liquidation finale, sauf element explicite contredisant cette hypothese

Cas particulier CMS 10 ans:
- les coupons ne doivent pas etre supposes sans validation
- on doit pouvoir declarer explicitement si un coupon a ete verse ou non
- ce point reste a verifier contre la documentation produit et les releves assureur

## 3. Rapports assureur

Les rapports annuels de l'assureur servent de source de verite de reference pour figer les positions a une date donnee.

Usage metier attendu:
- figer la valeur des fonds euros au 31/12
- confirmer les mouvements reels
- confirmer les coupons verses
- confirmer l'etat d'un produit structure a une date donnee

## 4. Principes d'implementation souhaites

- separer strictement:
  - apports externes
  - arbitrages internes
  - revenus/coupons/distributions
  - frais/taxes
  - valeur a date
- ne jamais sommer des "premiers achats de position" comme s'il s'agissait d'apports externes reels
- privilegier le niveau contrat pour les apports
- privilegier le niveau position pour la valorisation
- conserver une trace de la source de chaque valeur importante:
  - rapport assureur
  - brochure produit
  - fichier de marche
  - saisie manuelle

## 5. Questions ouvertes

- formule exacte de valorisation intrannuelle des fonds euros
- convention exacte de versement des coupons pour les produits CMS
- distinction entre:
  - apports externes cumules
  - apports externes nets
  - cout encore expose
  - valeur actuelle
