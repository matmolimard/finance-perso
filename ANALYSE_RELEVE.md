# Analyse de cohérence avec le relevé de situation au 31/12/2024

## 📄 Données du relevé (PDF)

- **Date du relevé** : 31/12/2024
- **Épargne totale** : 1 038 151,04 €
- **Épargne UC** : 652 693,57 €
- **Épargne fonds euro** : 385 457,47 €
- **Taux fonds euro 2024** : 3,25% (net de frais)

## 📊 Données enregistrées dans le système

### Fonds Euro (pos_swiss_001)

**Valeur actuelle** : 195 039,79 € (units_held)

**Mouvements enregistrés** :
1. **09/11/2022** : +360 000,00 € (versement initial) ✅
2. **31/12/2022** : -336,05 € (frais de gestion) ✅
3. **31/12/2023** : -2 442,38 € (frais de gestion) ✅
4. **31/12/2024** : -2 521,87 € (frais de gestion) ✅
5. **31/12/2024** : +14 671,02 € (participation aux bénéfices) ✅
6. **12/12/2025** : -173 962,34 € (rachat partiel) ✅

**Valeur reconstituée au 31/12/2024** :
- Valeur actuelle : 195 039,79 €
- + Rachat du 12/12/2025 : 173 962,34 €
- **= 369 002,13 €**

## 🔍 Écart détecté

**Écart fonds euro** : 385 457,47 - 369 002,13 = **16 455,34 €** (4,26%)

## ✅ Vérification des mouvements

Tous les mouvements du fonds euro présents dans `mouvements_swisslife.txt` sont bien enregistrés dans `positions.yaml` :

| Date | Type | Montant | Statut |
|------|------|---------|--------|
| 09/11/2022 | Versement initial | +360 000,00 € | ✅ Enregistré |
| 31/12/2022 | Frais de gestion | -336,05 € | ✅ Enregistré |
| 31/12/2023 | Frais de gestion | -2 442,38 € | ✅ Enregistré |
| 31/12/2024 | Frais de gestion | -2 521,87 € | ✅ Enregistré |
| 31/12/2024 | Participation bénéfices | +14 671,02 € | ✅ Enregistré |
| 12/12/2025 | Rachat partiel | -173 962,34 € | ✅ Enregistré |

## 💡 Hypothèses sur l'écart

L'écart de **16 455,34 €** peut s'expliquer par :

1. **Rachat partiel non enregistré** entre le 31/12/2024 et le 12/12/2025
   - Un rachat partiel aurait réduit la valeur, mais l'écart est positif (relevé > calculé)
   - ❌ Cette hypothèse ne correspond pas à l'écart positif

2. **Participation aux bénéfices supplémentaire** non enregistrée
   - Une participation supplémentaire augmenterait la valeur
   - ✅ Cette hypothèse correspond à l'écart positif
   - Montant manquant : ~16 455 €

3. **Mouvement dans le fichier texte non importé**
   - Tous les mouvements du fichier texte sont enregistrés
   - ❌ Cette hypothèse ne semble pas valide

4. **Erreur dans le relevé ou dans les données**
   - Le relevé pourrait indiquer une valeur incorrecte
   - Ou `units_held` pourrait être incorrect

5. **Différence de date de valorisation**
   - Le relevé est arrêté au 31/12/2024
   - Il pourrait y avoir une différence de quelques jours

## 🎯 Actions recommandées

1. **Vérifier les relevés intermédiaires** (trimestriels ou mensuels) entre 31/12/2024 et 12/12/2025 pour identifier un mouvement manquant

2. **Vérifier la valeur exacte de `units_held`** dans l'espace client Swiss Life au 31/12/2024

3. **Vérifier s'il y a eu une participation aux bénéfices supplémentaire** en 2024 ou début 2025

4. **Comparer avec d'autres relevés** pour voir si l'écart se maintient ou se corrige

## 📝 Calculs détaillés

### Calcul depuis les mouvements enregistrés

**Achats totaux jusqu'au 31/12/2024** :
- 09/11/2022 : +360 000,00 €
- 31/12/2024 : +14 671,02 € (participation)
- **Total achats** : 374 671,02 €

**Retraits totaux jusqu'au 31/12/2024** :
- 31/12/2022 : -336,05 € (frais)
- 31/12/2023 : -2 442,38 € (frais)
- 31/12/2024 : -2 521,87 € (frais)
- **Total retraits** : 5 300,30 €

**Net investi** : 374 671,02 - 5 300,30 = **369 370,72 €**

**Valeur au 31/12/2024 (relevé)** : **385 457,47 €**

**Performance** : 385 457,47 - 369 370,72 = **16 086,75 €**

**Taux de performance** : (385 457,47 / 369 370,72 - 1) × 100 = **4,36%**

### Comparaison avec le taux déclaré

Le relevé indique un **taux de 3,25%** pour 2024, mais le calcul donne **4,36%** sur la période totale (2022-2024).

Cela suggère que :
- Le taux de 3,25% est pour l'année 2024 uniquement
- Le taux moyen sur la période 2022-2024 est plus élevé (~4,36%)

## ✅ Conclusion

**Tous les mouvements du fichier texte sont correctement enregistrés.**

L'écart de **16 455,34 €** nécessite une vérification supplémentaire :
- Vérifier les relevés intermédiaires
- Vérifier la valeur exacte dans l'espace client Swiss Life
- Identifier s'il y a eu un mouvement non documenté dans le fichier texte

