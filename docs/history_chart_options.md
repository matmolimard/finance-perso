# Options des graphiques (commande `history`)

Avec **plotext** installé (`make install-charts`), les graphiques en terminal utilisent les options ci‑dessous.

## Type de graphe (`--chart-type`)

| Type   | Description              |
|--------|--------------------------|
| **line** | Courbe (défaut)        |
| **bar**  | Diagramme à bâtons (barres verticales) |

Le diagramme à bâtons est souvent plus lisible pour des séries courtes (ex. avec `--from` / `--to` sur un mois). Pour des centaines de points, la courbe reste plus claire.

Exemple :
```bash
make history VALUE=CMS_EUR_10Y FROM=2026-01-01 CHART_TYPE=bar
portfolio-tracker history --chart-type bar VALUE=helium_selection --from 2026-03-01
```

## Marqueur de courbe (`--chart-marker`)

| Marqueur   | Rendu typique | Usage recommandé        |
|-----------|----------------|--------------------------|
| **dot**   | • • • • •      | **Traits fins** (défaut) |
| **sd**    | Petits blocs   | Courbe fine à blocs      |
| **braille** | Points braille | Très fin, haute résolution |
| **fhd**   | Blocs pleins   | Haute définition         |
| **hd**    | Blocs (défaut plotext) | Épais         |
| dollar    | $              | Symboles                 |
| euro      | €              | Symboles                 |
| bitcoin   | ฿              | Symboles                 |
| at        | @              |                          |
| heart     | ♥              |                          |
| smile     | ☺              |                          |
| star      | ❋              |                          |
| cross     | ♰              |                          |
| zero … nine | Chiffres     |                          |

Pour une **courbe fine** : `dot`, `sd` ou `braille`.  
Pour une **courbe épaisse** : `hd` ou `fhd`.

Exemple :
```bash
make history VALUE=helium_selection FROM=2026-03-01 --chart-marker=braille
portfolio-tracker history --all --from 2026-01-01 --chart-marker=dot
```

## Couleur (`--chart-color`)

Couleurs courantes : `green`, `blue`, `red`, `cyan`, `magenta`, `yellow`, `white`, `black`, `orange`, `gray`.  
Voir la liste complète avec `python3 -c "import plotext as plt; plt.colors()"`.

## Désactiver le graphe

`--no-chart` : affiche uniquement le tableau et le résumé, sans graphique.
