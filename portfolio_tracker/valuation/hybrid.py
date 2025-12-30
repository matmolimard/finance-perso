"""
Hybrid Engine - Valorisation hybride (UC peu liquides)
"""
from datetime import date
from typing import Optional

from .base import BaseValuationEngine, ValuationResult
from .mark_to_market import MarkToMarketEngine
from ..core.asset import Asset
from ..core.position import Position


class HybridEngine(BaseValuationEngine):
    """
    Moteur de valorisation pour unités de compte peu liquides.
    
    Combine plusieurs approches :
    - Utilise mark-to-market si VL disponible
    - Utilise une valorisation estimative sinon
    - Gère explicitement les données manquantes
    """
    
    def valuate(
        self, 
        asset: Asset, 
        position: Position, 
        valuation_date: Optional[date] = None
    ) -> ValuationResult:
        """
        Valorise une UC illiquide avec une approche hybride.
        """
        val_date = self._get_valuation_date(valuation_date)
        
        # Essayer d'abord mark-to-market
        mtm_engine = MarkToMarketEngine(self.data_dir)
        nav_data = mtm_engine._load_nav(asset.asset_id, val_date)
        
        if nav_data:
            # VL disponible, utiliser mark-to-market
            result = mtm_engine.valuate(asset, position, valuation_date)
            result.metadata = result.metadata or {}
            result.metadata['valuation_method'] = 'mark_to_market'
            return result
        
        # Pas de VL, utiliser une valorisation estimative
        return self._estimative_valuation(asset, position, val_date)
    
    def _estimative_valuation(
        self, 
        asset: Asset, 
        position: Position, 
        valuation_date: date
    ) -> ValuationResult:
        """
        Valorisation estimative en l'absence de VL.
        
        Stratégies possibles :
        1. Utiliser la dernière VL connue (même ancienne)
        2. Utiliser le coût historique
        3. Marquer comme données manquantes
        """
        # Chercher la dernière VL connue, quelle que soit son ancienneté
        mtm_engine = MarkToMarketEngine(self.data_dir)
        
        # Essayer de charger n'importe quelle VL
        nav_file = self.market_data_dir / f"nav_{asset.asset_id}.yaml"
        if nav_file.exists():
            import yaml
            with open(nav_file, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
            
            if data and 'nav_history' in data and data['nav_history']:
                # Prendre la VL la plus récente disponible
                from datetime import datetime
                latest_nav = max(
                    data['nav_history'],
                    key=lambda x: datetime.fromisoformat(x['date'])
                )
                
                nav_value = latest_nav['value']
                nav_date = datetime.fromisoformat(latest_nav['date']).date()
                days_old = (valuation_date - nav_date).days
                
                # Calculer une valorisation indicative
                if position.investment.units_held:
                    estimated_value = position.investment.units_held * nav_value
                elif position.investment.invested_amount:
                    # Utiliser le coût historique
                    estimated_value = position.investment.invested_amount
                else:
                    estimated_value = None
                
                return ValuationResult(
                    position_id=position.position_id,
                    asset_id=asset.asset_id,
                    valuation_date=valuation_date,
                    current_value=estimated_value,
                    invested_amount=position.investment.invested_amount,
                    status="warning",
                    message=f"Valorisation estimative (VL de {days_old}j)",
                    metadata={
                        "valuation_method": "estimative",
                        "nav": nav_value,
                        "nav_date": nav_date.isoformat(),
                        "days_old": days_old
                    }
                )
        
        # Aucune VL disponible : utiliser le coût historique
        invested = position.investment.invested_amount
        
        return ValuationResult(
            position_id=position.position_id,
            asset_id=asset.asset_id,
            valuation_date=valuation_date,
            current_value=invested,
            invested_amount=invested,
            status="warning",
            message="Aucune VL disponible, valorisation au coût historique",
            metadata={
                "valuation_method": "historical_cost",
                "data_missing": True
            }
        )


