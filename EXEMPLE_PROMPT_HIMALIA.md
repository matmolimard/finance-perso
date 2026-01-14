# Exemple de Prompt pour HIMALIA

Ce document montre comment les données du profil HIMALIA sont intégrées dans le prompt envoyé à l'IA.

## 1. Données du profil HIMALIA (depuis `profiles.yaml`)

```yaml
- name: "HIMALIA"
  contract_name: "HIMALIA"
  insurer: "Generali"
  risk_tolerance: "moderate"
  performance_priority: true
  max_volatility: null
  preferred_asset_classes: ["structured_product", "uc_fund"]
  excluded_asset_classes: []
  description: "Investissement personnel - Accepte un risque modéré mais recherche de la performance"
```

## 2. Intégration dans le prompt (code dans `prompts.py`)

Le prompt est construit dans la fonction `build_advisory_prompt()` qui extrait les données du profil depuis `summary.risk_profile` :

```python
profile = summary.risk_profile

# Section 1: Contexte et objectifs
context_section = f"""
## CONTEXTE D'INVESTISSEMENT

**Profil:** {profile.name}
**Contrat:** {profile.contract_name} ({profile.insurer})
**Tolérance au risque:** {profile.risk_tolerance}
**Priorité performance:** {"Oui" if profile.performance_priority else "Non"}
**Description:** {profile.description or "Non spécifiée"}
"""
```

## 3. Exemple de prompt complet avec les valeurs HIMALIA

Voici comment la section "CONTEXTE D'INVESTISSEMENT" apparaîtrait dans le prompt final pour HIMALIA :

```
## CONTEXTE D'INVESTISSEMENT

**Profil:** HIMALIA
**Contrat:** HIMALIA (Generali)
**Tolérance au risque:** moderate
**Priorité performance:** Oui
**Description:** Investissement personnel - Accepte un risque modéré mais recherche de la performance
```

## 4. Utilisation du profil dans les instructions

Le profil est également utilisé dans la section "INSTRUCTIONS" pour adapter les recommandations :

```python
- Respecter le profil de risque ({"modéré/performance" if profile.performance_priority else "conservateur"})
```

Pour HIMALIA avec `performance_priority: true`, cela génère :
```
- Respecter le profil de risque (modéré/performance)
```

## 5. Flux complet

1. **Chargement du profil** (`cli.py` ligne 2160-2165) :
   - Les profils sont chargés depuis `profiles.yaml` via `load_profiles()`
   - Le profil HIMALIA est trouvé par son nom

2. **Analyse du portefeuille** (`cli.py` ligne 2204) :
   - `analyzer.analyze_profile(profile)` crée un `PortfolioSummary`
   - Le `PortfolioSummary` contient `risk_profile=profile` (ligne 173 de `analyzer.py`)

3. **Capture de la vue globale** (`cli.py` ligne 2252-2268) :
   - Le mapping entre profil et portefeuille est utilisé (HIMALIA → HIMAL, SwissLife → swiss)
   - La sortie de `global_view()` est capturée avec `io.StringIO()` et `redirect_stdout()`
   - Cette sortie est passée à `build_advisory_prompt()`

4. **Construction du prompt** (`cli.py` ligne 2274) :
   - `build_advisory_prompt(summary, market_context, global_view_output)` est appelé
   - Les données du profil sont extraites via `summary.risk_profile` (ligne 26 de `prompts.py`)
   - La sortie de `make global` est intégrée dans une section dédiée

5. **Intégration dans le prompt** :
   - Les champs du profil sont injectés dans la section "CONTEXTE D'INVESTISSEMENT"
   - La sortie de `make global` est capturée et intégrée dans la section "VUE GLOBALE DU PORTEFEUILLE"
   - Le profil influence aussi les instructions d'analyse (ligne 124 de `prompts.py`)

## 6. Mapping profil → portefeuille

Le système utilise un mapping pour associer chaque profil à son portefeuille correspondant :

| Profil | Portefeuille (pour `make global`) |
|--------|-----------------------------------|
| HIMALIA | HIMAL |
| SwissLife | swiss |

Ce mapping est défini dans `cli.py` (ligne 2200) et permet de capturer automatiquement la sortie de `make global PORTFOLIO=...` pour chaque profil.

## 7. Champs du profil utilisés

| Champ du profil | Utilisation dans le prompt |
|----------------|---------------------------|
| `name` | Affiche le nom du profil |
| `contract_name` | Affiche le nom du contrat |
| `insurer` | Affiche l'assureur |
| `risk_tolerance` | Affiche la tolérance au risque |
| `performance_priority` | Affiche "Oui" ou "Non" et influence les instructions |
| `description` | Affiche la description du profil |

**Note:** Les champs `preferred_asset_classes`, `excluded_asset_classes` et `max_volatility` sont définis dans le profil mais ne sont **pas actuellement utilisés** dans le prompt. Ils pourraient être ajoutés pour enrichir les instructions.

## 8. Intégration de la sortie de `make global`

Depuis la dernière mise à jour, le prompt inclut maintenant la sortie complète de la commande `make global` pour le portefeuille correspondant :

- Pour `make advice PROFILE=HIMALIA` → inclut la sortie de `make global PORTFOLIO=HIMAL`
- Pour `make advice PROFILE=SwissLife` → inclut la sortie de `make global PORTFOLIO=swiss`

Cette sortie est capturée automatiquement et intégrée dans une section dédiée du prompt, permettant à l'IA d'avoir une vision complète et détaillée de toutes les positions (fonds euros, UC, produits structurés) avec leurs récapitulatifs.

## 9. Exemple de prompt complet (avec données fictives)

Voici un exemple de prompt complet qui serait envoyé à l'IA pour HIMALIA :

```
## CONTEXTE D'INVESTISSEMENT

**Profil:** HIMALIA
**Contrat:** HIMALIA (Generali)
**Tolérance au risque:** moderate
**Priorité performance:** Oui
**Description:** Investissement personnel - Accepte un risque modéré mais recherche de la performance

## VUE GLOBALE DU PORTEFEUILLE (sortie de `make global`)

Cette section contient la sortie complète de la commande `make global PORTFOLIO=HIMAL` qui affiche
toutes les positions (fonds euros, UC, produits structurés) avec leurs détails et récapitulatifs.

```
[Sortie complète de make global PORTFOLIO=HIMAL avec toutes les tables et récapitulatifs]
```

**IMPORTANT:** Cette vue globale contient les données EXACTES calculées avec la même logique que la commande CLI.
Utilise ces données pour avoir une vision complète du portefeuille avant de faire tes recommandations.

## RÉSUMÉ ANALYTIQUE DU PORTEFEUILLE

**IMPORTANT: Ces données sont calculées avec la MÊME logique que la commande CLI (make swisslife).**
**Les chiffres sont EXACTS et correspondent à l'affichage du portefeuille.**

**Valeur totale:** 150,000.00 €
**Capital investi (externe):** 140,000.00 € (somme des apports externes uniquement, lots marqués external=true)
**P&L total:** 10,000.00 € (+7.14%)

**Note:** Le capital investi affiché correspond aux apports externes (nouveaux fonds injectés).
Le P&L individuel de chaque position est calculé sur son capital investi réel (achats - rachats - frais).

**Allocation par type d'actif:**
- structured_product: 60.0%
- uc_fund: 40.0%

**Positions détaillées:**

### 1. Produit Structuré XYZ
- **Position ID:** pos_001
- **Type:** structured_product
- **Valeur actuelle:** 90,000.00 €
- **Capital investi:** 85,000.00 €
- **P&L:** 5,000.00 € (+5.88%)
- **Durée de détention:** 12 mois
- **ISIN:** FR0012345678

### 2. Fonds UC ABC
- **Position ID:** pos_002
- **Type:** uc_fund
- **Valeur actuelle:** 60,000.00 €
- **Capital investi:** 55,000.00 €
- **P&L:** 5,000.00 € (+9.09%)
- **Durée de détention:** 18 mois
- **Rating Quantalys:** 4/5
- **ISIN:** FR0098765432

## CONJONCTURE DE MARCHÉ

**Taux CMS 10Y:** 3.25% (au 2025-01-15)

## INSTRUCTIONS

Analyse ce portefeuille et fournis des recommandations d'actions concrètes. 

**Format de réponse attendu (JSON strict):**
```json
{
  "summary": "Analyse globale du portefeuille en 2-3 phrases",
  "recommendations": [
    {
      "position_id": "pos_xxx",
      "asset_name": "Nom de l'actif",
      "action": "reinforce" | "reduce" | "maintain" | "exit",
      "reasoning": "Explication détaillée de la recommandation (2-3 phrases)",
      "priority": "high" | "medium" | "low"
    }
  ],
  "market_concerns": [
    "Liste des préoccupations sur le marché ou le portefeuille"
  ],
  "opportunities": [
    "Liste des opportunités identifiées"
  ]
}
```

**Critères d'analyse IMPORTANTS:**
- ⚠️ Les chiffres fournis sont EXACTS et calculés avec la MÊME logique que la commande CLI
- ⚠️ Le P&L total est calculé sur le capital externe (apports externes uniquement)
- ⚠️ Le P&L individuel de chaque position est calculé sur son capital investi réel (achats - rachats - frais)
- ⚠️ NE PAS inventer ou interpréter différemment les chiffres fournis - ils sont déjà calculés correctement
- Respecter le profil de risque (modéré/performance)
- Analyser la performance relative de chaque position par rapport à son type d'actif
- Considérer la durée de détention (positions longues vs courtes)
- Évaluer la diversification et l'allocation par type d'actif
- Identifier les positions sous-performantes ou sur-performantes de manière factuelle
- Proposer des actions concrètes (renforcer, réduire, maintenir, sortir) basées UNIQUEMENT sur les données fournies

**Actions possibles:**
- **reinforce**: Augmenter la position (si performance bonne et alignée avec le profil)
- **reduce**: Réduire la position (si sur-exposition, sous-performance, ou risque trop élevé)
- **maintain**: Maintenir la position actuelle (équilibre optimal)
- **exit**: Sortir complètement (si inadapté au profil ou très sous-performant)

Réponds UNIQUEMENT en JSON valide, sans texte avant ou après.
```

**Points clés sur l'intégration du profil :**
- Les données du profil HIMALIA apparaissent dans la section "CONTEXTE D'INVESTISSEMENT" (lignes 1-6)
- Le profil influence les instructions : "Respecter le profil de risque (modéré/performance)" car `performance_priority: true`
- Le profil guide l'IA pour adapter ses recommandations au contexte d'investissement personnel avec recherche de performance

