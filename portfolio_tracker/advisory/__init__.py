"""
Advisory Module - Système de conseil IA pour le portefeuille
"""
from .profiles import RiskProfile, load_profiles
from .analyzer import PortfolioAnalyzer
from .openrouter_client import OpenRouterClient
from .prompts import build_advisory_prompt, get_market_context
from .recommendations import Recommendation, RecommendationSet

__all__ = [
    'RiskProfile',
    'load_profiles',
    'PortfolioAnalyzer',
    'OpenRouterClient',
    'build_advisory_prompt',
    'get_market_context',
    'Recommendation',
    'RecommendationSet',
]

