"""Modeles et chargement de donnees propres a la V2."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
import json
from pathlib import Path
from typing import Any

from .storage import connect, default_db_path


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
    """Vue portefeuille chargee depuis SQLite."""

    def __init__(self, data_dir: Path, *, db_path: Path | None = None, include_db_overlay: bool = True):
        self.data_dir = Path(data_dir)
        self.db_path = Path(db_path or default_db_path(self.data_dir))
        self.include_db_overlay = include_db_overlay
        self.assets: dict[str, Asset] = {}
        self.positions: dict[str, Position] = {}
        self._load_portfolio_from_db()
        if self.include_db_overlay:
            self._overlay_sqlite_lots()

    def _load_portfolio_from_db(self) -> bool:
        if not self.db_path.exists():
            return False
        try:
            with connect(self.db_path) as conn:
                required_tables = {"assets", "positions", "position_lots"}
                if not all(self._table_exists(conn, table_name) for table_name in required_tables):
                    return False

                asset_rows = conn.execute(
                    """
                    SELECT asset_id, asset_type, name, valuation_engine, isin, metadata_json
                    FROM assets
                    ORDER BY asset_id
                    """
                ).fetchall()
                position_rows = conn.execute(
                    """
                    SELECT position_id, asset_id, holder_type, wrapper_type, insurer, contract_name,
                           subscription_date, invested_amount, units_held, purchase_nav,
                           purchase_nav_currency, purchase_nav_source
                    FROM positions
                    ORDER BY position_id
                    """
                ).fetchall()
                if not asset_rows:
                    return False

                self.assets = {}
                for row in asset_rows:
                    asset = Asset(
                        asset_id=str(row["asset_id"]),
                        asset_type=AssetType(str(row["asset_type"])),
                        name=str(row["name"]),
                        valuation_engine=ValuationEngine(str(row["valuation_engine"])),
                        isin=str(row["isin"]) if row["isin"] is not None else None,
                        metadata=dict(json.loads(str(row["metadata_json"] or "{}")) or {}),
                    )
                    self.assets[asset.asset_id] = asset

                lots_by_position: dict[str, list[dict[str, Any]]] = {}
                lot_rows = conn.execute(
                    """
                    SELECT position_id, raw_lot_json
                    FROM position_lots
                    ORDER BY position_id, lot_index
                    """
                ).fetchall()
                for row in lot_rows:
                    normalized_lot = dict(json.loads(str(row["raw_lot_json"] or "{}")) or {})
                    if "date" in normalized_lot and normalized_lot["date"] is not None:
                        normalized_lot["date"] = _as_date(normalized_lot["date"], field_name="lot.date")
                    lots_by_position.setdefault(str(row["position_id"]), []).append(normalized_lot)

                self.positions = {}
                for row in position_rows:
                    position = Position(
                        position_id=str(row["position_id"]),
                        asset_id=str(row["asset_id"]),
                        holder_type=HolderType(str(row["holder_type"])),
                        wrapper=Wrapper(
                            wrapper_type=WrapperType(str(row["wrapper_type"])),
                            insurer=str(row["insurer"]),
                            contract_name=str(row["contract_name"]),
                        ),
                        investment=Investment(
                            subscription_date=_as_date(row["subscription_date"], field_name="subscription_date"),
                            invested_amount=float(row["invested_amount"]) if row["invested_amount"] is not None else None,
                            units_held=float(row["units_held"]) if row["units_held"] is not None else None,
                            purchase_nav=float(row["purchase_nav"]) if row["purchase_nav"] is not None else None,
                            purchase_nav_currency=str(row["purchase_nav_currency"] or "EUR"),
                            purchase_nav_source=row["purchase_nav_source"],
                            lots=lots_by_position.get(str(row["position_id"]), []),
                        ),
                    )
                    self.positions[position.position_id] = position
        except Exception:
            self.assets = {}
            self.positions = {}
            return False
        return True

    @staticmethod
    def _table_exists(conn, table_name: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
        return row is not None

    @staticmethod
    def _as_optional_float(value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _lot_matches(cls, existing_lot: dict[str, Any], candidate_lot: dict[str, Any]) -> bool:
        existing_type = str(existing_lot.get("type") or "").lower()
        candidate_type = str(candidate_lot.get("type") or "").lower()
        if existing_type != candidate_type:
            return False

        existing_date = existing_lot.get("date")
        if existing_date is not None and not isinstance(existing_date, date):
            existing_date = _as_date(existing_date, field_name="lot.date")
        candidate_date = candidate_lot.get("date")
        if candidate_date is not None and not isinstance(candidate_date, date):
            candidate_date = _as_date(candidate_date, field_name="lot.date")
        if existing_date != candidate_date:
            return False

        existing_amount = cls._as_optional_float(existing_lot.get("net_amount"))
        candidate_amount = cls._as_optional_float(candidate_lot.get("net_amount"))
        if existing_amount is None and candidate_amount is None:
            amount_matches = True
        elif existing_amount is None or candidate_amount is None:
            amount_matches = False
        else:
            amount_matches = abs(existing_amount - candidate_amount) <= 0.01
        if not amount_matches:
            return False

        existing_units = cls._as_optional_float(existing_lot.get("units"))
        candidate_units = cls._as_optional_float(candidate_lot.get("units"))
        if existing_units is None and candidate_units is None:
            return True
        if existing_units is None or candidate_units is None:
            return False
        return abs(existing_units - candidate_units) <= 0.0001

    def _append_overlay_lot(
        self,
        *,
        position_id: str,
        lot: dict[str, Any],
        touched_positions: set[str],
    ) -> None:
        position = self.positions.get(position_id)
        if position is None:
            return
        if any(self._lot_matches(existing_lot, lot) for existing_lot in position.investment.lots):
            return
        position.investment.lots.append(lot)
        touched_positions.add(position_id)

    def _overlay_sqlite_lots(self) -> None:
        if not self.db_path.exists():
            return

        touched_positions: set[str] = set()
        latest_snapshot_by_contract: dict[str, date] = {}
        try:
            with connect(self.db_path) as conn:
                if self._table_exists(conn, "annual_snapshots"):
                    latest_snapshot_by_contract = {
                        str(row["contract_name"]): _as_date(row["reference_date"], field_name="reference_date")
                        for row in conn.execute(
                            """
                            SELECT contract_name, MAX(reference_date) AS reference_date
                            FROM annual_snapshots
                            GROUP BY contract_name
                            """
                        ).fetchall()
                        if row["reference_date"] is not None
                    }
                if self._table_exists(conn, "manual_movements"):
                    rows = conn.execute(
                        """
                        SELECT position_id, effective_date, raw_lot_type, cash_amount, units_delta, unit_price,
                               external_flag, linked_document_id, reason, notes
                        FROM manual_movements
                        ORDER BY effective_date, manual_movement_id
                        """
                    ).fetchall()
                    for row in rows:
                        position_id = str(row["position_id"] or "").strip()
                        if not position_id:
                            continue
                        lot = {
                            "date": _as_date(row["effective_date"], field_name="lot.date"),
                            "type": str(row["raw_lot_type"]),
                            "units": self._as_optional_float(row["units_delta"]),
                            "nav": self._as_optional_float(row["unit_price"]),
                            "net_amount": float(row["cash_amount"] or 0.0),
                            "external": True if row["external_flag"] == 1 else False if row["external_flag"] == 0 else None,
                            "source": "manual_v2",
                            "model_anchor": True,
                            "linked_document_id": row["linked_document_id"],
                            "reason": row["reason"],
                            "notes": row["notes"],
                        }
                        self._append_overlay_lot(position_id=position_id, lot=lot, touched_positions=touched_positions)

                if self._table_exists(conn, "document_movements"):
                    rows = conn.execute(
                        """
                        SELECT position_id, effective_date, raw_lot_type, cash_amount, units_delta, unit_price,
                               external_flag, document_id, notes
                        FROM document_movements
                        ORDER BY effective_date, document_movement_id
                        """
                    ).fetchall()
                    for row in rows:
                        position_id = str(row["position_id"] or "").strip()
                        if not position_id:
                            continue
                        lot = {
                            "date": _as_date(row["effective_date"], field_name="lot.date"),
                            "type": str(row["raw_lot_type"]),
                            "units": self._as_optional_float(row["units_delta"]),
                            "nav": self._as_optional_float(row["unit_price"]),
                            "net_amount": float(row["cash_amount"] or 0.0),
                            "external": True if row["external_flag"] == 1 else False if row["external_flag"] == 0 else None,
                            "source": "document_pdf",
                            "model_anchor": True,
                            "document_id": row["document_id"],
                            "notes": row["notes"],
                        }
                        self._append_overlay_lot(position_id=position_id, lot=lot, touched_positions=touched_positions)
        except Exception:
            return

        for position_id in touched_positions:
            position = self.positions.get(position_id)
            if position is None:
                continue
            position.investment.lots.sort(key=lambda lot: str(lot.get("date") or ""))
            total_units = 0.0
            has_units = False
            for lot in position.investment.lots:
                units = self._as_optional_float(lot.get("units"))
                if units is None:
                    continue
                has_units = True
                total_units += units
            current_units = self._as_optional_float(position.investment.units_held)
            latest_snapshot_date = latest_snapshot_by_contract.get(str(position.wrapper.contract_name or ""))
            allow_overlay_units = (
                latest_snapshot_date is None
                or position.investment.subscription_date > latest_snapshot_date
            )
            if has_units and allow_overlay_units and (current_units is None or abs(current_units) < 0.000001):
                position.investment.units_held = round(total_units, 6)

    def get_asset(self, asset_id: str) -> Asset | None:
        return self.assets.get(asset_id)

    def get_position(self, position_id: str) -> Position | None:
        return self.positions.get(position_id)

    def list_all_assets(self) -> list[Asset]:
        return list(self.assets.values())

    def list_all_positions(self) -> list[Position]:
        return list(self.positions.values())
