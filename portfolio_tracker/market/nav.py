"""
NAV Provider - Gestion des valeurs liquidatives (VL)
"""
from typing import Optional, Dict, Any, List
from datetime import date, datetime
from pathlib import Path
import yaml

from .providers import MarketDataProvider


class NAVProvider(MarketDataProvider):
    """
    Fournisseur de valeurs liquidatives (VL/NAV).
    
    Gère les VL des fonds, UC, et instruments cotés.
    """
    
    def get_data(
        self, 
        identifier: str, 
        data_type: str = "nav",
        target_date: Optional[date] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Récupère une VL à une date donnée.
        
        Args:
            identifier: ISIN ou identifiant du fonds
            data_type: Toujours "nav" pour ce provider
            target_date: Date cible
        
        Returns:
            {'value': float, 'date': date, 'currency': str} ou None
        """
        if target_date is None:
            target_date = datetime.now().date()
        
        nav_file = self.data_dir / f"nav_{identifier}.yaml"
        if not nav_file.exists():
            return None
        
        with open(nav_file, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
        
        if not data or 'nav_history' not in data:
            return None
        
        # Trouver la VL la plus proche de la date cible
        suitable_navs = []
        for nav_entry in data['nav_history']:
            nav_date = datetime.fromisoformat(nav_entry['date']).date()
            if nav_date <= target_date:
                suitable_navs.append({
                    'value': nav_entry['value'],
                    'date': nav_date,
                    'currency': nav_entry.get('currency', 'EUR')
                })
        
        if not suitable_navs:
            return None
        
        return max(suitable_navs, key=lambda x: x['date'])
    
    def is_data_available(self, identifier: str, data_type: str = "nav") -> bool:
        """Vérifie si des VL sont disponibles"""
        nav_file = self.data_dir / f"nav_{identifier}.yaml"
        return nav_file.exists()
    
    def get_latest_date(
        self, 
        identifier: str, 
        data_type: str = "nav"
    ) -> Optional[date]:
        """Retourne la date de la dernière VL disponible"""
        data = self.get_data(identifier, data_type, date.max)
        return data['date'] if data else None
    
    def get_nav_history(
        self, 
        identifier: str,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None
    ) -> List[Dict[str, Any]]:
        """
        Récupère l'historique des VL sur une période.
        
        Args:
            identifier: ISIN ou identifiant du fonds
            start_date: Date de début (None = tout l'historique)
            end_date: Date de fin (None = aujourd'hui)
        
        Returns:
            Liste de {'value': float, 'date': date, 'currency': str}
        """
        nav_file = self.data_dir / f"nav_{identifier}.yaml"
        if not nav_file.exists():
            return []
        
        with open(nav_file, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
        
        if not data or 'nav_history' not in data:
            return []
        
        result = []
        for nav_entry in data['nav_history']:
            nav_date = datetime.fromisoformat(nav_entry['date']).date()
            
            if start_date and nav_date < start_date:
                continue
            if end_date and nav_date > end_date:
                continue
            
            result.append({
                'value': nav_entry['value'],
                'date': nav_date,
                'currency': nav_entry.get('currency', 'EUR')
            })
        
        return sorted(result, key=lambda x: x['date'])
    
    def calculate_performance(
        self,
        identifier: str,
        start_date: date,
        end_date: Optional[date] = None
    ) -> Optional[float]:
        """
        Calcule la performance entre deux dates.
        
        Args:
            identifier: ISIN ou identifiant du fonds
            start_date: Date de début
            end_date: Date de fin (None = aujourd'hui)
        
        Returns:
            Performance en % ou None si données manquantes
        """
        start_nav = self.get_data(identifier, "nav", start_date)
        end_nav = self.get_data(identifier, "nav", end_date)
        
        if not start_nav or not end_nav:
            return None
        
        return ((end_nav['value'] / start_nav['value']) - 1) * 100


