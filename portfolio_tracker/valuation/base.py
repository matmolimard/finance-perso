"""
Base - Classes de base pour la valorisation
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Dict, Any, List
from datetime import date, datetime
from pathlib import Path

from ..core.asset import Asset
from ..core.position import Position


@dataclass
class ValuationEvent:
    """Événement de valorisation (coupon, autocall, échéance...)"""
    event_type: str  # "coupon", "autocall", "maturity", etc.
    event_date: date
    amount: Optional[float] = None
    description: str = ""
    metadata: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


@dataclass
class ValuationResult:
    """
    Résultat de la valorisation d'une position.
    
    Contient la valeur actuelle, les événements passés/futurs,
    et des métadonnées spécifiques au moteur de valorisation.
    """
    position_id: str
    asset_id: str
    valuation_date: date
    current_value: Optional[float] = None
    invested_amount: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    events: List[ValuationEvent] = None
    status: str = "ok"  # "ok", "warning", "error", "missing_data"
    message: str = ""
    metadata: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.events is None:
            self.events = []
        if self.metadata is None:
            self.metadata = {}
        
        # Calculer le P&L si possible
        if (self.current_value is not None and 
            self.invested_amount is not None and 
            self.unrealized_pnl is None):
            self.unrealized_pnl = self.current_value - self.invested_amount


class BaseValuationEngine(ABC):
    """
    Moteur de valorisation abstrait.
    
    Chaque type d'actif implémente sa propre logique de valorisation
    en héritant de cette classe.
    """
    
    def __init__(self, data_dir: Path):
        """
        Initialise le moteur avec le répertoire des données de marché.
        
        Args:
            data_dir: Chemin vers le dossier data/ contenant market_data/
        """
        self.data_dir = Path(data_dir)
        self.market_data_dir = self.data_dir / "market_data"
    
    @abstractmethod
    def valuate(
        self, 
        asset: Asset, 
        position: Position, 
        valuation_date: Optional[date] = None
    ) -> ValuationResult:
        """
        Valorise une position à une date donnée.
        
        Args:
            asset: L'actif à valoriser
            position: La position détenue
            valuation_date: Date de valorisation (défaut: aujourd'hui)
        
        Returns:
            ValuationResult contenant la valorisation
        """
        pass
    
    def _get_valuation_date(self, valuation_date: Optional[date] = None) -> date:
        """Retourne la date de valorisation (aujourd'hui par défaut)"""
        return valuation_date or datetime.now().date()


