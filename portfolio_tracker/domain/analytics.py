"""
Services d'analytics métier dérivés des mouvements normalisés.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Optional, Protocol

from .movements import LotCategory, LotClassifier
from .projection import PositionProjectionService


class InvestmentLike(Protocol):
    units_held: float | None
    lots: list[dict]
    invested_amount: float | None


class PositionLike(Protocol):
    position_id: str
    asset_id: str
    investment: InvestmentLike


class PositionAnalyticsService:
    """Centralise les calculs métier dérivés des mouvements/projections."""

    def __init__(
        self,
        *,
        projection_service: Optional[PositionProjectionService] = None,
        classifier: Optional[LotClassifier] = None,
    ):
        self.projection_service = projection_service or PositionProjectionService()
        self.classifier = classifier or LotClassifier()

    def project_lots(
        self,
        *,
        position_id: str,
        asset_id: str,
        lots: list[dict],
        valuation_date: date,
    ):
        return self.projection_service.project_from_lots(
            position_id=position_id,
            asset_id=asset_id,
            lots=lots,
            valuation_date=valuation_date,
        )

    def is_position_sold(self, position: PositionLike, *, valuation_date: Optional[date] = None) -> bool:
        units_held = position.investment.units_held
        if units_held is not None:
            try:
                return abs(float(units_held)) < 0.01
            except (TypeError, ValueError):
                pass

        projection = self.project_lots(
            position_id=position.position_id,
            asset_id=position.asset_id,
            lots=position.investment.lots or [],
            valuation_date=valuation_date or date.today(),
        )
        return projection.is_closed or (projection.has_unit_movements and abs(projection.open_units) < 0.01)

    def extract_sell_date(
        self,
        *,
        position_id: str,
        asset_id: str,
        lots: list[dict],
        valuation_date: Optional[date] = None,
    ) -> Optional[date]:
        projection = self.project_lots(
            position_id=position_id,
            asset_id=asset_id,
            lots=lots,
            valuation_date=valuation_date or date.today(),
        )
        return projection.close_date

    def extract_sell_value(
        self,
        *,
        position_id: str,
        asset_id: str,
        lots: list[dict],
        valuation_date: Optional[date] = None,
    ) -> Optional[float]:
        projection = self.project_lots(
            position_id=position_id,
            asset_id=asset_id,
            lots=lots,
            valuation_date=valuation_date or date.today(),
        )
        return projection.realized_exit_value

    def calculate_fees_total(
        self,
        *,
        position_id: str,
        asset_id: str,
        lots: list[dict],
        metadata: Optional[dict[str, Any]] = None,
        valuation_date: Optional[date] = None,
    ) -> float:
        projection = self.project_lots(
            position_id=position_id,
            asset_id=asset_id,
            lots=lots,
            valuation_date=valuation_date or date.today(),
        )
        fees_total = projection.fees_total

        if metadata:
            cashflow_adjustments = metadata.get("cashflow_adjustments")
            if cashflow_adjustments is not None:
                fees_total = abs(float(cashflow_adjustments))

        return fees_total

    def calculate_invested_amounts(
        self,
        *,
        position_id: str,
        asset_id: str,
        lots: list[dict],
        ref_date: Optional[date] = None,
        valuation_date: Optional[date] = None,
    ) -> dict[str, float]:
        result = {
            "invested_total": 0.0,
            "invested_external": 0.0,
            "invested_until_ref": 0.0,
            "invested_external_until_ref": 0.0,
        }
        if not lots:
            return result

        full_projection = self.project_lots(
            position_id=position_id,
            asset_id=asset_id,
            lots=lots,
            valuation_date=valuation_date or date.today(),
        )
        ref_projection = self.project_lots(
            position_id=position_id,
            asset_id=asset_id,
            lots=lots,
            valuation_date=ref_date or valuation_date or date.today(),
        )

        result["invested_total"] = full_projection.external_capital_remaining
        result["invested_external"] = full_projection.external_contributions_total
        result["invested_until_ref"] = ref_projection.external_capital_remaining
        result["invested_external_until_ref"] = ref_projection.external_contributions_total
        return result

    def build_cashflows_for_xirr(
        self,
        *,
        position_id: str,
        asset_id: str,
        lots: list[dict],
        value_at_end: float,
        end_date: date,
    ) -> list[tuple[date, float]]:
        projection = self.project_lots(
            position_id=position_id,
            asset_id=asset_id,
            lots=lots,
            valuation_date=end_date,
        )
        cashflows = list(projection.xirr_cashflows)
        if cashflows:
            cashflows.append((end_date, value_at_end))
        return cashflows

    def calculate_fonds_euro_invested_amount(
        self,
        *,
        position: PositionLike,
        lots: list[dict],
        valuation_date: Optional[date] = None,
    ) -> float:
        invested_amount = position.investment.invested_amount
        if lots:
            projection = self.project_lots(
                position_id=position.position_id,
                asset_id=position.asset_id,
                lots=lots,
                valuation_date=valuation_date or date.today(),
            )
            if projection.external_capital_remaining > 0:
                invested_amount = projection.external_capital_remaining
        return float(invested_amount) if invested_amount else 0.0

    def get_fonds_euro_reference_date(self, *, lots: list[dict], position_id: str, today: date) -> date:
        classified_lots = self.classifier.classify_all_lots(lots, position_id)
        benefit_years = {
            classified_lot.date.year
            for classified_lot in classified_lots
            if classified_lot.category == LotCategory.INTERNAL_CAPITALIZATION
        }

        if not benefit_years:
            ref_year = today.year - 2 if today.month <= 2 else today.year - 1
            return date(ref_year, 12, 31)

        last_benefit_year = max(benefit_years)
        if today.month <= 2:
            ref_year = last_benefit_year
        elif last_benefit_year >= today.year - 1:
            ref_year = today.year - 1
        else:
            ref_year = last_benefit_year
        return date(ref_year, 12, 31)

    def calculate_fonds_euro_performance_values(
        self,
        *,
        current_value: float,
        lots: list[dict],
        position_id: str,
        asset_id: str,
        ref_date_end: date,
        valuation_date: Optional[date] = None,
    ) -> tuple[Optional[float], Optional[float]]:
        invested_amounts = self.calculate_invested_amounts(
            position_id=position_id,
            asset_id=asset_id,
            lots=lots,
            ref_date=ref_date_end,
            valuation_date=valuation_date or date.today(),
        )
        invested_for_perf = invested_amounts["invested_external_until_ref"]

        value_for_perf = None
        if current_value is not None:
            value_for_perf = float(current_value)
            classified_lots = self.classifier.classify_all_lots(lots, position_id)
            ref_year = ref_date_end.year
            for cl in classified_lots:
                if cl.date.year <= ref_year:
                    continue
                if cl.category in {LotCategory.EXTERNAL_DEPOSIT, LotCategory.INTERNAL_CAPITALIZATION}:
                    value_for_perf -= cl.amount
                elif cl.is_cash_outflow():
                    value_for_perf += cl.amount

        return value_for_perf, invested_for_perf if invested_for_perf > 0 else None
