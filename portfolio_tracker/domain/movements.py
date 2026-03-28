"""
Normalisation et classification métier des mouvements.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from hashlib import sha256
from typing import Any, Optional


class LotCategory(Enum):
    """Catégories métier de haut niveau pour les lots historiques."""

    EXTERNAL_DEPOSIT = "external_deposit"
    INTERNAL_CAPITALIZATION = "internal_capitalization"
    WITHDRAWAL = "withdrawal"
    FEE = "fee"
    TAX = "tax"
    OTHER = "other"


@dataclass(frozen=True)
class ClassifiedLot:
    """Résultat de la classification d'un lot historique."""

    category: LotCategory
    date: date
    amount: float
    raw_lot: dict

    def is_cash_inflow(self) -> bool:
        return self.category == LotCategory.EXTERNAL_DEPOSIT

    def is_cash_outflow(self) -> bool:
        return self.category in (LotCategory.WITHDRAWAL, LotCategory.FEE, LotCategory.TAX)

    def is_performance(self) -> bool:
        return self.category == LotCategory.INTERNAL_CAPITALIZATION

    def for_xirr(self) -> Optional[float]:
        if self.category == LotCategory.EXTERNAL_DEPOSIT:
            return -self.amount
        if self.category == LotCategory.WITHDRAWAL:
            return self.amount
        return None


class LotClassifier:
    """
    Classificateur historique des lots.

    Cette classe conserve la logique métier existante pour rester compatible
    avec les données déjà importées.
    """

    def __init__(self):
        self._external_deposits_seen = set()

    def classify_lot(self, lot: dict, position_id: str) -> Optional[ClassifiedLot]:
        if not isinstance(lot, dict):
            return None

        lot_type = str(lot.get("type", "buy")).lower()
        net_amount = lot.get("net_amount", 0.0)
        lot_date_raw = lot.get("date")

        if not lot_date_raw:
            return None
        try:
            if isinstance(lot_date_raw, str):
                lot_date = datetime.fromisoformat(lot_date_raw).date()
            else:
                lot_date = lot_date_raw
        except Exception:
            return None

        if lot_type == "buy" and net_amount > 0:
            is_external = self._is_external_deposit(lot, position_id)
            category = LotCategory.EXTERNAL_DEPOSIT if is_external else LotCategory.INTERNAL_CAPITALIZATION
            return ClassifiedLot(category, lot_date, abs(float(net_amount)), lot)

        if lot_type in ("sell", "other") and net_amount < 0:
            return ClassifiedLot(LotCategory.WITHDRAWAL, lot_date, abs(float(net_amount)), lot)

        if lot_type == "fee" and net_amount < 0:
            return ClassifiedLot(LotCategory.FEE, lot_date, abs(float(net_amount)), lot)

        if lot_type == "tax" and net_amount < 0:
            return ClassifiedLot(LotCategory.TAX, lot_date, abs(float(net_amount)), lot)

        return ClassifiedLot(LotCategory.OTHER, lot_date, abs(float(net_amount or 0.0)), lot)

    def _is_external_deposit(self, lot: dict, position_id: str) -> bool:
        external = lot.get("external")
        if external is not None:
            if external:
                self._external_deposits_seen.add(position_id)
            return bool(external)

        lot_date_raw = lot.get("date")
        if lot_date_raw and position_id in self._external_deposits_seen:
            try:
                if isinstance(lot_date_raw, str):
                    lot_date_obj = datetime.fromisoformat(lot_date_raw).date()
                else:
                    lot_date_obj = lot_date_raw
                if lot_date_obj.month == 12 and lot_date_obj.day == 31:
                    return False
            except Exception:
                pass

        self._external_deposits_seen.add(position_id)
        return True

    def classify_all_lots(self, lots: list, position_id: str) -> list[ClassifiedLot]:
        def get_lot_date(lot: Any) -> date:
            if not isinstance(lot, dict):
                return date.min
            lot_date_raw = lot.get("date")
            if not lot_date_raw:
                return date.min
            try:
                if isinstance(lot_date_raw, str):
                    return datetime.fromisoformat(lot_date_raw).date()
                return lot_date_raw
            except Exception:
                return date.min

        sorted_lots = sorted(lots, key=get_lot_date)
        classified = []
        for lot in sorted_lots:
            classified_lot = self.classify_lot(lot, position_id)
            if classified_lot:
                classified.append(classified_lot)
        classified.sort(key=lambda cl: cl.date)
        return classified


class MovementKind(str, Enum):
    """Typologie normalisée des mouvements."""

    EXTERNAL_CONTRIBUTION = "external_contribution"
    INTERNAL_CAPITALIZATION = "internal_capitalization"
    WITHDRAWAL = "withdrawal"
    FEE = "fee"
    TAX = "tax"
    OTHER = "other"


@dataclass(frozen=True)
class NormalizedMovement:
    """Mouvement métier normalisé, prêt à être persisté et projeté."""

    movement_id: str
    position_id: str
    asset_id: str
    effective_date: date
    raw_lot_type: str
    movement_kind: MovementKind
    cash_amount: float
    units_delta: Optional[float]
    unit_price: Optional[float]
    external: Optional[bool]
    raw_lot: dict


class MovementNormalizer:
    """Transforme les lots historiques en mouvements métier normalisés."""

    def __init__(self, classifier: Optional[LotClassifier] = None):
        self.classifier = classifier or LotClassifier()

    def normalize_lots(self, *, position_id: str, asset_id: str, lots: list) -> list[NormalizedMovement]:
        classified_lots = self.classifier.classify_all_lots(lots or [], position_id)
        normalized: list[NormalizedMovement] = []
        for idx, cl in enumerate(classified_lots):
            raw_lot = dict(cl.raw_lot)
            raw_lot_type = str(raw_lot.get("type") or "buy").lower()
            cash_amount = self._extract_signed_cash_amount(raw_lot)
            units_delta = self._extract_float(raw_lot.get("units"))
            unit_price = self._extract_float(raw_lot.get("nav"))
            external = raw_lot.get("external")

            movement_kind = self._movement_kind_for(cl.category)
            movement_id = self._movement_id(
                position_id=position_id,
                asset_id=asset_id,
                effective_date=cl.date,
                raw_lot_type=raw_lot_type,
                cash_amount=cash_amount,
                units_delta=units_delta,
                occurrence_index=idx,
            )

            normalized.append(
                NormalizedMovement(
                    movement_id=movement_id,
                    position_id=position_id,
                    asset_id=asset_id,
                    effective_date=cl.date,
                    raw_lot_type=raw_lot_type,
                    movement_kind=movement_kind,
                    cash_amount=cash_amount,
                    units_delta=units_delta,
                    unit_price=unit_price,
                    external=bool(external) if external is not None else None,
                    raw_lot=raw_lot,
                )
            )
        return normalized

    @staticmethod
    def _movement_kind_for(category: LotCategory) -> MovementKind:
        if category == LotCategory.EXTERNAL_DEPOSIT:
            return MovementKind.EXTERNAL_CONTRIBUTION
        if category == LotCategory.INTERNAL_CAPITALIZATION:
            return MovementKind.INTERNAL_CAPITALIZATION
        if category == LotCategory.WITHDRAWAL:
            return MovementKind.WITHDRAWAL
        if category == LotCategory.FEE:
            return MovementKind.FEE
        if category == LotCategory.TAX:
            return MovementKind.TAX
        return MovementKind.OTHER

    @staticmethod
    def _extract_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _extract_signed_cash_amount(cls, raw_lot: dict) -> float:
        net_amount = cls._extract_float(raw_lot.get("net_amount"))
        if net_amount is not None:
            return net_amount

        gross_amount = cls._extract_float(raw_lot.get("gross_amount"))
        fees_amount = cls._extract_float(raw_lot.get("fees_amount")) or 0.0
        if gross_amount is not None:
            return gross_amount - fees_amount

        nav = cls._extract_float(raw_lot.get("nav"))
        units = cls._extract_float(raw_lot.get("units"))
        if nav is not None and units is not None:
            return nav * units

        return 0.0

    @staticmethod
    def _movement_id(
        *,
        position_id: str,
        asset_id: str,
        effective_date: date,
        raw_lot_type: str,
        cash_amount: float,
        units_delta: Optional[float],
        occurrence_index: int,
    ) -> str:
        payload = (
            f"{position_id}|{asset_id}|{effective_date.isoformat()}|{raw_lot_type}|"
            f"{cash_amount:.8f}|{'' if units_delta is None else f'{units_delta:.8f}'}|{occurrence_index}"
        )
        return sha256(payload.encode("utf-8")).hexdigest()
