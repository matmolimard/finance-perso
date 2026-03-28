"""Modeles et chargement de donnees propres a la V2."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any

import yaml


class AssetType(str, Enum):
    STRUCTURED_PRODUCT = "structured_product"
    FONDS_EURO = "fonds_euro"
    UC_FUND = "uc_fund"
    UC_ILLIQUID = "uc_illiquid"


class ValuationEngine(str, Enum):
    EVENT_BASED = "event_based"
    DECLARATIVE = "declarative"
    MARK_TO_MARKET = "mark_to_market"
    HYBRID = "hybrid"


class HolderType(str, Enum):
    INDIVIDUAL = "individual"
    COMPANY = "company"


class WrapperType(str, Enum):
    ASSURANCE_VIE = "assurance_vie"
    CONTRAT_CAPITALISATION = "contrat_de_capitalisation"


def _as_date(value: Any, *, field_name: str) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).date()
        except ValueError as exc:
            raise ValueError(f"Date invalide pour {field_name}: {value!r}") from exc
    raise ValueError(f"Date invalide pour {field_name}: {value!r}")


@dataclass
class Asset:
    asset_id: str
    asset_type: AssetType
    name: str
    valuation_engine: ValuationEngine
    isin: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Wrapper:
    wrapper_type: WrapperType
    insurer: str
    contract_name: str


@dataclass
class Investment:
    subscription_date: date
    invested_amount: float | None = None
    units_held: float | None = None
    purchase_nav: float | None = None
    purchase_nav_currency: str = "EUR"
    purchase_nav_source: str | None = None
    lots: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Position:
    position_id: str
    asset_id: str
    holder_type: HolderType
    wrapper: Wrapper
    investment: Investment


class PortfolioData:
    """Vue portefeuille chargee depuis les YAML V2, sans dependre du code legacy."""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.assets: dict[str, Asset] = {}
        self.positions: dict[str, Position] = {}
        self._load_assets()
        self._load_positions()

    def _load_assets(self) -> None:
        path = self.data_dir / "assets.yaml"
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for raw in payload.get("assets") or []:
            asset = Asset(
                asset_id=str(raw["asset_id"]),
                asset_type=AssetType(str(raw["type"])),
                name=str(raw["name"]),
                valuation_engine=ValuationEngine(str(raw["valuation_engine"])),
                isin=str(raw["isin"]) if raw.get("isin") is not None else None,
                metadata=dict(raw.get("metadata") or {}),
            )
            self.assets[asset.asset_id] = asset

    def _load_positions(self) -> None:
        path = self.data_dir / "positions.yaml"
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for raw in payload.get("positions") or []:
            wrapper_raw = raw["wrapper"]
            investment_raw = raw["investment"]
            lots: list[dict[str, Any]] = []
            for lot in investment_raw.get("lots") or []:
                normalized_lot = dict(lot)
                if "date" in normalized_lot and normalized_lot["date"] is not None:
                    normalized_lot["date"] = _as_date(normalized_lot["date"], field_name="lot.date")
                lots.append(normalized_lot)

            investment = Investment(
                subscription_date=_as_date(investment_raw["subscription_date"], field_name="subscription_date"),
                invested_amount=float(investment_raw["invested_amount"]) if investment_raw.get("invested_amount") is not None else None,
                units_held=float(investment_raw["units_held"]) if investment_raw.get("units_held") is not None else None,
                purchase_nav=float(investment_raw["purchase_nav"]) if investment_raw.get("purchase_nav") is not None else None,
                purchase_nav_currency=str(investment_raw.get("purchase_nav_currency") or "EUR"),
                purchase_nav_source=investment_raw.get("purchase_nav_source"),
                lots=lots,
            )
            position = Position(
                position_id=str(raw["position_id"]),
                asset_id=str(raw["asset_id"]),
                holder_type=HolderType(str(raw["holder_type"])),
                wrapper=Wrapper(
                    wrapper_type=WrapperType(str(wrapper_raw["type"])),
                    insurer=str(wrapper_raw["insurer"]),
                    contract_name=str(wrapper_raw["contract_name"]),
                ),
                investment=investment,
            )
            self.positions[position.position_id] = position

    def get_asset(self, asset_id: str) -> Asset | None:
        return self.assets.get(asset_id)

    def get_position(self, position_id: str) -> Position | None:
        return self.positions.get(position_id)

    def list_all_assets(self) -> list[Asset]:
        return list(self.assets.values())

    def list_all_positions(self) -> list[Position]:
        return list(self.positions.values())
