"""
Position - Détention réelle d'un actif
"""
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from datetime import date
from enum import Enum


class HolderType(Enum):
    """Type de détenteur"""
    INDIVIDUAL = "individual"
    COMPANY = "company"


class WrapperType(Enum):
    """Type d'enveloppe fiscale"""
    ASSURANCE_VIE = "assurance_vie"
    CONTRAT_CAPITALISATION = "contrat_de_capitalisation"


@dataclass
class Wrapper:
    """Enveloppe fiscale contenant l'actif"""
    wrapper_type: WrapperType
    insurer: str
    contract_name: str
    
    def __post_init__(self):
        if isinstance(self.wrapper_type, str):
            self.wrapper_type = WrapperType(self.wrapper_type)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Wrapper':
        return cls(
            wrapper_type=WrapperType(data['type']),
            insurer=data['insurer'],
            contract_name=data['contract_name']
        )
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'type': self.wrapper_type.value,
            'insurer': self.insurer,
            'contract_name': self.contract_name
        }


@dataclass
class Investment:
    """Détails de l'investissement"""
    subscription_date: date
    invested_amount: Optional[float] = None
    units_held: Optional[float] = None
    # VL / NAV d'achat (utile pour la performance UC, indépendante de l'historique de marché)
    purchase_nav: Optional[float] = None
    purchase_nav_currency: Optional[str] = "EUR"
    # "manual" (renseignée), "derived" (estimée), "nav_history" (déduite d'une VL historique)
    purchase_nav_source: Optional[str] = None
    # Achats multiples: lots d'achat (par position = par enveloppe)
    lots: List[Dict[str, Any]] = field(default_factory=list)
    
    def __post_init__(self):
        if isinstance(self.subscription_date, str):
            from datetime import datetime
            self.subscription_date = datetime.fromisoformat(self.subscription_date).date()
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Investment':
        return cls(
            subscription_date=data['subscription_date'],
            invested_amount=data.get('invested_amount'),
            units_held=data.get('units_held'),
            purchase_nav=data.get('purchase_nav'),
            purchase_nav_currency=data.get('purchase_nav_currency', 'EUR'),
            purchase_nav_source=data.get('purchase_nav_source'),
            lots=list(data.get('lots') or []),
        )
    
    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            'subscription_date': self.subscription_date.isoformat(),
            'invested_amount': self.invested_amount,
            'units_held': self.units_held,
        }
        if self.purchase_nav is not None:
            data["purchase_nav"] = self.purchase_nav
            if self.purchase_nav_currency and self.purchase_nav_currency != "EUR":
                data["purchase_nav_currency"] = self.purchase_nav_currency
            if self.purchase_nav_source:
                data["purchase_nav_source"] = self.purchase_nav_source
        if self.lots:
            data["lots"] = self.lots
        return data


@dataclass
class Position:
    """
    Représente la détention réelle d'un actif.
    
    Une position lie un actif à un contexte de détention :
    - détenteur (personne physique ou morale)
    - enveloppe (assurance vie, contrat de capitalisation)
    - montant investi ou nombre de parts
    """
    position_id: str
    asset_id: str
    holder_type: HolderType
    wrapper: Wrapper
    investment: Investment
    
    def __post_init__(self):
        if isinstance(self.holder_type, str):
            self.holder_type = HolderType(self.holder_type)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Position':
        """Crée une Position depuis un dictionnaire (YAML)"""
        return cls(
            position_id=data['position_id'],
            asset_id=data['asset_id'],
            holder_type=HolderType(data['holder_type']),
            wrapper=Wrapper.from_dict(data['wrapper']),
            investment=Investment.from_dict(data['investment'])
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """Convertit la Position en dictionnaire"""
        return {
            'position_id': self.position_id,
            'asset_id': self.asset_id,
            'holder_type': self.holder_type.value,
            'wrapper': self.wrapper.to_dict(),
            'investment': self.investment.to_dict()
        }
    
    def __repr__(self) -> str:
        return f"Position({self.position_id}, asset={self.asset_id}, {self.holder_type.value})"


