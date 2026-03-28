"""
Schémas de validation Pydantic pour les entités du portfolio.
"""
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator, model_validator

from .core.asset import AssetType, ValuationEngine


class AssetSchema(BaseModel):
    asset_id: str = Field(min_length=1, max_length=100)
    asset_type: AssetType
    name: str = Field(min_length=1, max_length=200)
    valuation_engine: ValuationEngine
    isin: Optional[str] = Field(None, pattern=r'^[A-Z]{2}[A-Z0-9]{10}$')
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    @field_validator('asset_id')
    @classmethod
    def validate_asset_id(cls, v: str) -> str:
        if not v or v.isspace():
            raise ValueError("asset_id ne peut être vide")
        if not all(c.isalnum() or c in '_-' for c in v):
            raise ValueError("asset_id doit contenir uniquement [a-zA-Z0-9_-]")
        return v.strip()
    
    @model_validator(mode='after')
    def validate_engine_compatibility(self):
        if self.asset_type == AssetType.STRUCTURED_PRODUCT and self.valuation_engine != ValuationEngine.EVENT_BASED:
            raise ValueError("STRUCTURED_PRODUCT requiert EVENT_BASED engine")
        if self.asset_type == AssetType.FONDS_EURO and self.valuation_engine != ValuationEngine.DECLARATIVE:
            raise ValueError("FONDS_EURO requiert DECLARATIVE engine")
        return self
    
    model_config = {"frozen": False, "str_strip_whitespace": True}


class HolderType(str, Enum):
    INDIVIDUAL = "individual"
    COMPANY = "company"


class WrapperType(str, Enum):
    ASSURANCE_VIE = "assurance_vie"
    CONTRAT_CAPITALISATION = "contrat_de_capitalisation"


class WrapperSchema(BaseModel):
    wrapper_type: WrapperType
    insurer: str = Field(min_length=1)
    contract_name: str = Field(min_length=1)
    
    model_config = {"frozen": False}


class LotSchema(BaseModel):
    date: date
    type: str = Field(pattern=r'^(buy|sell|fee|tax|income|other)$')
    gross_amount: Optional[Decimal] = None
    fees_amount: Optional[Decimal] = Field(None, ge=0)
    net_amount: Optional[Decimal] = None
    units: Optional[Decimal] = None
    external: Optional[bool] = None
    
    @model_validator(mode='after')
    def validate_amounts(self):
        if self.gross_amount is not None and self.net_amount is not None:
            if self.fees_amount is None:
                self.fees_amount = Decimal(0)
            expected_net = self.gross_amount - self.fees_amount
            if abs(self.net_amount - expected_net) > Decimal("0.01"):
                raise ValueError(
                    f"Incohérence lot {self.date}: net_amount ({self.net_amount}) "
                    f"!= gross_amount ({self.gross_amount}) - fees_amount ({self.fees_amount})"
                )
        
        if self.net_amount is None and self.gross_amount is None:
            raise ValueError(f"Lot {self.date}: net_amount ou gross_amount requis")
        
        return self
    
    model_config = {"frozen": False}


class InvestmentSchema(BaseModel):
    subscription_date: date
    invested_amount: Optional[Decimal] = Field(None, ge=0)
    units_held: Optional[Decimal] = Field(None, ge=0)
    purchase_nav: Optional[Decimal] = Field(None, gt=0)
    purchase_nav_currency: str = Field("EUR", pattern=r'^[A-Z]{3}$')
    purchase_nav_source: Optional[str] = Field(None, pattern=r'^(manual|derived|nav_history|lots|unknown)$')
    lots: List[LotSchema] = Field(default_factory=list)
    
    @model_validator(mode='after')
    def validate_investment_coherence(self):
        if self.lots:
            external_buy_total = Decimal(0)
            has_external_lots = False
            for lot in self.lots:
                if lot.type == "buy" and lot.external is True:
                    has_external_lots = True
                    amount = lot.net_amount if lot.net_amount is not None else lot.gross_amount
                    if amount is not None:
                        external_buy_total += amount
            
            # NOTE: La validation de cohérence entre invested_amount et lots est désactivée
            # car le calcul du capital investi est maintenant géré par la couche domaine
            # (classification + projection), pas directement par le schéma.
            pass
        
        if self.purchase_nav is not None and self.purchase_nav_source is None:
            self.purchase_nav_source = "manual"
        
        return self
    
    model_config = {"frozen": False}


class PositionSchema(BaseModel):
    position_id: str = Field(min_length=1, max_length=100)
    asset_id: str = Field(min_length=1)
    holder_type: HolderType
    wrapper: WrapperSchema
    investment: InvestmentSchema
    
    @field_validator('position_id')
    @classmethod
    def validate_position_id(cls, v: str) -> str:
        if not v or v.isspace():
            raise ValueError("position_id ne peut être vide")
        if not all(c.isalnum() or c in '_-' for c in v):
            raise ValueError("position_id doit contenir uniquement [a-zA-Z0-9_-]")
        return v.strip()
    
    model_config = {"frozen": False}


class NavPointSchema(BaseModel):
    point_date: date
    value: Decimal = Field(gt=0)
    currency: str = Field("EUR", pattern=r'^[A-Z]{3}$')
    source: Optional[str] = None
    
    model_config = {"frozen": True}


class ValuationEventSchema(BaseModel):
    event_type: str = Field(min_length=1)
    event_date: date
    amount: Optional[Decimal] = None
    description: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    model_config = {"frozen": False}
