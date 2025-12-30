"""
Asset - Définition financière abstraite d'un actif
"""
from dataclasses import dataclass
from typing import Optional, Dict, Any
from enum import Enum


class AssetType(Enum):
    """Types d'actifs supportés"""
    STRUCTURED_PRODUCT = "structured_product"
    FONDS_EURO = "fonds_euro"
    UC_FUND = "uc_fund"
    UC_ILLIQUID = "uc_illiquid"


class ValuationEngine(Enum):
    """Moteurs de valorisation disponibles"""
    EVENT_BASED = "event_based"
    DECLARATIVE = "declarative"
    MARK_TO_MARKET = "mark_to_market"
    HYBRID = "hybrid"


@dataclass
class Asset:
    """
    Représente un actif financier abstrait.
    
    Un actif est indépendant de sa détention (position).
    Un même actif peut être détenu dans plusieurs positions.
    """
    asset_id: str
    asset_type: AssetType
    name: str
    valuation_engine: ValuationEngine
    isin: Optional[str] = None
    metadata: Dict[str, Any] = None
    
    def __post_init__(self):
        """Conversion des strings en enums si nécessaire"""
        if isinstance(self.asset_type, str):
            self.asset_type = AssetType(self.asset_type)
        if isinstance(self.valuation_engine, str):
            self.valuation_engine = ValuationEngine(self.valuation_engine)
        if self.metadata is None:
            self.metadata = {}
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Asset':
        """Crée un Asset depuis un dictionnaire (YAML)"""
        return cls(
            asset_id=data['asset_id'],
            asset_type=AssetType(data['type']),
            name=data['name'],
            valuation_engine=ValuationEngine(data['valuation_engine']),
            isin=data.get('isin'),
            metadata=data.get('metadata', {})
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """Convertit l'Asset en dictionnaire"""
        return {
            'asset_id': self.asset_id,
            'type': self.asset_type.value,
            'name': self.name,
            'valuation_engine': self.valuation_engine.value,
            'isin': self.isin,
            'metadata': self.metadata
        }
    
    def __repr__(self) -> str:
        return f"Asset({self.asset_id}, {self.name}, {self.asset_type.value})"


