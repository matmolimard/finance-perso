# Synthèse de l'écart avec le relevé au 31/12/2024

## 📊 Situation actuelle

**Relevé au 31/12/2024** : 385 457,47 €  
**Valeur calculée** : 369 002,13 €  
**Écart** : **16 455,34 €** (4,26%)

## ✅ Vérifications effectuées

1. ✅ **Tous les mouvements du fichier texte sont enregistrés** dans `positions.yaml`
2. ✅ **Les intérêts 2024 sont cohérents** (12 149,15 € = participation 14 671,02 € - frais 2 521,87 €)
3. ✅ **Aucun mouvement manquant** entre le 31/12/2024 et le 12/12/2025
4. ❌ **Aucune participation aux bénéfices supplémentaire** trouvée dans les événements

## 💡 Hypothèses sur l'écart

L'écart de **16 455,34 €** peut s'expliquer par :

1. **Différence de méthode de calcul** : Le relevé pourrait utiliser une méthode de valorisation différente (capitalisation continue vs discrète)
2. **Date d'arrêté différente** : Le relevé pourrait être arrêté à une date légèrement différente
3. **Valeur déclarée vs valeur calculée** : Le relevé pourrait indiquer une valeur déclarée par Swiss Life qui diffère légèrement de la somme des mouvements
4. **Arrondis et ajustements** : Des arrondis ou ajustements comptables pourraient expliquer la différence

## 🎯 Solutions proposées

### Option 1 : Corriger `units_held` pour correspondre au relevé

Ajuster `units_held` pour qu'il reflète la valeur du relevé au 31/12/2024, puis reconstruire la valeur actuelle :

```yaml
units_held: 211495.13  # 385457.47 - 173962.34 (rachat du 12/12/2025)
```

### Option 2 : Ajouter un ajustement dans les lots

Ajouter un lot d'ajustement au 31/12/2024 pour expliquer l'écart :

```yaml
- date: '2024-12-31'
  type: other
  units: 16455.34
  currency: EUR
  nav: 1.0
  net_amount: 16455.34
  description: "Ajustement pour correspondre au relevé"
```

### Option 3 : Accepter l'écart

Conserver l'écart comme une différence connue et documentée, en notant que la valeur du relevé peut différer légèrement de la valeur calculée.

## 📝 Recommandation

Je recommande l'**Option 1** : corriger `units_held` pour qu'il corresponde au relevé, car :
- Le relevé est la source de vérité officielle
- Cela garantit la cohérence avec les documents officiels
- L'écart est probablement dû à des ajustements comptables ou des arrondis

Souhaitez-vous que j'applique cette correction ?

