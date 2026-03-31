# Note de cadrage - Connecteur Himalia

## Objectif

Construire un connecteur Himalia autonome, fiable et traçable, capable de :

- se connecter à l'espace client Generali / Himalia
- récupérer la valorisation assureur courante
- récupérer les positions par support
- récupérer les mouvements visibles sur l'espace client
- récupérer les avenants, relevés et documents contractuels
- produire un export structuré réutilisable par un moteur de valorisation "source assureur"

Cette note ne branche rien à l'application. Elle fixe seulement les règles métier et techniques pour le futur connecteur Himalia.

## Sources disponibles

### 1. Page contrat

Page observée :

- `GET /b2b2c/epargne/CoeDetCon`

Informations utiles :

- numéro de contrat assureur
- souscripteur / assuré
- date d'effet
- situation du contrat
- profil de gestion
- épargne atteinte à date
- détail des supports avec :
  - date de valeur
  - valeur liquidative
  - nombre de parts
  - contre-valeur
  - prix d'achat moyen
  - plus ou moins-value
  - performance

### 2. Page mouvements

Page observée :

- `GET /b2b2c/epargne/CoeLisMvt`

Informations utiles :

- historique visible des mouvements
- date d'effet
- nature du mouvement
- montant brut
- identifiant de détail `numMvt` sur certaines lignes

Limite observée :

- la page n'affiche qu'une partie de l'historique par pagination
- il faudra compléter par navigation supplémentaire ou documents si l'on veut un historique exhaustif

### 3. Page documents

Page observée :

- `GET /b2b2c/epargne/CoeConAve`

Informations utiles :

- avenants et courriers
- relevés de situation
- attestations fiscales type IFI
- identifiants d'ouverture PDF dans les liens Javascript

Historique visible observé :

- `2026`: arbitrages, distribution de dividendes, opération sur titres, relevé de situation
- `2025`: versement libre, distribution de dividendes, arbitrage, opération sur titres, IFI
- `2023`: conditions particulières, saisie d'affaire nouvelle

## Règle de fiabilité principale

Le moteur assureur Himalia doit être un moteur **à points d'ancrage assureur**.

### Principe

- la page contrat et les relevés de situation sont des **checkpoints officiels**
- les mouvements servent à expliquer les flux entre deux checkpoints
- un mouvement antérieur à un checkpoint ne doit jamais être réinjecté dans le stock de ce checkpoint

### Conséquence

On ne doit jamais faire :

- `valorisation snapshot + somme de tous les mouvements passés`

On doit faire :

1. prendre le dernier checkpoint assureur connu
2. considérer que tous les flux antérieurs sont déjà absorbés dans ce checkpoint
3. appliquer uniquement les flux postérieurs à ce checkpoint
4. recalibrer complètement dès qu'un relevé ou une valorisation web plus récente est disponible

## Gestion anti double comptage

### Checkpoints

Sources de checkpoint possibles :

- page contrat Himalia
- relevé de situation PDF

Chaque checkpoint doit porter :

- `reference_date`
- `official_total_value`
- `positions`
- `source_type`
- `source_document_id` ou `source_page_artifact`

### Flux

Chaque flux doit porter :

- `effective_date`
- `operation_type`
- `gross_amount`
- `source_kind`
- `included_in_next_snapshot`

### Règle

Un flux `F` est :

- `active_for_rollforward = true` s'il est postérieur au dernier checkpoint retenu
- `already_absorbed = true` s'il est antérieur ou égal au checkpoint suivant connu

## Contraintes de connexion

### Authentification

Le connecteur Himalia a les contraintes suivantes :

- passage conseillé par `EntAccBou` puis `Accès client`
- FriendlyCaptcha sur la page de connexion
- OTP SMS lors de certaines recréations de session

### Stratégie opérationnelle

Le connecteur ne doit pas redemander un OTP à chaque exécution normale.

La stratégie validée est :

1. exécution standard avec session persistée `session.json`
2. si la session est expirée :
   - premier run jusqu'à l'étape OTP
   - sauvegarde d'une session intermédiaire `session_otp_pending.json`
   - second run avec `HIMALIA_OTP_CODE=...`
3. après validation OTP :
   - persistance de la nouvelle session
   - reprise des exécutions quotidiennes sans OTP tant que la session reste valide

Conclusion :

- un run quotidien Docker est réaliste **si** la session persistée dure plusieurs jours
- le renouvellement de session reste un flux à part entière

## Export structuré attendu

Comme pour SwissLife, le connecteur Himalia doit produire un fichier `himalia_collected.json` contenant :

- `contract`
- `visible_metrics`
- `positions`
- `operations`
- `documents`
- `page_artifacts`
- `api_sources`
- `summary`

## Conclusion

Oui, un connecteur Himalia exploitable est faisable.

Mais il ne doit pas être vu comme un simple scraper de page :

- il doit gérer une session longue
- il doit gérer un cycle OTP de reprise
- il doit combiner valorisation courante, mouvements visibles et documents
- il doit rester piloté par des checkpoints assureur pour éviter tout double comptage
