"""
Projection métier d'une position à partir du ledger de mouvements.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, Optional

from .movements import MovementKind, MovementNormalizer, NormalizedMovement


@dataclass(frozen=True)
class PositionProjection:
    valuation_date: date
    movements: list[NormalizedMovement] = field(default_factory=list)
    open_units: float = 0.0
    has_unit_movements: bool = False
    external_contributions_total: float = 0.0
    internal_capitalizations_total: float = 0.0
    withdrawals_total: float = 0.0
    fees_total: float = 0.0
    taxes_total: float = 0.0
    other_total: float = 0.0
    signed_cash_balance: float = 0.0
    buy_trade_notional_total: float = 0.0
    buy_trade_units_total: float = 0.0
    realized_exit_value: Optional[float] = None
    close_date: Optional[date] = None
    external_capital_remaining: float = 0.0

    @property
    def is_closed(self) -> bool:
        return self.close_date is not None or (self.has_unit_movements and abs(self.open_units) < 0.01 and self.realized_exit_value is not None)

    @property
    def xirr_cashflows(self) -> list[tuple[date, float]]:
        cashflows: list[tuple[date, float]] = []
        for mv in self.movements:
            if mv.movement_kind == MovementKind.EXTERNAL_CONTRIBUTION and mv.cash_amount > 0:
                cashflows.append((mv.effective_date, -mv.cash_amount))
            elif mv.movement_kind == MovementKind.WITHDRAWAL and mv.cash_amount < 0:
                cashflows.append((mv.effective_date, abs(mv.cash_amount)))
        return cashflows


class PositionProjectionService:
    """Construit une projection économique homogène à partir des mouvements normalisés."""

    def __init__(self, normalizer: Optional[MovementNormalizer] = None):
        self.normalizer = normalizer or MovementNormalizer()

    def project_from_lots(
        self,
        *,
        position_id: str,
        asset_id: str,
        lots: list,
        valuation_date: date,
    ) -> PositionProjection:
        movements = self.normalizer.normalize_lots(position_id=position_id, asset_id=asset_id, lots=lots)
        return self.project_from_movements(movements=movements, valuation_date=valuation_date)

    def project_from_movements(
        self,
        *,
        movements: Iterable[NormalizedMovement],
        valuation_date: date,
    ) -> PositionProjection:
        filtered = sorted(
            [mv for mv in movements if mv.effective_date <= valuation_date],
            key=lambda mv: (mv.effective_date, mv.movement_id),
        )

        open_units = 0.0
        has_unit_movements = False
        external_total = 0.0
        internal_total = 0.0
        withdrawals_total = 0.0
        fees_total = 0.0
        taxes_total = 0.0
        other_total = 0.0
        signed_cash_balance = 0.0
        buy_trade_notional_total = 0.0
        buy_trade_units_total = 0.0
        realized_exit_value = None
        close_date = None

        for mv in filtered:
            signed_cash_balance += mv.cash_amount

            if mv.units_delta is not None:
                has_unit_movements = True
                open_units += mv.units_delta

            if mv.raw_lot_type == "buy" and mv.cash_amount > 0:
                buy_trade_notional_total += mv.cash_amount
                if mv.units_delta is not None and mv.units_delta > 0:
                    buy_trade_units_total += mv.units_delta

            if mv.movement_kind == MovementKind.EXTERNAL_CONTRIBUTION:
                if mv.cash_amount > 0:
                    external_total += mv.cash_amount
            elif mv.movement_kind == MovementKind.INTERNAL_CAPITALIZATION:
                if mv.cash_amount > 0:
                    internal_total += mv.cash_amount
            elif mv.movement_kind == MovementKind.WITHDRAWAL:
                if mv.cash_amount < 0:
                    withdrawals_total += abs(mv.cash_amount)
                    realized_exit_value = (realized_exit_value or 0.0) + abs(mv.cash_amount)
                    close_date = mv.effective_date
            elif mv.movement_kind == MovementKind.FEE:
                if mv.cash_amount < 0:
                    fees_total += abs(mv.cash_amount)
            elif mv.movement_kind == MovementKind.TAX:
                if mv.cash_amount < 0:
                    taxes_total += abs(mv.cash_amount)
                    if mv.units_delta is not None and mv.units_delta < -10:
                        realized_exit_value = (realized_exit_value or 0.0) + abs(mv.cash_amount)
                        close_date = mv.effective_date
            else:
                other_total += mv.cash_amount

        # Detect position fully exited via internal transfer (external=False sell lots → OTHER category)
        if has_unit_movements and abs(open_units) < 0.01 and close_date is None and realized_exit_value is None:
            for mv in reversed(filtered):
                if mv.raw_lot_type in ("sell", "other") and mv.cash_amount < 0 and mv.external is False:
                    close_date = mv.effective_date
                    break

        external_capital_remaining = max(0.0, external_total - withdrawals_total - fees_total - taxes_total)

        return PositionProjection(
            valuation_date=valuation_date,
            movements=filtered,
            open_units=open_units,
            has_unit_movements=has_unit_movements,
            external_contributions_total=external_total,
            internal_capitalizations_total=internal_total,
            withdrawals_total=withdrawals_total,
            fees_total=fees_total,
            taxes_total=taxes_total,
            other_total=other_total,
            signed_cash_balance=signed_cash_balance,
            buy_trade_notional_total=buy_trade_notional_total,
            buy_trade_units_total=buy_trade_units_total,
            realized_exit_value=realized_exit_value,
            close_date=close_date,
            external_capital_remaining=external_capital_remaining,
        )
