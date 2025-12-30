# 🔧 Dépannage : Les notes Quantalys ne sont pas récupérées

## Problème identifié

Vous avez signalé que les notes Quantalys ne sont pas récupérées automatiquement lors de `make update-navs`.

## Cause principale : Playwright n'est pas installé ❌

J'ai vérifié et **Playwright n'est pas installé** sur votre système. C'est la raison pour laquelle les notes ne peuvent pas être récupérées.

### Pourquoi Playwright est nécessaire ?

Quantalys utilise du JavaScript pour afficher le contenu de ses pages. Sans Playwright :
- ❌ Les pages Quantalys ne s'affichent pas complètement
- ❌ Les notes et catégories ne peuvent pas être extraites
- ❌ Seul le HTML brut (incomplet) est accessible

Avec Playwright :
- ✅ Un vrai navigateur (headless) est lancé
- ✅ Le JavaScript s'exécute et génère le contenu complet
- ✅ Les notes et catégories peuvent être extraites

## Solution : Installer Playwright

### Option 1 : Installation rapide (recommandée) 🚀

J'ai créé un script d'installation automatique :

```bash
cd /Users/mathieu/Documents/Developpement/finance-perso
./QUICK_INSTALL_QUANTALYS.sh
```

Ce script va :
1. Installer le module Python Playwright
2. Télécharger et installer Chromium (~300 Mo)
3. Vérifier que tout fonctionne

**Durée** : 2-3 minutes

### Option 2 : Installation manuelle

```bash
# Étape 1 : Installer le module
pip3 install playwright

# Étape 2 : Installer Chromium
python3 -m playwright install chromium

# Étape 3 : Vérifier
python3 -c "from playwright.sync_api import sync_playwright; print('✓ OK')"
```

## Après l'installation

Une fois Playwright installé, lancez :

```bash
make update-navs
```

Vous devriez voir dans la sortie :
```
VL enregistrée (quantalys) 2025-12-28: 153.58 + Note Quantalys: 4
VL enregistrée (quantalys) 2025-12-28: 102.35 + Note Quantalys: 3
...
```

## Vérifier que les notes sont bien récupérées

```bash
# Vérifier le fichier des notes
cat portfolio_tracker/data/market_data/quantalys_ratings.yaml

# Voir les notes dans les rapports
make himalia
make swisslife
```

## Problèmes secondaires résolus

### 1. Catégories mal extraites ✅

Vous aviez des catégories corrompues comme :
```
quantalys_category: performance, risque, etc." data-helper-explanation="" data-t
```

**Correction effectuée** : J'ai amélioré les expressions régulières pour :
- ✅ Mieux détecter les vraies catégories
- ✅ Filtrer les fragments de code HTML
- ✅ Nettoyer les espaces et caractères parasites

### 2. Notes non mises à jour ✅

**Correction effectuée** : Le système ne sauvegarde maintenant que si :
- ✅ La note a changé
- ✅ La catégorie a changé
- ✅ Le nom du fonds a changé

Sinon, le fichier reste inchangé (évite les modifications inutiles).

## Alternative sans Playwright

Si vous ne pouvez ou ne voulez pas installer Playwright :

### Option A : Saisie manuelle

Éditez directement `portfolio_tracker/data/market_data/quantalys_ratings.yaml` :

```yaml
ratings:
  - isin: "LU1331971769"
    name: "Eleva Absolute Return Europe"
    quantalys_rating: 4
    quantalys_category: "Actions Long/Short Europe"
    last_update: "2025-12-28"
    notes: "Note globale Quantalys"
```

Les notes s'afficheront quand même dans `make himalia` et `make swisslife`.

### Option B : Ne pas afficher les notes

Si vous ne voulez pas de notes du tout, laissez le fichier vide :

```yaml
ratings: []
```

## État actuel du système

- ✅ Affichage des notes dans `make himalia` et `make swisslife` : **FONCTIONNE**
- ❌ Récupération automatique avec `make update-navs` : **NE FONCTIONNE PAS** (Playwright manquant)
- ✅ Code d'extraction amélioré : **PRÊT** (attend juste Playwright)

## Prochaines étapes

1. **Installer Playwright** (voir ci-dessus)
2. **Tester la récupération** : `make update-navs`
3. **Vérifier les résultats** : `make himalia`

## Besoin d'aide ?

- 📖 Documentation complète : [INSTALLATION_PLAYWRIGHT.md](INSTALLATION_PLAYWRIGHT.md)
- 📖 Guide Quantalys : [QUANTALYS.md](QUANTALYS.md)
- 📝 Changelog : [CHANGELOG_QUANTALYS.md](CHANGELOG_QUANTALYS.md)

---

## Résumé en 30 secondes

```bash
# Problème : Playwright manquant
# Solution :
./QUICK_INSTALL_QUANTALYS.sh

# Puis :
make update-navs
make himalia
```

Voilà ! 🎉





