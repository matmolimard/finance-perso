"""
Recommendations - Format et affichage des recommandations
"""
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from enum import Enum
import json


class RecommendationAction(Enum):
    """Actions possibles sur une position"""
    REINFORCE = "reinforce"
    REDUCE = "reduce"
    MAINTAIN = "maintain"
    EXIT = "exit"


class RecommendationPriority(Enum):
    """Priorité d'une recommandation"""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class Recommendation:
    """Une recommandation individuelle"""
    position_id: str
    asset_name: str
    action: RecommendationAction
    reasoning: str
    priority: RecommendationPriority
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Recommendation':
        """Crée une Recommendation depuis un dictionnaire"""
        return cls(
            position_id=data['position_id'],
            asset_name=data['asset_name'],
            action=RecommendationAction(data['action']),
            reasoning=data['reasoning'],
            priority=RecommendationPriority(data.get('priority', 'medium')),
        )


@dataclass
class RecommendationSet:
    """Ensemble de recommandations pour un profil"""
    summary: str
    recommendations: List[Recommendation]
    market_concerns: List[str]
    opportunities: List[str]
    
    @classmethod
    def from_ai_response(cls, response: Dict[str, Any]) -> 'RecommendationSet':
        """
        Parse la réponse JSON de l'IA
        
        Args:
            response: Réponse JSON de l'IA
            
        Returns:
            RecommendationSet parsé
            
        Raises:
            ValueError: Si la structure est invalide
        """
        if not isinstance(response, dict):
            raise ValueError("Réponse doit être un dictionnaire")
        
        summary = response.get('summary', 'Aucun résumé fourni')
        
        # Parser les recommandations
        recommendations = []
        for rec_data in response.get('recommendations', []):
            try:
                recommendations.append(Recommendation.from_dict(rec_data))
            except (KeyError, ValueError) as e:
                # Logger l'erreur mais continuer
                import logging
                logging.warning(f"Recommandation invalide ignorée: {e}")
        
        market_concerns = response.get('market_concerns', [])
        if not isinstance(market_concerns, list):
            market_concerns = []
        
        opportunities = response.get('opportunities', [])
        if not isinstance(opportunities, list):
            opportunities = []
        
        return cls(
            summary=summary,
            recommendations=recommendations,
            market_concerns=market_concerns,
            opportunities=opportunities,
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """Convertit en dictionnaire"""
        return {
            'summary': self.summary,
            'recommendations': [
                {
                    'position_id': r.position_id,
                    'asset_name': r.asset_name,
                    'action': r.action.value,
                    'reasoning': r.reasoning,
                    'priority': r.priority.value,
                }
                for r in self.recommendations
            ],
            'market_concerns': self.market_concerns,
            'opportunities': self.opportunities,
        }
    
    def display(self, use_colors: bool = True) -> str:
        """
        Formate l'affichage pour le CLI
        
        Args:
            use_colors: Utiliser des couleurs ANSI (si supporté)
            
        Returns:
            Chaîne formatée
        """
        lines = []
        
        # Résumé
        lines.append("=" * 70)
        lines.append("RÉSUMÉ DE L'ANALYSE")
        lines.append("=" * 70)
        lines.append(self.summary)
        lines.append("")
        
        # Recommandations par priorité
        high_priority = [r for r in self.recommendations if r.priority == RecommendationPriority.HIGH]
        medium_priority = [r for r in self.recommendations if r.priority == RecommendationPriority.MEDIUM]
        low_priority = [r for r in self.recommendations if r.priority == RecommendationPriority.LOW]
        
        if high_priority:
            lines.append("=" * 70)
            lines.append("🔴 RECOMMANDATIONS PRIORITAIRES")
            lines.append("=" * 70)
            for rec in high_priority:
                lines.append(self._format_recommendation(rec, use_colors))
                lines.append("")
        
        if medium_priority:
            lines.append("=" * 70)
            lines.append("🟡 RECOMMANDATIONS MOYENNES")
            lines.append("=" * 70)
            for rec in medium_priority:
                lines.append(self._format_recommendation(rec, use_colors))
                lines.append("")
        
        if low_priority:
            lines.append("=" * 70)
            lines.append("🟢 RECOMMANDATIONS FAIBLES")
            lines.append("=" * 70)
            for rec in low_priority:
                lines.append(self._format_recommendation(rec, use_colors))
                lines.append("")
        
        # Préoccupations marché
        if self.market_concerns:
            lines.append("=" * 70)
            lines.append("⚠️  PRÉOCCUPATIONS MARCHÉ")
            lines.append("=" * 70)
            for concern in self.market_concerns:
                lines.append(f"  • {concern}")
            lines.append("")
        
        # Opportunités
        if self.opportunities:
            lines.append("=" * 70)
            lines.append("💡 OPPORTUNITÉS IDENTIFIÉES")
            lines.append("=" * 70)
            for opp in self.opportunities:
                lines.append(f"  • {opp}")
            lines.append("")
        
        # Disclaimer
        lines.append("=" * 70)
        lines.append("⚠️  DISCLAIMER")
        lines.append("=" * 70)
        lines.append("Ces recommandations sont fournies à titre informatif uniquement.")
        lines.append("Elles ne constituent pas un conseil en investissement personnalisé.")
        lines.append("Consultez un conseiller financier certifié avant toute décision.")
        lines.append("")
        
        return "\n".join(lines)
    
    def _format_recommendation(self, rec: Recommendation, use_colors: bool) -> str:
        """Formate une recommandation individuelle"""
        action_symbols = {
            RecommendationAction.REINFORCE: "📈",
            RecommendationAction.REDUCE: "📉",
            RecommendationAction.MAINTAIN: "➡️",
            RecommendationAction.EXIT: "🚪",
        }
        
        action_labels = {
            RecommendationAction.REINFORCE: "RENFORCER",
            RecommendationAction.REDUCE: "RÉDUIRE",
            RecommendationAction.MAINTAIN: "MAINTENIR",
            RecommendationAction.EXIT: "SORTIR",
        }
        
        symbol = action_symbols.get(rec.action, "•")
        label = action_labels.get(rec.action, rec.action.value.upper())
        
        lines = [
            f"{symbol} {rec.asset_name} ({rec.position_id})",
            f"   Action: {label}",
            f"   Raison: {rec.reasoning}",
        ]
        
        return "\n".join(lines)

