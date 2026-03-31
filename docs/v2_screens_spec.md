# Application V2 - Specification des Ecrans

Derniere mise a jour: 2026-03-26
Statut: brouillon a valider

Ce document decrit les ecrans minimums de la V2 phase 1.
Il complete :

- [README.md — règles métier](../README.md)
- [v2_global_spec.md](v2_global_spec.md)
- [v2_functional_spec.md](v2_functional_spec.md)
- [v2_data_spec.md](v2_data_spec.md)

## 1. Principes UX retenus

La V2 phase 1 suit quelques principes simples :

- la web app est l'interface principale
- les positions actives sont visibles par defaut
- les positions closes sont sorties du parcours principal
- chaque chiffre important doit pouvoir s'expliquer
- les donnees officielles et les donnees de pilotage doivent etre distinguees visuellement
- l'interface doit rester simple et locale, sans login

## 2. Navigation phase 1

La navigation minimum retenue contient 6 ecrans :

1. tableau de bord
2. detail contrat
3. detail support
4. GED
5. validation et evenements
6. historique

## 3. Ecran 1 - Tableau de bord

## 3.1 Objectif

Donner une vue immediate du patrimoine actif selon les regles v2.

## 3.2 Filtres globaux

Filtres attendus :

- contrat : `Tous`, `HIMALIA`, `SwissLife Capi Strategique Premium`
- periode : `Depuis l'ouverture`, `Depuis le 1er janvier`, `Par annee`
- mode de valorisation :
  - `officiel`
  - `pilotage`
  - `strict structures`
  - `pilotage structures`
- date de reference

## 3.3 Indicateurs principaux

Cards attendues :

- `Apports externes cumules`
- `Apports externes nets`
- `Valeur actuelle`
- `Performance simple`
- `TRI`
- `Frais identifies`
- `Coupons verses`

Regles :

- les valeurs officielles et de pilotage doivent etre identifiables
- si une valeur n'est pas fiable, l'ecran doit le dire explicitement

## 3.4 Blocs de synthese

Blocs attendus :

- synthese par contrat
- synthese par type d'actif
- alertes
- donnees en attente de validation

Exemples d'alertes :

- coupon CMS a confirmer
- snapshot annuel non valide
- taux de pilotage fonds euro non renseigne
- brochure structuree manquante

## 3.5 Actions disponibles

Depuis le tableau de bord, l'utilisateur doit pouvoir :

- ouvrir le detail d'un contrat
- ouvrir le detail d'un support
- ouvrir une alerte
- ouvrir un evenement a valider
- basculer entre valeur officielle et valeur de pilotage

## 4. Ecran 2 - Detail contrat

## 4.1 Objectif

Donner une lecture complete d'un contrat, a la fois historique et de pilotage.

## 4.2 Blocs attendus

- resume du contrat
- indicateurs du contrat
- fonds euro
- UC actives
- produits structures actifs
- evenements recents
- documents rattaches

## 4.3 Bloc fonds euro

Affichages attendus :

- derniere valeur officielle connue
- date du dernier snapshot officiel
- valeur de pilotage a date
- taux officiel N-1
- taux de pilotage N
- methode de calcul

Actions attendues :

- renseigner ou modifier le taux de pilotage
- ouvrir le releve assureur source
- ouvrir le detail du calcul

## 4.4 Bloc UC

Affichages attendus :

- support
- ISIN
- quantite
- derniere VL
- date de VL
- valeur
- performance simple
- TRI si disponible
- fraicheur de la VL

Actions attendues :

- ouvrir le detail support
- ouvrir l'historique de VL

## 4.5 Bloc produits structures

Affichages attendus :

- support
- ISIN
- statut
- valeur d'achat
- coupons confirmes
- coupons theoriques selon mode
- valeur de pilotage
- prochain evenement connu
- mode coupon

Actions attendues :

- ouvrir la fiche produit
- editer la fiche produit
- confirmer un coupon
- ouvrir la brochure
- ouvrir un remboursement ou un arbitrage lie

## 4.6 Evenements du contrat

Liste des evenements recents :

- arbitrages
- remboursements de structures
- retraits
- distributions
- versements externes

Pour chaque evenement :

- date
- type
- montant
- document source
- statut de validation

## 5. Ecran 3 - Detail support

## 5.1 Objectif

Expliquer un support precis et comment sa valeur est calculee.

## 5.2 Contenu minimum

- identite du support
- contrat rattache
- position active ou closee
- methode de valorisation
- historique annuel officiel
- historique de marche si UC
- regles produit si structure
- documents lies

## 5.3 Cas fonds euro

Le detail doit montrer :

- valeurs officielles par annee
- taux officiels par annee
- valeur de pilotage courante
- detail du prorata en cours d'annee

## 5.4 Cas UC

Le detail doit montrer :

- quantite detenue
- serie de VL
- valeurs officielles annuelles
- explication de la valeur actuelle

## 5.5 Cas produit structure

Le detail doit montrer :

- brochure
- regles lisibles du produit
- mode coupon
- coupons attendus
- coupons confirmes
- evenements de remboursement
- difference entre mode strict et mode pilotage

Le detail doit aussi permettre l'edition des champs metier du produit :

- nom affiche
- ISIN
- mode de coupon
- mode de paiement du coupon
- frequence
- texte de regle coupon
- texte de regle autocall
- texte de regle capital
- source brochure
- notes

Actions attendues :

- `Editer le produit`
- `Enregistrer comme brouillon`
- `Valider la fiche produit`
- `Ouvrir la brochure source`
- `Voir les impacts sur la valorisation`

Limite retenue en phase 1 :

- l'utilisateur peut editer la fiche produit
- mais il ne peut pas encore editer librement tous les evenements du produit
- les evenements restent validables, pas pleinement modifiables

## 6. Ecran 4 - GED

## 6.1 Objectif

Donner une vue claire de tous les documents utiles.

## 6.2 Filtres attendus

- assureur
- contrat
- type de document
- annee
- actif
- statut

## 6.3 Categories visibles

- releves annuels
- courriers d'arbitrage
- avenants
- documents contractuels
- brochures produits structures
- autres courriers

## 6.4 Actions attendues

- ouvrir un document
- voir son indexation
- corriger ses metadonnees
- voir les objets metier qui en dependent
- voir s'il a deja ete transforme en snapshot ou evenement

## 7. Ecran 5 - Validation et evenements

## 7.1 Objectif

Permettre de valider les extractions et les cas ambigus sans toucher au code.

## 7.2 Objets a valider

- snapshots annuels proposes
- arbitrages extraits
- remboursements de structures
- coupons conditionnels
- retraits
- mappings support/document
- fiches produits structures

## 7.3 Actions attendues

- valider
- rejeter
- corriger
- lier a un document
- lier a une position
- laisser en attente

## 7.4 Cas particulier des coupons conditionnels

L'ecran doit permettre de passer un coupon a :

- `verse`
- `non verse`
- `inconnu`

Avec :

- date de validation
- document source optionnel
- note libre

## 8. Ecran 6 - Historique

## 8.1 Objectif

Sortir du pilotage quotidien tout ce qui n'est plus actif, sans rien perdre.

## 8.2 Contenu attendu

- positions closes
- snapshots annuels valides
- remboursements de structures passes
- retraits passes
- arbitrages passes

## 8.3 Regles

- les positions closes ne sont pas visibles dans les vues principales
- elles restent consultables et auditables
- aucun compactage automatique en phase 1

## 9. Ecrans ou fonctions reportes en phase 2

Fonctions non obligatoires pour la phase 1 :

- edition visuelle avancee de graphiques
- exports complexes
- vue comptable holding
- automatisations avancees
- edition massive de donnees
- moteur de rapprochement semi-automatique sophistique

## 10. Parcours critiques phase 1

Parcours a reussir absolument :

1. deposer un releve assureur -> proposer un snapshot -> valider
2. consulter un contrat -> comprendre la valeur actuelle -> ouvrir le calcul
3. consulter un produit structure -> voir sa regle -> confirmer un coupon
4. voir un remboursement de structure -> comprendre son reinvestissement
5. renseigner un taux de pilotage fonds euro -> voir la valeur de pilotage se mettre a jour
6. consulter l'historique sans polluer la vue principale

## 11. Decision de perimetre phase 1

La phase 1 est consideree suffisante si :

- les deux contrats du perimetre sont consultables
- les snapshots annuels sont gerables
- les fonds euro, UC et structures sont lisibles
- la GED est exploitable
- les evenements critiques sont validables
- les chiffres principaux sont auditables
