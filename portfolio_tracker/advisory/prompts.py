"""
Prompts - Templates de prompts pour l'IA
"""
from typing import Dict, Any, Optional
from datetime import date
from .analyzer import PortfolioSummary
from .profiles import RiskProfile
from ..market.rates import RatesProvider
from pathlib import Path


def build_advisory_prompt(
    summary: PortfolioSummary,
    market_context: Optional[Dict[str, Any]] = None
) -> str:
    """
    Construit le prompt complet pour l'IA
    
    Args:
        summary: Résumé du portefeuille
        market_context: Contexte de marché additionnel (taux, etc.)
        
    Returns:
        Prompt formaté pour l'IA
    """
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
    
    # Section 2: État actuel du portefeuille
    portfolio_section = f"""
## ÉTAT ACTUEL DU PORTEFEUILLE

**IMPORTANT: Ces données sont calculées avec la MÊME logique que la commande CLI (make swisslife).**
**Les chiffres sont EXACTS et correspondent à l'affichage du portefeuille.**

**Valeur totale:** {summary.total_value:,.2f} €
**Capital investi (externe):** {summary.total_invested:,.2f} € (somme des apports externes uniquement, lots marqués external=true)
**P&L total:** {summary.total_pnl:,.2f} € ({summary.total_pnl_percent:+.2f}%)

**Note:** Le capital investi affiché correspond aux apports externes (nouveaux fonds injectés).
Le P&L individuel de chaque position est calculé sur son capital investi réel (achats - rachats - frais).

**Allocation par type d'actif:**
"""
    for asset_type, percentage in sorted(summary.asset_allocation.items(), key=lambda x: -x[1]):
        portfolio_section += f"- {asset_type}: {percentage:.1f}%\n"
    
    portfolio_section += "\n**Positions détaillées:**\n\n"
    
    # Trier par valeur décroissante
    sorted_positions = sorted(summary.positions, key=lambda x: -x.current_value)
    
    for i, pos in enumerate(sorted_positions, 1):
        portfolio_section += f"""
### {i}. {pos.asset_name}
- **Position ID:** {pos.position_id}
- **Type:** {pos.asset_type}
- **Valeur actuelle:** {pos.current_value:,.2f} €
- **Capital investi:** {pos.invested_amount:,.2f} €
- **P&L:** {pos.pnl:,.2f} € ({pos.pnl_percent:+.2f}%)
- **Durée de détention:** {pos.holding_period_months} mois
"""
        if pos.quantalys_rating:
            portfolio_section += f"- **Rating Quantalys:** {pos.quantalys_rating}\n"
        if pos.isin:
            portfolio_section += f"- **ISIN:** {pos.isin}\n"
    
    # Section 3: Conjoncture de marché
    market_section = "\n## CONJONCTURE DE MARCHÉ\n\n"
    
    if market_context:
        if "cms_10y" in market_context:
            cms = market_context["cms_10y"]
            market_section += f"**Taux CMS 10Y:** {cms['value']:.2f}% (au {cms['date']})\n"
        
        if "volatility" in market_context:
            market_section += f"**Volatilité estimée:** {market_context['volatility']:.2f}%\n"
    else:
        market_section += "Données de marché limitées disponibles.\n"
    
    # Section 4: Instructions pour l'IA
    instructions_section = """
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
- Respecter le profil de risque ({"modéré/performance" if profile.performance_priority else "conservateur"})
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
"""
    
    return context_section + portfolio_section + market_section + instructions_section


def get_market_context(data_dir: Path, valuation_date: Optional[date] = None) -> Dict[str, Any]:
    """
    Collecte le contexte de marché disponible
    
    Args:
        data_dir: Répertoire des données de marché
        valuation_date: Date de valorisation
        
    Returns:
        Dictionnaire avec les données de marché disponibles
    """
    if valuation_date is None:
        from datetime import date
        valuation_date = date.today()
    
    context = {}
    rates_provider = RatesProvider(data_dir)
    
    # Taux CMS 10Y si disponible
    cms_data = rates_provider.get_data("CMS_EUR_10Y", target_date=valuation_date)
    if cms_data:
        context["cms_10y"] = cms_data
    
    # TODO: Ajouter d'autres données de marché (volatilité, indices, etc.)
    
    return context

