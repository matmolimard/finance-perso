# Installation de Playwright pour la récupération automatique des notes Quantalys

## Pourquoi Playwright est nécessaire ?

Quantalys utilise du JavaScript pour afficher les données de ses pages. Un simple téléchargement HTTP ne suffit pas car le contenu n'est pas encore généré. Playwright simule un vrai navigateur (mode headless) qui exécute le JavaScript et permet d'accéder au contenu complet.

## Installation

### Étape 1 : Installer le module Python

```bash
pip install playwright
```

### Étape 2 : Installer le navigateur Chromium

```bash
python -m playwright install chromium
```

Cette commande télécharge Chromium (environ 300 Mo).

### Étape 3 : Vérifier l'installation

```bash
python3 -c "from playwright.sync_api import sync_playwright; print('✓ Playwright installé et fonctionnel')"
```

Si vous voyez "✓ Playwright installé et fonctionnel", c'est bon !

## Utilisation

Une fois installé, la commande `make update-navs` récupérera automatiquement :
- ✅ Les valeurs liquidatives
- ✅ Les notes Quantalys (1-5 étoiles)
- ✅ Les catégories des fonds

```bash
# Mettre à jour toutes les VL et notes
make update-navs
```

## Test de l'installation

Pour tester que tout fonctionne :

```bash
# Tester sur un seul fonds
python3 -m portfolio_tracker.cli --data-dir portfolio_tracker/data update-uc-navs
```

Vous devriez voir dans la sortie :
```
VL enregistrée (quantalys) 2025-12-28: 153.58 + Note Quantalys: 4
```

## Dépannage

### Erreur : "Playwright n'est pas installé"

Suivez les étapes 1 et 2 ci-dessus.

### Erreur : "Executable doesn't exist"

Relancez :
```bash
python -m playwright install chromium
```

### Les notes ne s'affichent pas

1. Vérifiez que Playwright est installé :
```bash
python3 -c "import playwright; print('OK')"
```

2. Vérifiez que les fonds sont configurés dans `nav_sources.yaml` avec `headless: true`

3. Relancez `make update-navs` avec des permissions réseau

### Performance lente

C'est normal : Playwright lance un vrai navigateur pour chaque fonds. Pour ~10 fonds, comptez 1-2 minutes.

## Alternative sans Playwright

Si vous ne pouvez pas installer Playwright, vous pouvez :
1. Saisir manuellement les notes dans `quantalys_ratings.yaml`
2. Les notes s'afficheront dans `make himalia` et `make swisslife`

Mais vous perdez la récupération automatique.

## Sécurité

- Playwright télécharge depuis les serveurs officiels de Microsoft
- Le navigateur s'exécute en mode headless (pas d'interface graphique)
- Aucune donnée personnelle n'est envoyée
- Navigation uniquement sur quantalys.com

## Ressources

- Documentation Playwright : https://playwright.dev/python/
- Site Quantalys : https://www.quantalys.com






