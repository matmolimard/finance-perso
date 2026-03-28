# Application V2 - Cadrage Global

Derniere mise a jour: 2026-03-26

Ce document sert de base produit pour la V2.
Il decrit le perimetre, les objectifs, les principes de conception et les grandes briques fonctionnelles.

Il complete [RULES.md](/Users/mathieu/Documents/Developpement/finance-perso/RULES.md), qui reste la reference des regles metier.

## 1. Vision

L'application est un outil personnel de suivi patrimonial.

Son objectif n'est pas de faire du trading ni de reproduire exactement l'interface des assureurs.
Son objectif est de fournir une vision claire, fiable et pilotable des placements financiers, avec :

- les methodes de calcul choisies par l'utilisateur
- les indicateurs voulus par l'utilisateur
- des sources explicites et auditables
- une presentation simple, stable et orientee usage reel

L'application doit remplir une fonction proche de Moneypitch, mais avec :

- les propres conventions de calcul de l'utilisateur
- une transparence totale sur les hypotheses
- une capacite a relier chaque valeur a une source documentaire

## 2. Probleme a resoudre

Les outils existants donnent une vision partielle ou opaque :

- les valorisations sont presentees sans maitrise complete de la methode
- la performance des supports n'est pas toujours lisible
- les frais sont difficiles a isoler
- les mouvements internes et externes sont facilement confondus
- les documents assureur ne sont pas relies proprement aux calculs

La V2 doit corriger cela.

## 3. Perimetre fonctionnel

Le perimetre de la V2 couvre :

- les contrats d'assurance vie et de capitalisation suivis par l'utilisateur
- les fonds euros
- les unites de compte
- les produits structures
- les documents assureur et brochures produits
- les snapshots annuels issus des releves assureur
- les calculs de valorisation et de performance selon les regles de l'utilisateur

Le perimetre ne couvre pas, pour le moment :

- la synchronisation automatique avec les assureurs
- une comptabilite fiscale exhaustive
- une estimation de marche complexe des produits structures
- la multi-utilisation ou les roles d'acces

## 4. Sources de verite

La V2 repose sur une hierarchie de sources simple.

### 4.1 Source de verite historique

Les releves annuels de l'assureur sont la source de verite historique.

Ils servent a figer, a une date donnee :

- les positions existantes
- les valeurs constatees
- l'etat des fonds euros
- les informations de coupons ou remboursements visibles sur les supports

### 4.2 Source de verite documentaire

La GED locale contient :

- les releves assureur
- les courriers d'arbitrage
- les avenants
- les conditions particulieres / garanties
- les brochures de produits structures

Chaque valeur importante doit pouvoir etre rattachee a une ou plusieurs pieces.

### 4.3 Source de calcul courant

Pour l'annee en cours, l'application calcule une valeur de pilotage a partir :

- du dernier snapshot annuel disponible
- des regles metier explicites
- des donnees de marche ou parametres renseignes manuellement

## 5. Principes de conception

La V2 suit les principes suivants :

- priorite a la simplicite
- pas d'heuristique metier opaque si une saisie simple peut faire mieux
- pas de reconstruction complexe quand un releve assureur existe
- separation stricte entre donnees declarees, donnees calculees et donnees estimees
- distinction claire entre historique et pilotage courant
- toutes les hypotheses doivent etre visibles

## 6. Concepts metier majeurs

### 6.1 Contrat

Le contrat est le niveau principal de pilotage.

Le capital investi s'entend d'abord au niveau contrat.
Les arbitrages internes n'augmentent pas le capital investi.

### 6.2 Position

La position est le niveau principal de valorisation.

Chaque position correspond a un support detenue dans un contrat, avec son propre historique et sa propre methode de calcul.

### 6.3 Snapshot annuel

Un snapshot annuel represente l'etat fige d'un contrat a la date d'un releve assureur.

Il sert de point de depart pour les calculs de l'annee en cours.

### 6.4 GED

La GED n'est pas un simple rangement de PDF.
Elle fait partie du modele fonctionnel.

Elle doit permettre :

- de retrouver les pieces
- de les rattacher aux contrats et actifs
- de justifier les calculs
- de relire l'historique

## 7. Regles de valorisation cibles

Le detail des regles vit dans [RULES.md](/Users/mathieu/Documents/Developpement/finance-perso/RULES.md).
La V2 s'aligne sur les principes suivants.

### 7.1 Fonds euro

- l'etat historique vient des releves assureur
- l'annee en cours est calculee a partir du dernier etat assureur
- le taux utilise pour l'annee en cours est renseigne explicitement par l'utilisateur
- l'application doit distinguer la valeur constatee et la valeur de pilotage

### 7.2 UC

- valeur a date = nombre de parts x derniere VL connue
- la fraicheur de la VL doit etre visible

### 7.3 Produits structures

- pas de pretention a reconstruire une vraie valeur liquidative assureur
- la valeur de pilotage suit les conventions definies par l'utilisateur
- les regles de chaque produit doivent etre lisibles
- les brochures PDF doivent etre disponibles dans la GED
- les coupons conditionnels doivent pouvoir etre confirmes ou refuses manuellement

## 8. Comportements attendus de l'interface

### 8.1 Vue principale

La vue principale doit montrer les positions actives uniquement.

Elle doit privilegier :

- une vue par contrat
- une vue par type d'actif
- une vue par support
- des indicateurs simples et comprehensibles

### 8.2 Historique

Les positions closes ou inactives ne doivent pas polluer la vue principale.

Elles doivent vivre dans une zone historique dediee, avec :

- leur date de cloture
- leur valeur de sortie si connue
- leur performance realisee si calculable
- les documents associes

### 8.3 Edition manuelle

L'interface doit permettre de gerer explicitement :

- le taux fonds euro de l'annee en cours
- la confirmation d'un coupon structure
- la saisie d'un retrait
- la correction ou qualification d'un mouvement ambigu

## 9. Donnees que la V2 doit maitriser

### 9.1 Donnees metier

- contrats
- actifs
- positions
- snapshots annuels
- regles de valorisation
- retraits
- arbitrages
- coupons confirmes / refuses / inconnus

### 9.2 Donnees documentaires

- releves annuels assureur
- courriers d'arbitrage
- avenants
- conditions contractuelles
- brochures produits structures

## 10. Indicateurs cibles

La V2 doit permettre d'afficher des indicateurs choisis et compris.

Exemples :

- apports externes cumules
- apports externes nets
- valeur actuelle
- performance globale
- performance par support
- frais identifies
- coupons verses
- valeur de pilotage versus valeur constatee

Les definitions exactes devront etre documentees avant affichage.

## 11. Ecarts assumes avec la V1

La V2 ne doit pas reprendre aveuglement l'architecture actuelle.

En particulier :

- la V1 surexploite les mouvements comme source de verite comptable globale
- la V1 utilise encore des heuristiques sur les flux externes et internes
- la V1 ne donne pas une place centrale aux snapshots assureur
- la V1 n'integre pas encore pleinement la GED comme brique metier

La V2 doit simplifier.

## 12. Grandes briques de la V2

### 12.1 GED locale

Stockage et indexation des documents :

- assureur
- contrat
- date
- type de document
- statut actif / archive
- empreinte

### 12.2 Snapshots assureur

Representation structuree d'un etat annuel par contrat.

### 12.3 Moteur de pilotage courant

Calcul de l'annee en cours a partir :

- du dernier snapshot
- des regles metier
- des donnees de marche
- des parametres saisis

### 12.4 Interface web

Interface locale sans login permettant :

- consultation
- validation
- saisie des parametres manuels
- navigation documentaire

## 13. Questions ouvertes

Les principaux arbitrages structurants ont ete valides :

- le format du snapshot annuel est specifie dans [v2_data_spec.md](/Users/mathieu/Documents/Developpement/finance-perso/docs/v2_data_spec.md)
- la formule de valorisation intrannuelle des fonds euros est retenue
- la gestion des retraits, arbitrages, coupons conditionnels et remboursements structures est definie
- la GED sera navigable sous forme de liste, sans page dediee par document en phase 1
- l'interface V2 n'aura pas de menu lateral permanent ; seulement un burger
- la densite cible est standard
- pour les produits structures, la brochure est obligatoire pour considerer la documentation exploitable

Il reste surtout a ajuster :

- la liste exacte des champs presentes par defaut dans la fiche produit structure
- le niveau de detail visible immediatement dans les tables et fiches

## 14. Usage de cette doc

Cette doc sert a :

- cadrer la V2
- arbitrer les choix d'architecture
- eviter de recoder des heuristiques metier non validees
- servir de reference avant de definir les schemas et les ecrans

Ordre recommande pour la suite :

1. stabiliser la vision et les concepts
2. definir les schemas de donnees V2
3. definir les ecrans et parcours utilisateur
4. seulement ensuite, implementer
