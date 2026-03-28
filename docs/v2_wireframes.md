# Application V2 - Wireframes Phase 1

Derniere mise a jour: 2026-03-26
Statut: brouillon a valider

Ce document propose des wireframes basse fidelite pour la V2 phase 1.
Ils servent a valider :

- la structure des ecrans
- les zones principales
- les filtres
- les actions critiques

Ils ne definissent pas encore le design final.

## 1. Tableau de bord

```text
+----------------------------------------------------------------------------------+
| Portfolio V2                                                                    |
| Contrat [Tous v]   Periode [Depuis l'ouverture v]   Mode [Pilotage v]           |
| Date de reference [26/03/2026]                                                  |
+----------------------------------------------------------------------------------+

+-------------------+-------------------+-------------------+----------------------+
| Apports externes  | Apports externes  | Valeur actuelle   | Performance simple   |
| cumules           | nets              |                   |                      |
| 1 140 000 EUR     | 1 140 000 EUR     | 1 308 000 EUR     | +168 000 / +14.7%    |
+-------------------+-------------------+-------------------+----------------------+

+-------------------+-------------------+-------------------+----------------------+
| TRI               | Frais identifies  | Coupons verses    | Etat des donnees     |
| 6.8%              | 13 650 EUR        | 2 700 EUR         | 3 alertes            |
+-------------------+-------------------+-------------------+----------------------+

+--------------------------------------+-------------------------------------------+
| Synthese par contrat                 | Alertes / validations                      |
| HIMALIA                        ...   | - Snapshot 2025 HIMALIA a valider          |
| SwissLife Capi Strategique ... ...  | - Coupon CMS a confirmer                   |
|                                      | - Taux pilotage SwissLife non renseigne    |
+--------------------------------------+-------------------------------------------+

+----------------------------------------------------------------------------------+
| Repartition par type d'actif                                                  |
| Fonds euro ...   UC ...   Structures ...                                       |
+----------------------------------------------------------------------------------+

+----------------------------------------------------------------------------------+
| Evenements recents                                                             |
| 28/02/2026  Remboursement structure  ...   [Ouvrir]                            |
| 26/02/2026  Arbitrage                 ...   [Ouvrir]                            |
+----------------------------------------------------------------------------------+
```

## 2. Detail contrat

```text
+----------------------------------------------------------------------------------+
| Contrat : HIMALIA                                      [Actif] [GED] [Historique]|
| Assureur : Generali     Ouverture : 06/12/2023         Holder : individual       |
+----------------------------------------------------------------------------------+

+-------------------+-------------------+-------------------+----------------------+
| Valeur officielle | Valeur pilotage   | Performance simple| TRI                  |
| au 31/12/2025     | au 26/03/2026     |                   |                      |
+-------------------+-------------------+-------------------+----------------------+

+----------------------------------------------------------------------------------+
| Fonds euro                                                                     |
| Derniere valeur officielle : 72 124,42 EUR                                     |
| Taux officiel 2025 : 3,40%                 Taux pilotage 2026 : [3,00    ]      |
| Valeur pilotage : 73 0xx EUR               [Modifier taux] [Voir calcul]        |
+----------------------------------------------------------------------------------+

+----------------------------------------------------------------------------------+
| UC actives                                                                      |
| Support                           ISIN          Qtte    VL      Date     Valeur  |
| D Rend Distri Fev 25              ...           ...     ...     ...      ...     |
| BDL Rempart C                     ...           ...     ...     ...      ...     |
| [Voir support]                                                                  |
+----------------------------------------------------------------------------------+

+----------------------------------------------------------------------------------+
| Produits structures actifs                                                      |
| Support                Statut     Valeur achat  Coupons  Valeur pilotage  Action |
| D Rend Distri Fev 25   actif      ...           ...      ...              Ouvrir |
+----------------------------------------------------------------------------------+

+----------------------------------------------------------------------------------+
| Evenements recents                                                              |
| Date        Type                  Montant       Statut        Source              |
| 28/02/2026  Arbitrage             53 339,18     validated     courrier           |
| 19/02/2026  Remboursement struct. 27 233,90     confirmed     courrier           |
+----------------------------------------------------------------------------------+
```

## 3. Detail support

```text
+----------------------------------------------------------------------------------+
| Support : D Rendt CA Div Forf 0,9e 0225                                        |
| ISIN : FRIP00000YI4     Contrat : HIMALIA     Type : structured_product         |
+----------------------------------------------------------------------------------+

+--------------------------------------+-------------------------------------------+
| Identite                              | Valorisation                              |
| Statut : cloture / remboursé          | Mode strict : ...                         |
| Date d'entree : 03/02/2025            | Mode pilotage : ...                       |
| Date de sortie : 16/02/2026           | Methode : remboursement structure         |
+--------------------------------------+-------------------------------------------+

+----------------------------------------------------------------------------------+
| Regles du produit                                                                 |
| Coupon mode : conditional_periodic                                               |
| Coupon payment mode : in_fine                                                    |
| Regle de coupon : ...                                                            |
| Regle d'autocall : ...                                                           |
| [Ouvrir brochure]                                                                |
+----------------------------------------------------------------------------------+

+----------------------------------------------------------------------------------+
| Coupons / evenements                                                             |
| Date obs.   Date paiement   Statut     Montant   Source                          |
| ...         ...             paid       ...       brochure / courrier / manuel    |
| Remboursement : 16/02/2026 -> 27 233,90 EUR                                      |
+----------------------------------------------------------------------------------+

+----------------------------------------------------------------------------------+
| Documents lies                                                                    |
| Brochure | Courrier de remboursement | Snapshot annuel | Arbitrage suivant       |
+----------------------------------------------------------------------------------+
```

## 4. GED

```text
+----------------------------------------------------------------------------------+
| GED                                                                              |
| Assureur [Tous v] Contrat [Tous v] Type [Tous v] Annee [Tous v] Statut [Tous v] |
+----------------------------------------------------------------------------------+

+----------------------------------------------------------------------------------+
| Documents                                                                         |
| Date        Type                  Contrat     Actif         Statut    Action      |
| 28/02/2026  avenant               HIMALIA     Bouygues ...  active    Ouvrir      |
| 20/02/2026  releve annuel         SwissLife   -             active    Ouvrir      |
| 04/02/2025  avenant               HIMALIA     CA/Bouygues   archived  Ouvrir      |
+----------------------------------------------------------------------------------+

+----------------------------------------------------------------------------------+
| Document selectionne                                                              |
| Metadonnees                                                                       |
| - type                                                                           |
| - assureur                                                                       |
| - contrat                                                                        |
| - actif                                                                          |
| - annee                                                                          |
| - hash                                                                           |
| [Corriger indexation] [Voir objets lies]                                         |
+----------------------------------------------------------------------------------+
```

## 5. Validation et evenements

```text
+----------------------------------------------------------------------------------+
| Validation                                                                       |
| Onglet [Snapshots] [Coupons] [Arbitrages] [Remboursements] [Retraits]           |
+----------------------------------------------------------------------------------+

+----------------------------------------------------------------------------------+
| File d'attente                                                                    |
| Type            Date        Contrat     Resume                     Action         |
| Snapshot        02/03/2026  HIMALIA     Releve 31/12/2025          Ouvrir        |
| Coupon CMS      ...         ...         Statut inconnu             Ouvrir        |
| Remboursement   19/02/2026  HIMALIA     CA 0,9e 0225               Ouvrir        |
+----------------------------------------------------------------------------------+

+----------------------------------------------------------------------------------+
| Validation detail                                                                  |
| Donnees extraites                                                                  |
| [Valider] [Rejeter] [Corriger] [Lier document] [Laisser en attente]              |
+----------------------------------------------------------------------------------+
```

## 6. Historique

```text
+----------------------------------------------------------------------------------+
| Historique                                                                       |
| Contrat [Tous v] Type [Tous v] Periode [Toutes v]                                |
+----------------------------------------------------------------------------------+

+----------------------------------------------------------------------------------+
| Positions closes                                                                  |
| Support                       Contrat     Date sortie   Valeur sortie   Action    |
| Callable Note Taux Fixe ...   HIMALIA     03/01/2025    94 598,28      Ouvrir    |
| D Rendt CA Div Forf 0,9e ...  HIMALIA     16/02/2026    27 233,90      Ouvrir    |
+----------------------------------------------------------------------------------+

+----------------------------------------------------------------------------------+
| Snapshots annuels valides                                                         |
| 31/12/2023   31/12/2024   31/12/2025                                             |
+----------------------------------------------------------------------------------+
```

## 7. Points UX a arbitrer ensuite

Decisions retenues pour la phase 1 :

- densite standard
- pas de menu lateral permanent
- navigation reduite a un burger
- GED en mode liste

Points encore libres pour la suite :

- le niveau de detail affiche par defaut
- la place des graphiques
- le look final exact
