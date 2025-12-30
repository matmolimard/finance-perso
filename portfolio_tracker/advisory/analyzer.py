"""
Portfolio Analyzer - Analyse du portefeuille et collecte de métriques
"""
from typing import List, Dict, Any, Optional
from pathlib import Path
from datetime import date, datetime
from dataclasses import dataclass

from ..core import Portfolio
from ..core.position import Position
from ..core.asset import Asset, ValuationEngine
from ..valuation import (
    EventBasedEngine,
    DeclarativeEngine,
    MarkToMarketEngine,
    HybridEngine,
)
from ..market.quantalys import QuantalysProvider
from .profiles import RiskProfile, get_profile_for_position


@dataclass
class PositionMetrics:
    """Métriques d'une position"""
    position_id: str
    asset_id: str
    asset_name: str
    asset_type: str
    current_value: float
    invested_amount: float
    pnl: float
    pnl_percent: float
    holding_period_months: int
    quantalys_rating: Optional[str] = None
    isin: Optional[str] = None
    metadata: Dict[str, Any] = None


@dataclass
class PortfolioSummary:
    """Résumé du portefeuille pour un profil"""
    profile_name: str
    total_value: float
    total_invested: float
    total_pnl: float
    total_pnl_percent: float
    positions: List[PositionMetrics]
    asset_allocation: Dict[str, float]  # Par type d'actif
    risk_profile: RiskProfile


class PortfolioAnalyzer:
    """Analyseur de portefeuille pour générer des métriques"""
    
    def __init__(self, portfolio: Portfolio, data_dir: Path):
        """
        Initialise l'analyseur
        
        Args:
            portfolio: Instance du portefeuille
            data_dir: Répertoire des données de marché
        """
        self.portfolio = portfolio
        self.data_dir = Path(data_dir)
        self.quantalys_provider = QuantalysProvider(self.data_dir)
        
        # Engines de valorisation
        self.engines = {
            ValuationEngine.EVENT_BASED: EventBasedEngine(self.data_dir),
            ValuationEngine.DECLARATIVE: DeclarativeEngine(self.data_dir),
            ValuationEngine.MARK_TO_MARKET: MarkToMarketEngine(self.data_dir),
            ValuationEngine.HYBRID: HybridEngine(self.data_dir),
        }
    
    def analyze_profile(
        self,
        profile: RiskProfile,
        valuation_date: Optional[date] = None
    ) -> PortfolioSummary:
        """
        Analyse toutes les positions d'un profil donné
        
        Args:
            profile: Profil de risque à analyser
            valuation_date: Date de valorisation (défaut: aujourd'hui)
            
        Returns:
            Résumé du portefeuille pour ce profil
        """
        if valuation_date is None:
            valuation_date = date.today()
        
        # Trouver toutes les positions correspondant au profil
        matching_positions = []
        for position in self.portfolio.list_all_positions():
            if profile.matches_position(
                position.wrapper.contract_name,
                position.wrapper.insurer
            ):
                matching_positions.append(position)
        
        # Analyser chaque position
        position_metrics = []
        total_value = 0.0
        total_invested_external = 0.0  # Capital externe (pour le P&L total, comme dans le CLI)
        asset_allocation = {}
        
        for position in matching_positions:
            metrics = self._analyze_position(position, valuation_date)
            if metrics and metrics.current_value > 0.01:  # Filtrer positions vendues
                position_metrics.append(metrics)
                total_value += metrics.current_value
                
                # Calculer le capital externe pour cette position (comme dans le CLI)
                invested_external = 0.0
                lots = position.investment.lots or []
                for lot in lots:
                    if not isinstance(lot, dict):
                        continue
                    if not lot.get('external', False):
                        continue
                    lot_type = str(lot.get('type', 'buy')).lower()
                    if lot_type != 'buy':
                        continue
                    net_amt = lot.get('net_amount')
                    if net_amt is None:
                        gross = lot.get('gross_amount')
                        fees = lot.get('fees_amount', 0.0)
                        if gross is not None:
                            try:
                                net_amt = float(gross) - float(fees or 0.0)
                            except Exception:
                                net_amt = None
                    if net_amt is not None and net_amt > 0:
                        invested_external += float(net_amt)
                
                # Fallback sur invested_amount du YAML
                if invested_external == 0.0:
                    asset = self.portfolio.get_asset(position.asset_id)
                    if asset:
                        engine = self.engines.get(asset.valuation_engine)
                        if engine:
                            result = engine.valuate(asset, position, valuation_date)
                            invested_external = result.invested_amount or 0.0
                
                # Ajouter au total externe (comme dans le CLI ligne 186)
                total_invested_external += invested_external
                
                # Allocation par type d'actif
                asset_type = metrics.asset_type
                asset_allocation[asset_type] = asset_allocation.get(asset_type, 0.0) + metrics.current_value
        
        # Calculer P&L total (comme dans le CLI ligne 278)
        # Le P&L total est calculé par rapport au capital externe total
        total_pnl = total_value - total_invested_external
        total_pnl_percent = (total_pnl / total_invested_external * 100) if total_invested_external > 0 else 0.0
        
        # Normaliser l'allocation en pourcentages
        if total_value > 0:
            asset_allocation = {
                k: (v / total_value * 100) 
                for k, v in asset_allocation.items()
            }
        
        return PortfolioSummary(
            profile_name=profile.name,
            total_value=total_value,
            total_invested=total_invested_external,  # Capital externe (comme dans le CLI)
            total_pnl=total_pnl,
            total_pnl_percent=total_pnl_percent,
            positions=position_metrics,
            asset_allocation=asset_allocation,
            risk_profile=profile,
        )
    
    def _analyze_position(
        self,
        position: Position,
        valuation_date: date
    ) -> Optional[PositionMetrics]:
        """
        Analyse une position individuelle
        
        Args:
            position: Position à analyser
            valuation_date: Date de valorisation
            
        Returns:
            Métriques de la position ou None si erreur
        """
        asset = self.portfolio.get_asset(position.asset_id)
        if not asset:
            return None
        
        # Valoriser la position
        engine = self.engines.get(asset.valuation_engine)
        if not engine:
            return None
        
        result = engine.valuate(asset, position, valuation_date)
        
        if result.current_value is None:
            return None
        
        # Calculer la durée de détention
        holding_period_months = self._calculate_holding_period(
            position.investment.subscription_date,
            valuation_date
        )
        
        # Récupérer le rating Quantalys si disponible
        quantalys_rating = None
        if asset.isin:
            rating_info = self.quantalys_provider.get_rating(asset.isin)
            if rating_info:
                rating = rating_info.get('rating')
                if rating:
                    quantalys_rating = f"{rating}/5"
        
        # Utiliser la MÊME logique que le CLI (status_by_wrapper)
        # 1. Calculer le capital investi externe (lots external=true)
        invested_external = 0.0
        lots = position.investment.lots or []
        for lot in lots:
            if not isinstance(lot, dict):
                continue
            # Seuls les lots marqués external=true comptent pour le capital externe
            if not lot.get('external', False):
                continue
            lot_type = str(lot.get('type', 'buy')).lower()
            if lot_type != 'buy':
                continue
            net_amt = lot.get('net_amount')
            if net_amt is None:
                gross = lot.get('gross_amount')
                fees = lot.get('fees_amount', 0.0)
                if gross is not None:
                    try:
                        net_amt = float(gross) - float(fees or 0.0)
                    except Exception:
                        net_amt = None
            if net_amt is not None and net_amt > 0:
                invested_external += float(net_amt)
        
        # Fallback sur result.invested_amount si aucun lot externe trouvé
        if invested_external == 0.0:
            invested_external = result.invested_amount or 0.0
        
        # 2. Calculer le capital investi réel (achats - rachats - frais) pour le P&L individuel
        # Vérifier si la position est vendue
        units_held = position.investment.units_held
        is_sold = False
        if units_held is not None:
            try:
                if abs(float(units_held)) < 0.01:
                    is_sold = True
            except:
                pass
        
        invested_real = invested_external
        if not is_sold and lots:
            buy_total = 0.0
            sell_other_total = 0.0
            fees_total = 0.0
            for lot in lots:
                if not isinstance(lot, dict):
                    continue
                lot_type = str(lot.get('type', 'buy')).lower()
                net_amt = lot.get('net_amount', 0.0)
                
                if lot_type == 'buy' and net_amt > 0:
                    buy_total += net_amt
                elif lot_type in ('sell', 'other') and net_amt < 0:
                    sell_other_total += abs(net_amt)
                elif lot_type == 'fee' and net_amt < 0:
                    fees_total += abs(net_amt)
            
            # Capital investi réel = achats - rachats - frais (minimum 0)
            invested_real = max(0.0, buy_total - sell_other_total - fees_total)
        else:
            # Position vendue : capital investi réel = 0 pour le P&L
            invested_real = 0.0
        
        # P&L basé sur le capital investi réel (comme dans le CLI)
        pnl = result.current_value - invested_real if invested_real > 0 else 0
        pnl_percent = (pnl / invested_real * 100) if invested_real > 0 else 0.0
        
        # Pour l'analyzer, on stocke invested_real (utilisé pour le P&L)
        # mais on garde aussi invested_external pour référence
        invested = invested_real
        
        return PositionMetrics(
            position_id=position.position_id,
            asset_id=asset.asset_id,
            asset_name=asset.name,
            asset_type=asset.asset_type.value,
            current_value=result.current_value,
            invested_amount=invested,
            pnl=pnl,
            pnl_percent=pnl_percent,
            holding_period_months=holding_period_months,
            quantalys_rating=quantalys_rating,
            isin=asset.isin,
            metadata=asset.metadata,
        )
    
    def _calculate_holding_period(
        self,
        subscription_date: date,
        valuation_date: date
    ) -> int:
        """
        Calcule la durée de détention en mois
        
        Args:
            subscription_date: Date de souscription
            valuation_date: Date de valorisation
            
        Returns:
            Nombre de mois (arrondi)
        """
        delta = valuation_date - subscription_date
        return int(delta.days / 30.44)  # Moyenne de jours par mois

