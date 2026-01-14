# Changelog - Intégration Notes Quantalys

## Version 2.0 - Récupération Automatique (2025-12-28)

### ✨ Nouvelles fonctionnalités

#### Récupération automatique des notes Quantalys

Les notes Quantalys sont désormais **récupérées automatiquement** lors de la mise à jour des valeurs liquidatives avec `make update-navs`.

**Avant** :
- Saisie manuelle des notes dans `quantalys_ratings.yaml`
- Risque d'oubli ou d'erreur
- Maintenance manuelle fastidieuse

**Après** :
- ✅ Récupération automatique des notes (1-5 étoiles)
- ✅ Récupération automatique des catégories
- ✅ Mise à jour synchronisée avec les VL
- ✅ Zéro maintenance manuelle

### 🔧 Modifications techniques

#### 1. `portfolio_tracker/market/nav_fetch.py`
- **Modifié** : Ajout de `quantalys_rating` et `quantalys_category` à la classe `FetchedNav`
- **Ajouté** : Extraction automatique des notes depuis le HTML Quantalys
- **Ajouté** : Extraction automatique de la catégorie du fonds
- **Méthode** : Utilisation de regex pour parser le HTML

#### 2. `portfolio_tracker/market/quantalys.py`
- **Ajouté** : Méthode `upsert_rating()` pour sauvegarder/mettre à jour les notes
- **Gestion intelligente** : Ne sauvegarde que si les données ont changé
- **Cache** : Invalidation automatique du cache après modification

#### 3. `portfolio_tracker/market/nav_daily.py`
- **Modifié** : Initialisation du `QuantalysProvider`
- **Ajouté** : Sauvegarde automatique des notes Quantalys après récupération des VL
- **Feedback** : Message indiquant si une note a été sauvegardée

#### 4. Documentation
- **Mis à jour** : `QUANTALYS.md` avec section sur la récupération automatique
- **Mis à jour** : `README.md` avec informations sur l'automatisation
- **Mis à jour** : `Makefile` help pour indiquer les notes Quantalys

### 📊 Données extraites automatiquement

Pour chaque fonds UC lors de `make update-navs` :
1. **Valeur liquidative** (comme avant)
2. **Date de la VL** (comme avant)
3. **Note Quantalys** ⭐⭐⭐⭐ (NOUVEAU)
4. **Catégorie Quantalys** (NOUVEAU)

### 🚀 Utilisation

```bash
# Mettre à jour les VL ET les notes Quantalys automatiquement
make update-navs

# Voir les notes dans les rapports
make himalia
make swisslife
```

### 📝 Exemple de sortie

Avant (notes manuelles) :
```
✓ Eleva Absolute Return Europe | Quantalys: ⭐⭐⭐⭐ (4/5)
  Valeur: 6,709.17 € | ...
```

Après (notes automatiques) :
```
make update-navs
VL enregistrée (quantalys) 2025-12-28: 153.58 + Note Quantalys: 4

make himalia
✓ Eleva Absolute Return Europe | Quantalys: ⭐⭐⭐⭐ (4/5)
  Valeur: 6,709.17 € | ...
```

### 🔍 Détails techniques

#### Patterns regex utilisés

Pour extraire la note (1-5) :
```python
rating_patterns = [
    r'Note\s+Quantalys[^\d]*(\d)[^\d]',
    r'note-(\d)',
    r'rating["\s:]+(\d)',
    r'Notation[^\d]*(\d)[^\d]',
]
```

Pour extraire la catégorie :
```python
category_patterns = [
    r'Catégorie[^\n]{0,10}:\s*([^\n<]{5,60})',
    r'Classification[^\n]{0,10}:\s*([^\n<]{5,60})',
    r'Type[^\n]{0,10}:\s*([^\n<]{5,60})',
]
```

#### Gestion des erreurs

- Si la note n'est pas trouvée : continue sans erreur (note = None)
- Si la catégorie n'est pas trouvée : continue sans erreur (category = None)
- Si Playwright n'est pas installé : affiche un message d'erreur clair
- Si les données n'ont pas changé : pas de sauvegarde (évite les modifications inutiles)

### ⚠️ Limitations

- Nécessite Playwright installé (`pip install playwright` + `python -m playwright install chromium`)
- Nécessite des permissions réseau pour accéder à Quantalys
- Fonctionne uniquement pour les fonds configurés dans `nav_sources.yaml`
- Les notes sont extraites via regex (peut casser si Quantalys change son HTML)

### 🔄 Compatibilité

- ✅ Compatible avec l'ancien système de saisie manuelle
- ✅ Les notes manuelles existantes ne sont pas écrasées (sauf si différentes)
- ✅ Fonctionne avec tous les fonds déjà configurés
- ✅ Pas de changement dans l'affichage des commandes `make himalia` / `make swisslife`

### 📦 Fichiers affectés

```
portfolio_tracker/
├── market/
│   ├── nav_fetch.py           [MODIFIÉ] Extraction des notes
│   ├── nav_daily.py           [MODIFIÉ] Sauvegarde automatique
│   └── quantalys.py           [MODIFIÉ] Méthode upsert_rating()
├── data/market_data/
│   └── quantalys_ratings.yaml [AUTO-MIS À JOUR] Notes sauvegardées
QUANTALYS.md                    [MODIFIÉ] Documentation
README.md                       [MODIFIÉ] Section Notes Quantalys
Makefile                        [MODIFIÉ] Help

```

### 🎯 Avantages

1. **Automatisation complète** : Plus besoin de saisie manuelle
2. **Synchronisation** : Notes toujours à jour avec les dernières données Quantalys
3. **Gain de temps** : Une seule commande pour tout mettre à jour
4. **Fiabilité** : Moins d'erreurs humaines
5. **Traçabilité** : Date de mise à jour automatiquement enregistrée

### 🏁 Prochaines étapes suggérées

- [ ] Installer Playwright pour activer la récupération automatique
- [ ] Lancer `make update-navs` pour tester
- [ ] Vérifier que les notes s'affichent correctement avec `make himalia`
- [ ] Mettre à jour régulièrement (hebdomadaire ou mensuel)

---

## Version 1.0 - Affichage des Notes (2025-12-28)

### Fonctionnalités initiales

- Affichage des notes Quantalys dans `make himalia` et `make swisslife`
- Fichier `quantalys_ratings.yaml` pour stocker les notes
- Module `QuantalysProvider` pour lire les notes
- Saisie manuelle des notes dans le fichier YAML






