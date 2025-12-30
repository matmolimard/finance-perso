# Vérification de units_held

## Calcul manuel depuis les lots

| Date | Type | Units | Net Amount |
|------|------|-------|------------|
| 2022-11-09 | buy | +360 000,00 | +360 000,00 € |
| 2022-12-31 | fee | -336,05 | -336,05 € |
| 2023-12-31 | fee | -2 442,38 | -2 442,38 € |
| 2024-12-31 | fee | -2 521,87 | -2 521,87 € |
| 2024-12-31 | buy | +14 671,02 | +14 671,02 € |
| 2025-12-12 | other | -174 330,93 | -173 962,34 € |

## Somme des units

**Somme des units** : 
360 000 - 336,05 - 2 442,38 - 2 521,87 + 14 671,02 - 174 330,93
= 360 000 - 5 300,30 + 14 671,02 - 174 330,93
= 369 370,72 - 174 330,93
= **195 039,79**

**units_held dans le YAML** : **195 039,79**

✅ **Les units sont correctes !**

## Vérification des montants

**Somme des net_amount** :
360 000 - 336,05 - 2 442,38 - 2 521,87 + 14 671,02 - 173 962,34
= 360 000 - 5 300,30 + 14 671,02 - 173 962,34
= 369 370,72 - 173 962,34
= **195 408,38 €**

⚠️ **Différence entre units et montants** :
- Units : 195 039,79
- Montants : 195 408,38 €
- Différence : **368,59 €**

Cette différence vient du rachat du 12/12/2025 :
- Units : -174 330,93
- Montant : -173 962,34 €
- Différence : **368,59 €**

Cela suggère que le rachat a été fait avec un léger écart entre les units et le montant net (peut-être des frais ou un arrondi).

## Valeur au 31/12/2024 (reconstituée)

**Méthode 1 : Depuis units_held actuel**
- units_held actuel : 195 039,79
- Units après 31/12/2024 : -174 330,93 (rachat du 12/12/2025)
- **Valeur au 31/12/2024** : 195 039,79 + 174 330,93 = **369 370,72**

**Méthode 2 : Depuis les montants**
- Montant actuel : 195 408,38 €
- Montant rachat : 173 962,34 €
- **Valeur au 31/12/2024** : 195 408,38 + 173 962,34 = **369 370,72 €**

## Comparaison avec le relevé

**Relevé au 31/12/2024** : **385 457,47 €**  
**Valeur calculée** : **369 370,72 €**  
**Écart** : **16 086,75 €** (4,17%)

## Conclusion

✅ **units_held est correctement calculé** à partir des lots  
⚠️ **Il y a un écart de 16 086,75 €** avec le relevé qui reste inexpliqué

L'écart pourrait être dû à :
1. Une différence dans la méthode de calcul (capitalisation continue vs discrète)
2. Des ajustements comptables non documentés
3. Une différence de date d'arrêté

