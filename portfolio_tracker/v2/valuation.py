"""Moteurs de valorisation V2, sans dependance aux modules legacy core/valuation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import yaml

from ..domain import PositionProjectionService
from ..domain.movements import MovementKind
from .models import Asset, Position


@dataclass
class ValuationEvent:
    event_type: str
    event_date: date
    amount: Optional[float] = None
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ValuationResult:
    position_id: str
    asset_id: str
    valuation_date: date
    current_value: Optional[float] = None
    invested_amount: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    events: list[ValuationEvent] = field(default_factory=list)
    status: str = "ok"
    message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.current_value is not None and self.invested_amount is not None and self.unrealized_pnl is None:
            self.unrealized_pnl = self.current_value - self.invested_amount


class BaseValuationEngine(ABC):
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.market_data_dir = self.data_dir / "market_data"
        self.projection_service = PositionProjectionService()

    def _get_valuation_date(self, valuation_date: Optional[date] = None) -> date:
        return valuation_date or datetime.now().date()

    def _project_position(self, asset: Asset, position: Position, valuation_date: date):
        return self.projection_service.project_from_lots(
            position_id=position.position_id,
            asset_id=asset.asset_id,
            lots=position.investment.lots or [],
            valuation_date=valuation_date,
        )

    @abstractmethod
    def valuate(self, asset: Asset, position: Position, valuation_date: Optional[date] = None) -> ValuationResult:
        raise NotImplementedError


class MarkToMarketEngine(BaseValuationEngine):
    def valuate(self, asset: Asset, position: Position, valuation_date: Optional[date] = None) -> ValuationResult:
        val_date = self._get_valuation_date(valuation_date)
        projection = self._project_position(asset, position, val_date)
        has_projected_movements = bool(projection.movements)
        lots_units_total = projection.open_units if projection.has_unit_movements else None
        buy_units_total = projection.buy_trade_units_total
        buy_amount_total = projection.buy_trade_notional_total

        units_held = position.investment.units_held
        if projection.has_unit_movements:
            units_held = projection.open_units
        elif units_held is None:
            units_held = 0.0

        if units_held is not None and abs(float(units_held)) < 0.01:
            invested_amount = position.investment.invested_amount
            if invested_amount is None and buy_amount_total:
                invested_amount = float(buy_amount_total)
            if invested_amount is None:
                invested_amount = 0.0
            return ValuationResult(
                position_id=position.position_id,
                asset_id=asset.asset_id,
                valuation_date=val_date,
                current_value=0.0,
                invested_amount=invested_amount,
                status="ok",
                message="Position historique (vendue, units_held=0)",
            )

        nav_data = self._load_nav(asset.asset_id, val_date)
        if not nav_data:
            return ValuationResult(
                position_id=position.position_id,
                asset_id=asset.asset_id,
                valuation_date=val_date,
                status="error",
                message="VL non disponible",
            )

        nav_value = nav_data["value"]
        nav_date = nav_data["date"]
        purchase_nav = position.investment.purchase_nav
        purchase_nav_source = position.investment.purchase_nav_source

        if purchase_nav is not None and purchase_nav_source == "manual":
            pass
        elif buy_units_total != 0:
            purchase_nav = buy_amount_total / buy_units_total
            purchase_nav_source = "lots"
        elif purchase_nav is not None and purchase_nav_source is None:
            purchase_nav_source = "unknown"

        if purchase_nav is None and position.investment.invested_amount and position.investment.units_held:
            try:
                if float(position.investment.units_held) != 0:
                    purchase_nav = float(position.investment.invested_amount) / float(position.investment.units_held)
                    purchase_nav_source = "derived"
            except Exception:
                purchase_nav = None

        units_for_valuation = None
        if projection.has_unit_movements:
            units_for_valuation = float(projection.open_units)
        elif position.investment.units_held is not None:
            units_for_valuation = float(position.investment.units_held)

        if units_for_valuation is not None:
            current_value = units_for_valuation * nav_value
        elif position.investment.invested_amount:
            initial_nav_value = purchase_nav
            if initial_nav_value is None:
                initial_nav = self._load_nav(asset.asset_id, position.investment.subscription_date)
                if initial_nav:
                    initial_nav_value = initial_nav["value"]
            if initial_nav_value is None:
                return ValuationResult(
                    position_id=position.position_id,
                    asset_id=asset.asset_id,
                    valuation_date=val_date,
                    status="error",
                    message="VL initiale non disponible pour calculer les parts",
                )
            units = position.investment.invested_amount / initial_nav_value
            current_value = units * nav_value
        else:
            return ValuationResult(
                position_id=position.position_id,
                asset_id=asset.asset_id,
                valuation_date=val_date,
                status="error",
                message="Ni le nombre de parts ni le montant investi n'est specifie",
            )

        days_old = (val_date - nav_date).days
        status = "warning" if days_old > 7 else "ok"
        message = f"VL datee de {days_old} jours ({nav_date})" if days_old > 7 else f"VL : {nav_value} au {nav_date}"

        perf_pct = None
        if purchase_nav is not None:
            try:
                if float(purchase_nav) != 0:
                    perf_pct = ((float(nav_value) / float(purchase_nav)) - 1.0) * 100.0
            except Exception:
                perf_pct = None

        invested_amount = position.investment.invested_amount
        if invested_amount is None and buy_amount_total:
            invested_amount = float(buy_amount_total)
        if invested_amount is None:
            invested_amount = 0.0

        return ValuationResult(
            position_id=position.position_id,
            asset_id=asset.asset_id,
            valuation_date=val_date,
            current_value=current_value,
            invested_amount=invested_amount,
            status=status,
            message=message,
            metadata={
                "nav": nav_value,
                "nav_date": nav_date.isoformat(),
                "days_old": days_old,
                "purchase_nav": purchase_nav,
                "purchase_nav_source": purchase_nav_source,
                "perf_pct": perf_pct,
                "lots_units_total": lots_units_total if has_projected_movements else None,
                "buy_units_total": buy_units_total if has_projected_movements else None,
                "buy_amount_total": buy_amount_total if has_projected_movements else None,
            },
        )

    def _load_nav(self, asset_id: str, target_date: date) -> Optional[dict[str, Any]]:
        nav_file = self.market_data_dir / f"nav_{asset_id}.yaml"
        if not nav_file.exists():
            return None
        data = yaml.safe_load(nav_file.read_text(encoding="utf-8"))
        if not data or "nav_history" not in data:
            return None
        suitable_navs = []
        for nav_entry in data["nav_history"]:
            nav_date = datetime.fromisoformat(nav_entry["date"]).date()
            if nav_date <= target_date:
                suitable_navs.append({"value": nav_entry["value"], "date": nav_date})
        if not suitable_navs:
            return None
        return max(suitable_navs, key=lambda item: item["date"])


class HybridEngine(BaseValuationEngine):
    def valuate(self, asset: Asset, position: Position, valuation_date: Optional[date] = None) -> ValuationResult:
        val_date = self._get_valuation_date(valuation_date)
        mtm_engine = MarkToMarketEngine(self.data_dir)
        nav_data = mtm_engine._load_nav(asset.asset_id, val_date)
        if nav_data:
            result = mtm_engine.valuate(asset, position, valuation_date)
            result.metadata = result.metadata or {}
            result.metadata["valuation_method"] = "mark_to_market"
            return result
        return self._estimative_valuation(asset, position, val_date)

    def _estimative_valuation(self, asset: Asset, position: Position, valuation_date: date) -> ValuationResult:
        nav_file = self.market_data_dir / f"nav_{asset.asset_id}.yaml"
        if nav_file.exists():
            data = yaml.safe_load(nav_file.read_text(encoding="utf-8"))
            if data and "nav_history" in data and data["nav_history"]:
                latest_nav = max(data["nav_history"], key=lambda row: datetime.fromisoformat(row["date"]))
                nav_value = latest_nav["value"]
                nav_date = datetime.fromisoformat(latest_nav["date"]).date()
                days_old = (valuation_date - nav_date).days
                estimated_value = None
                if position.investment.units_held:
                    estimated_value = position.investment.units_held * nav_value
                elif position.investment.invested_amount:
                    estimated_value = position.investment.invested_amount
                return ValuationResult(
                    position_id=position.position_id,
                    asset_id=asset.asset_id,
                    valuation_date=valuation_date,
                    current_value=estimated_value,
                    invested_amount=position.investment.invested_amount,
                    status="warning",
                    message=f"Valorisation estimative (VL de {days_old}j)",
                    metadata={
                        "valuation_method": "estimative",
                        "nav": nav_value,
                        "nav_date": nav_date.isoformat(),
                        "days_old": days_old,
                    },
                )
        invested = position.investment.invested_amount
        return ValuationResult(
            position_id=position.position_id,
            asset_id=asset.asset_id,
            valuation_date=valuation_date,
            current_value=invested,
            invested_amount=invested,
            status="warning",
            message="Aucune VL disponible, valorisation au cout historique",
            metadata={"valuation_method": "historical_cost", "data_missing": True},
        )


class DeclarativeEngine(BaseValuationEngine):
    def valuate(self, asset: Asset, position: Position, valuation_date: Optional[date] = None) -> ValuationResult:
        val_date = self._get_valuation_date(valuation_date)
        rates = self._load_declared_rates(asset.asset_id)
        projection = self._project_position(asset, position, val_date)
        invested = position.investment.invested_amount
        cashflows = self._extract_cashflows(projection=projection)
        use_cashflows = bool(cashflows)
        if invested is None and use_cashflows:
            invested = projection.external_contributions_total
        if invested is None:
            invested = 0.0
        raw_value = projection.signed_cash_balance if use_cashflows else None

        if not rates:
            units_held = position.investment.units_held
            if units_held is not None:
                current_value = float(units_held)
                status = "warning"
                message = f"Valeur declaree (units_held): {current_value:,.2f} EUR - Aucun taux declare disponible"
            else:
                current_value = raw_value if raw_value is not None else invested
                status = "warning"
                message = "Aucun taux declare disponible (valeur = cashflows / capital investi)"
            return ValuationResult(
                position_id=position.position_id,
                asset_id=asset.asset_id,
                valuation_date=val_date,
                current_value=current_value,
                invested_amount=invested,
                status=status,
                message=message,
                metadata={"opacity_acknowledged": True, "raw_value": raw_value, "units_held_value": units_held},
            )

        subscription_date = position.investment.subscription_date
        last_available_year = max(rates.keys()) if rates else None
        if last_available_year and val_date.year > last_available_year:
            units_held = position.investment.units_held
            if units_held is not None:
                base_value = float(units_held)
                theoretical_value_end_year = None
                if use_cashflows:
                    theoretical_value_end_year = self._compute_value_from_cashflows(
                        cashflows=cashflows, valuation_date=date(last_available_year, 12, 31), rates=rates
                    )
                else:
                    theoretical_value_end_year = self._compute_value_from_rates(
                        invested, subscription_date, date(last_available_year, 12, 31), rates
                    )
                if theoretical_value_end_year and abs(base_value - theoretical_value_end_year) < abs(base_value) * 0.1:
                    additional_value = sum(amt for cf_date, _, amt in cashflows if cf_date.year > last_available_year)
                    current_value = base_value + additional_value
                else:
                    current_value = base_value
            else:
                current_value = None
        else:
            if use_cashflows:
                current_value = self._compute_value_from_cashflows(cashflows=cashflows, valuation_date=val_date, rates=rates)
            else:
                current_value = self._compute_value_from_rates(invested, subscription_date, val_date, rates)

        if current_value is None:
            current_value = float(position.investment.units_held) if position.investment.units_held is not None else (raw_value if raw_value is not None else invested)
        latest_year = max(rates.keys()) if rates else None
        latest_data = rates.get(latest_year, {}) if latest_year is not None else {}
        if latest_year is None:
            status = "warning"
            message = "Aucun taux declare disponible"
        elif latest_year < val_date.year - 1:
            status = "warning"
            message = f"Derniere mise a jour : {latest_year}"
        else:
            status = "ok"
            message = f"Taux {latest_year} : {latest_data.get('rate', 'N/A')}%"
        return ValuationResult(
            position_id=position.position_id,
            asset_id=asset.asset_id,
            valuation_date=val_date,
            current_value=current_value,
            invested_amount=invested,
            status=status,
            message=message,
            metadata={
                "declared_rates": rates,
                "latest_year": latest_year,
                "opacity_acknowledged": True,
                "cashflows_count": len(cashflows) if use_cashflows else 0,
            },
        )

    def _load_declared_rates(self, asset_id: str) -> dict[int, dict[str, Any]]:
        rates_file = self.market_data_dir / f"fonds_euro_{asset_id}.yaml"
        if not rates_file.exists():
            return {}
        data = yaml.safe_load(rates_file.read_text(encoding="utf-8"))
        if not data or "declared_rates" not in data:
            return {}
        rates = {}
        for rate_entry in data["declared_rates"]:
            year = rate_entry["year"]
            rates[year] = {"rate": rate_entry.get("rate"), "source": rate_entry.get("source", "unknown"), "date": rate_entry.get("date")}
        return rates

    def _compute_value_from_rates(self, invested: float, subscription_date: date, valuation_date: date, rates: dict[int, dict[str, Any]]) -> Optional[float]:
        value = invested
        start_year = subscription_date.year
        if not rates:
            return None
        last_available_year = max(rates.keys())
        end_year = min(valuation_date.year, last_available_year)
        for year in range(start_year, end_year + 1):
            if year not in rates or rates[year].get("rate") is None:
                return None
            rate = rates[year]["rate"] / 100.0
            if year == start_year:
                days_invested = (date(year, 12, 31) - subscription_date).days
                value *= 1 + rate * (days_invested / 365.0)
            elif year == end_year:
                if end_year == last_available_year and end_year < valuation_date.year:
                    value *= 1 + rate
                else:
                    days_invested = (valuation_date - date(year, 1, 1)).days
                    value *= 1 + rate * (days_invested / 365.0)
            else:
                value *= 1 + rate
        return value

    @staticmethod
    def _extract_cashflows(*, projection) -> list[tuple[date, str, float]]:
        out = []
        for movement in projection.movements:
            if movement.movement_kind == MovementKind.EXTERNAL_CONTRIBUTION:
                kind = "buy"
            elif movement.movement_kind == MovementKind.INTERNAL_CAPITALIZATION:
                kind = "income"
            elif movement.movement_kind == MovementKind.FEE:
                kind = "fee"
            elif movement.movement_kind == MovementKind.TAX:
                kind = "tax"
            elif movement.movement_kind == MovementKind.WITHDRAWAL:
                kind = "withdrawal"
            else:
                kind = "other"
            out.append((movement.effective_date, kind, movement.cash_amount))
        return sorted(out, key=lambda item: item[0])

    def _compute_value_from_cashflows(self, *, cashflows: list[tuple[date, str, float]], valuation_date: date, rates: dict[int, dict[str, Any]]) -> Optional[float]:
        total = 0.0
        for cashflow_date, _, amount in cashflows:
            if cashflow_date > valuation_date:
                continue
            value = self._compute_value_from_rates(abs(float(amount)), cashflow_date, valuation_date, rates)
            if value is None:
                return None
            total += -float(value) if amount < 0 else float(value)
        return total


class EventBasedEngine(BaseValuationEngine):
    def valuate(self, asset: Asset, position: Position, valuation_date: Optional[date] = None) -> ValuationResult:
        val_date = self._get_valuation_date(valuation_date)
        metadata = asset.metadata
        if not metadata:
            return ValuationResult(position_id=position.position_id, asset_id=asset.asset_id, valuation_date=val_date, status="error", message="Metadonnees du produit structure manquantes")
        events, expected_events = self._load_event_file(asset.asset_id)
        current_period = self._identify_current_period(metadata, position.investment.subscription_date, val_date)
        units_held_yaml = position.investment.units_held
        lots = position.investment.lots or []
        projection = self._project_position(asset, position, val_date)
        current_projection = self._project_position(asset, position, date.today())
        sell_value = current_projection.realized_exit_value
        sell_date = current_projection.close_date

        if units_held_yaml is not None:
            units_held_float = float(units_held_yaml)
            if abs(units_held_float) < 0.01 and current_projection.is_closed and sell_date is not None and sell_date <= val_date:
                invested = position.investment.invested_amount or 0.0
                invested_for_valuation = projection.buy_trade_notional_total if projection.buy_trade_notional_total > 0 else invested
                return ValuationResult(
                    position_id=position.position_id,
                    asset_id=asset.asset_id,
                    valuation_date=val_date,
                    current_value=float(sell_value) if sell_value is not None else 0.0,
                    invested_amount=invested,
                    status="ok",
                    message="Position historique (vendue/reinvestie, units_held=0)" + (f" - Valeur de vente: {sell_value:,.2f} EUR" if sell_value is not None else ""),
                    metadata={
                        "invested_for_valuation": invested_for_valuation if projection.buy_trade_notional_total > 0 else None,
                        "buy_amount_total": projection.buy_trade_notional_total if projection.buy_trade_notional_total > 0 else None,
                        "sell_value": sell_value,
                        "sell_date": sell_date.isoformat() if sell_date is not None else None,
                    },
                )

        if projection.is_closed and units_held_yaml is None:
            invested = position.investment.invested_amount or 0.0
            invested_for_valuation = projection.buy_trade_notional_total if projection.buy_trade_notional_total > 0 else invested
            return ValuationResult(
                position_id=position.position_id,
                asset_id=asset.asset_id,
                valuation_date=val_date,
                current_value=float(sell_value) if sell_value is not None else 0.0,
                invested_amount=invested,
                status="ok",
                message="Position historique (vendue/reinvestie, units_held=0 depuis projection)" + (f" - Valeur de vente: {sell_value:,.2f} EUR" if sell_value is not None else ""),
                metadata={
                    "invested_for_valuation": invested_for_valuation if projection.buy_trade_notional_total > 0 else None,
                    "buy_amount_total": projection.buy_trade_notional_total if projection.buy_trade_notional_total > 0 else None,
                    "sell_value": sell_value,
                    "sell_date": sell_date.isoformat() if sell_date is not None else None,
                },
            )

        has_buy_before_val_date = any(movement.raw_lot_type == "buy" and movement.cash_amount > 0 for movement in projection.movements)
        if lots and not has_buy_before_val_date:
            return ValuationResult(position_id=position.position_id, asset_id=asset.asset_id, valuation_date=val_date, current_value=0.0, invested_amount=0.0, status="ok", message="Position non encore ouverte a cette date")

        invested = position.investment.invested_amount
        buy_amount_total = projection.buy_trade_notional_total
        cashflow_adjustments = projection.signed_cash_balance - projection.buy_trade_notional_total
        lots_has_amounts = bool(projection.movements)
        if invested is None and buy_amount_total:
            invested = buy_amount_total
        if invested is None:
            invested = 0.0
        invested_for_valuation = buy_amount_total if buy_amount_total > 0 else invested

        coupons_recorded = sum(e.amount for e in events if e.event_type == "coupon" and e.event_date <= val_date and e.amount)
        coupons_estimated = 0.0
        if self._is_cms_product(metadata) and metadata.get("cms_past_coupons_confirmed_paid", False):
            coupons_estimated = self._estimate_cms_paid_coupons_from_expected(
                expected_events=expected_events,
                invested_amount=invested_for_valuation,
                realized_events=events,
                valuation_date=val_date,
            )
        coupons_received = coupons_recorded + coupons_estimated
        autocalled = any(e.event_type == "autocall" and e.event_date <= val_date for e in events)
        theoretical_coupons_total = 0.0
        if not autocalled:
            theoretical_coupons_total = self._calculate_theoretical_coupons_if_strike(
                metadata=metadata,
                expected_events=expected_events,
                invested_amount=invested_for_valuation,
                subscription_date=position.investment.subscription_date,
                valuation_date=val_date,
            )
        if autocalled:
            current_value = 0.0
            status = "ok"
            message = "Produit rembourse par autocall"
        else:
            current_value = invested_for_valuation + theoretical_coupons_total + cashflow_adjustments
            status = "ok"
            message = f"Periode {current_period}"
        next_expected = self._next_expected_payment(expected_events, val_date)
        overdue_expected = self._overdue_expected_payments(expected_events, events, val_date, grace_days=7)
        return ValuationResult(
            position_id=position.position_id,
            asset_id=asset.asset_id,
            valuation_date=val_date,
            current_value=current_value,
            invested_amount=invested,
            events=events + expected_events,
            status=status,
            message=message,
            metadata={
                "current_period": current_period,
                "coupons_received": coupons_received,
                "coupons_recorded": coupons_recorded,
                "coupons_estimated": coupons_estimated,
                "theoretical_coupons_if_strike": theoretical_coupons_total if not autocalled else 0.0,
                "autocalled": autocalled,
                "buy_amount_total": buy_amount_total if lots_has_amounts else None,
                "invested_for_valuation": invested_for_valuation if lots_has_amounts else None,
                "cashflow_adjustments": cashflow_adjustments if lots_has_amounts else None,
                "expected_events_count": len(expected_events),
                "next_expected_event": next_expected,
                "expected_overdue_count": len(overdue_expected),
                "expected_overdue_events": overdue_expected[:10],
            },
        )

    def _load_event_file(self, asset_id: str) -> tuple[list[ValuationEvent], list[ValuationEvent]]:
        events_file = self.market_data_dir / f"events_{asset_id}.yaml"
        if not events_file.exists():
            return [], []
        data = yaml.safe_load(events_file.read_text(encoding="utf-8"))
        if not data:
            return [], []
        return self._parse_events_list(data.get("events", []), expected=False), self._parse_events_list(data.get("expected_events", []), expected=True)

    def _parse_events_list(self, raw_events: list[Any], expected: bool) -> list[ValuationEvent]:
        events = []
        for event_data in raw_events or []:
            if not isinstance(event_data, dict) or "type" not in event_data or "date" not in event_data:
                continue
            md = dict(event_data.get("metadata") or {})
            if expected:
                md.setdefault("expected", True)
            try:
                event_date = datetime.fromisoformat(str(event_data["date"])).date()
            except Exception:
                continue
            events.append(
                ValuationEvent(
                    event_type=str(event_data["type"]),
                    event_date=event_date,
                    amount=event_data.get("amount"),
                    description=event_data.get("description", ""),
                    metadata=md,
                )
            )
        return events

    def _identify_current_period(self, metadata: dict[str, Any], subscription_date: date, valuation_date: date) -> int:
        period_months = metadata.get("period_months", 12)
        months_elapsed = (valuation_date.year - subscription_date.year) * 12 + (valuation_date.month - subscription_date.month)
        period = (months_elapsed // period_months) + 1
        return max(1, period)

    def _is_cms_product(self, asset_metadata: dict[str, Any]) -> bool:
        underlying = str((asset_metadata or {}).get("underlying") or (asset_metadata or {}).get("underlying_id") or "").upper()
        return "CMS" in underlying

    def _estimate_cms_paid_coupons_from_expected(self, *, expected_events: list[ValuationEvent], invested_amount: float, realized_events: list[ValuationEvent], valuation_date: date) -> float:
        realized_coupon_dates = [event.event_date for event in realized_events if (event.event_type or "").lower() == "coupon"]
        total = 0.0
        for event in expected_events:
            if (event.event_type or "").lower() != "coupon_expected" or event.event_date > valuation_date or event.amount is None:
                continue
            matched = any(abs((realized_date - event.event_date).days) <= 7 for realized_date in realized_coupon_dates)
            if matched:
                continue
            try:
                amount = float(event.amount)
                total += float(invested_amount) * amount if amount <= 1.0 else amount
            except Exception:
                continue
        return total

    def _is_expected_payment_type(self, event_type: str) -> bool:
        lowered = (event_type or "").lower()
        return lowered.endswith("_expected") or lowered.endswith("_payment_expected") or lowered in {"maturity_expected", "maturity_payment_expected"}

    def _next_expected_payment(self, expected_events: list[ValuationEvent], valuation_date: date) -> Optional[dict[str, Any]]:
        upcoming = [event for event in expected_events if self._is_expected_payment_type(event.event_type) and event.event_date >= valuation_date]
        if not upcoming:
            return None
        event = sorted(upcoming, key=lambda item: item.event_date)[0]
        return {"type": event.event_type, "date": event.event_date.isoformat(), "description": event.description, "amount": event.amount}

    def _expected_to_real_type(self, expected_type: str) -> Optional[str]:
        return {
            "coupon_expected": "coupon",
            "autocall_payment_expected": "autocall",
            "maturity_payment_expected": "maturity",
            "maturity_expected": "maturity",
        }.get((expected_type or "").lower())

    def _overdue_expected_payments(self, expected_events: list[ValuationEvent], realized_events: list[ValuationEvent], valuation_date: date, grace_days: int = 7) -> list[dict[str, Any]]:
        cutoff = valuation_date - timedelta(days=grace_days) if grace_days and grace_days > 0 else valuation_date
        realized_by_type: dict[str, list[date]] = {}
        for event in realized_events:
            realized_by_type.setdefault((event.event_type or "").lower(), []).append(event.event_date)
        overdue = []
        for event in expected_events:
            if not self._is_expected_payment_type(event.event_type) or event.event_date >= cutoff:
                continue
            real_type = self._expected_to_real_type(event.event_type)
            if not real_type:
                continue
            matched = any(abs((realized_date - event.event_date).days) <= 7 for realized_date in realized_by_type.get(real_type, []))
            if matched:
                continue
            overdue.append({"type": event.event_type, "date": event.event_date.isoformat(), "description": event.description, "amount": event.amount})
        overdue.sort(key=lambda item: item["date"])
        return overdue

    def _calculate_theoretical_coupons_if_strike(self, *, metadata: dict[str, Any], expected_events: list[ValuationEvent], invested_amount: float, subscription_date: date, valuation_date: date) -> float:
        if invested_amount <= 0:
            return 0.0
        if self._is_cms_product(metadata) and not metadata.get("cms_past_coupons_confirmed_paid", False):
            return 0.0
        gain_per_period = None
        for event in expected_events:
            md = event.metadata or {}
            if not md.get("expected", False):
                continue
            gps = md.get("gain_per_semester")
            if gps is not None:
                try:
                    gain_per_period = float(gps)
                    break
                except Exception:
                    continue
            coupon_rate = md.get("coupon_rate")
            if coupon_rate is not None:
                try:
                    coupon_rate = float(coupon_rate)
                    gain_per_period = coupon_rate if coupon_rate <= 1.0 else coupon_rate / 100.0
                    break
                except Exception:
                    continue
        if gain_per_period is None:
            gps_meta = metadata.get("gain_per_semester")
            if gps_meta is not None:
                try:
                    gain_per_period = float(gps_meta)
                except Exception:
                    pass
        if gain_per_period is None:
            coupon_rate = metadata.get("coupon_rate")
            if coupon_rate is not None:
                try:
                    coupon_rate = float(coupon_rate)
                    gain_per_period = coupon_rate if coupon_rate <= 1.0 else coupon_rate / 100.0
                except Exception:
                    pass
        if gain_per_period is None:
            return 0.0
        period_months = metadata.get("period_months", 6)
        months_elapsed = (valuation_date.year - subscription_date.year) * 12 + (valuation_date.month - subscription_date.month)
        periods_completed = max(0, months_elapsed // period_months)
        return invested_amount * gain_per_period * periods_completed
