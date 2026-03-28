"""Runtime V2 indépendant du CLI historique."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional
import re

import yaml

from ..domain import LotClassifier, PositionAnalyticsService, PositionProjectionService
from .models import AssetType, PortfolioData, Position, ValuationEngine
from .providers import QuantalysProvider, RatesProvider, UnderlyingProvider
from .valuation import DeclarativeEngine, EventBasedEngine, HybridEngine, MarkToMarketEngine


@dataclass
class V2ValuationEvent:
    event_type: str
    event_date: date
    amount: float | None = None
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class V2Runtime:
    """Assemble les briques métier nécessaires à la V2 sans dépendre du CLI ni du ledger V1."""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.market_data_dir = self.data_dir / "market_data"
        self.portfolio = PortfolioData(self.data_dir)
        self.lot_classifier = LotClassifier()
        self.projection_service = PositionProjectionService()
        self.analytics_service = PositionAnalyticsService(
            projection_service=self.projection_service,
            classifier=self.lot_classifier,
        )
        self.underlyings_provider = UnderlyingProvider(self.market_data_dir)
        self.rates_provider = RatesProvider(self.market_data_dir)
        self.quantalys_provider = QuantalysProvider(self.market_data_dir)
        self.engines = {
            ValuationEngine.EVENT_BASED: EventBasedEngine(self.data_dir),
            ValuationEngine.DECLARATIVE: DeclarativeEngine(self.data_dir),
            ValuationEngine.MARK_TO_MARKET: MarkToMarketEngine(self.data_dir),
            ValuationEngine.HYBRID: HybridEngine(self.data_dir),
        }

    @staticmethod
    def _short_portfolio_name(contract_name: Optional[str]) -> str:
        contract_name = contract_name or ""
        return contract_name[:5] if len(contract_name) > 5 else contract_name

    def _filter_positions_by_portfolio(self, positions: list[Position], portfolio_name: Optional[str]) -> list[Position]:
        if not portfolio_name:
            return list(positions)
        portfolio_filter = self._short_portfolio_name(portfolio_name)
        return [
            position
            for position in positions
            if self._short_portfolio_name(position.wrapper.contract_name)[:5] == portfolio_filter[:5]
        ]

    def get_uc_summary_rows(
        self,
        *,
        include_terminated: bool = False,
        portfolio_name: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        today = datetime.now().date()
        rows = []
        positions = self._filter_positions_by_portfolio(self.portfolio.list_all_positions(), portfolio_name)
        for position in positions:
            asset = self.portfolio.get_asset(position.asset_id)
            if not asset or asset.asset_type.value not in {"uc_fund", "uc_illiquid"}:
                continue
            engine = self.engines.get(asset.valuation_engine)
            if not engine:
                continue
            result = engine.valuate(asset, position, today)
            current_value = result.current_value or 0.0
            is_sold = self.is_position_sold(position)
            if is_sold and abs(current_value) < 0.01 and not include_terminated:
                continue
            lots = position.investment.lots or []
            invested_amount = position.investment.invested_amount
            if lots:
                invested_amount = self.calculate_invested_amounts(lots, position.position_id)["invested_total"]
            fees = 0.0
            cashflow_adjustments = (result.metadata or {}).get("cashflow_adjustments")
            if cashflow_adjustments is not None:
                fees = abs(float(cashflow_adjustments))
            rows.append(
                {
                    "portfolio_name": self._short_portfolio_name(position.wrapper.contract_name),
                    "invested_amount": float(invested_amount) if invested_amount else 0.0,
                    "current_value": float(current_value),
                    "gain": float(current_value) - float(invested_amount) if invested_amount else 0.0,
                    "fees": fees,
                    "is_sold": is_sold,
                }
            )
        if not include_terminated:
            rows = [row for row in rows if not row["is_sold"]]
        return rows

    def get_uc_view_rows(
        self,
        *,
        include_terminated: bool = False,
        portfolio_name: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        today = datetime.now().date()
        rows = []
        positions = self._filter_positions_by_portfolio(self.portfolio.list_all_positions(), portfolio_name)
        for position in positions:
            asset = self.portfolio.get_asset(position.asset_id)
            if not asset or asset.asset_type.value not in {"uc_fund", "uc_illiquid"}:
                continue
            engine = self.engines.get(asset.valuation_engine)
            if not engine:
                continue
            result = engine.valuate(asset, position, today)
            current_value = result.current_value or 0.0
            is_sold = self.is_position_sold(position)
            lots = position.investment.lots or []
            invested_amount = position.investment.invested_amount
            if lots:
                invested_amount = self.calculate_invested_amounts(lots, position.position_id)["invested_total"]
            subscription_date = position.investment.subscription_date
            valuation_date_for_months = self.get_valuation_date_for_months(position, lots, today)
            months = self.months_elapsed(subscription_date, valuation_date_for_months)
            sell_date = self.extract_sell_date_from_lots(lots) if is_sold else None
            perf_metrics = self.calculate_performance_metrics(
                current_value=float(current_value) if current_value is not None else 0.0,
                invested_amount=float(invested_amount) if invested_amount else 0.0,
                subscription_date=subscription_date,
                position_id=position.position_id,
                end_date=valuation_date_for_months,
                lots=lots,
            )
            gain_amt = perf_metrics["gain"]
            perf_amt = perf_metrics["perf"]
            perf_annualized = perf_metrics["perf_annualized"]
            if perf_amt is not None and months > 0:
                years_from_months = months / 12.0
                if years_from_months > 0:
                    perf_annualized = ((1.0 + perf_amt / 100.0) ** (1.0 / years_from_months) - 1.0) * 100.0
            contract_name = position.wrapper.contract_name or ""
            portfolio_short_name = self._short_portfolio_name(contract_name)
            quantalys_display = ""
            if asset.isin:
                rating_display = self.quantalys_provider.get_rating_display(asset.isin)
                if rating_display and rating_display != "N/A":
                    quantalys_display = rating_display
            md = result.metadata or {}
            fees_total = self.calculate_fees_total(lots, md)
            rows.append(
                {
                    "asset_id": asset.asset_id,
                    "name": asset.name,
                    "display_name": asset.name,
                    "contract_name": contract_name,
                    "portfolio_name": portfolio_short_name,
                    "position_id": position.position_id,
                    "subscription_date": subscription_date,
                    "months": months,
                    "invested_amount": invested_amount,
                    "current_value": current_value,
                    "gain": gain_amt,
                    "perf": perf_amt,
                    "perf_annualized": perf_annualized,
                    "is_sold": is_sold,
                    "sell_date": sell_date,
                    "fees_total": fees_total,
                    "quantalys_display": quantalys_display,
                    "purchase_nav": md.get("purchase_nav") or position.investment.purchase_nav,
                    "purchase_nav_source": md.get("purchase_nav_source") or position.investment.purchase_nav_source,
                    "nav": md.get("nav"),
                    "nav_date": md.get("nav_date"),
                    "perf_pct": md.get("perf_pct"),
                    "result": result,
                    "position": position,
                    "asset": asset,
                }
            )
        if not include_terminated:
            rows = [row for row in rows if not row["is_sold"]]
        asset_id_counts: dict[str, int] = {}
        for row in rows:
            asset_id_counts[row["asset_id"]] = asset_id_counts.get(row["asset_id"], 0) + 1
        for row in rows:
            if asset_id_counts.get(row["asset_id"], 0) > 1:
                row["display_name"] = f"{row['name']} ({row['contract_name']})"
        rows.sort(key=lambda row: row["display_name"])
        return rows

    def get_structured_summary_rows(
        self,
        *,
        include_terminated: bool = False,
        portfolio_name: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        today = datetime.now().date()
        rows = []
        positions = self._filter_positions_by_portfolio(self.portfolio.list_all_positions(), portfolio_name)
        for position in positions:
            asset = self.portfolio.get_asset(position.asset_id)
            if not asset or asset.asset_type != AssetType.STRUCTURED_PRODUCT:
                continue
            engine = self.engines.get(asset.valuation_engine)
            if not engine:
                continue
            result = engine.valuate(asset, position, today)
            current_value = result.current_value if result else None
            lots = position.investment.lots or []
            invested_amount = position.investment.invested_amount
            if lots:
                invested_amount = self.calculate_invested_amounts(lots, position.position_id)["invested_total"]
            is_sold_or_terminated = self.is_structured_product_terminated(result, lots, current_value, invested_amount)
            if current_value is None:
                current_value = 0.0
            if is_sold_or_terminated and not include_terminated:
                continue
            fees = 0.0
            cashflow_adjustments = (result.metadata or {}).get("cashflow_adjustments")
            if cashflow_adjustments is not None:
                fees = abs(float(cashflow_adjustments))
            rows.append(
                {
                    "portfolio_name": self._short_portfolio_name(position.wrapper.contract_name),
                    "invested_amount": float(invested_amount) if invested_amount else 0.0,
                    "current_value": float(current_value),
                    "gain": float(current_value) - float(invested_amount) if invested_amount else 0.0,
                    "fees": fees,
                    "is_sold_or_terminated": is_sold_or_terminated,
                }
            )
        if not include_terminated:
            rows = [row for row in rows if not row.get("is_sold_or_terminated", False)]
        return rows

    @staticmethod
    def _find_initial_observation_date(asset: Any, events: list[Any]) -> str | None:
        aiod = (asset.metadata or {}).get("initial_observation_date")
        if aiod:
            return str(aiod)
        for event in events:
            metadata = getattr(event, "metadata", None) or {}
            iod = metadata.get("initial_observation_date")
            if iod:
                return str(iod)
        return None

    @staticmethod
    def _next_observation_event(events: list[Any], today_: date) -> tuple[str | None, Any | None]:
        candidates = []
        for event in events:
            event_type = (getattr(event, "event_type", "") or "").lower()
            if event_type.endswith("_expected") or event_type.endswith("_payment_expected"):
                continue
            metadata = getattr(event, "metadata", None) or {}
            if not metadata.get("expected", False):
                continue
            if ("observation" not in event_type) and (event_type not in {"autocall_possible"}):
                continue
            observation_date = metadata.get("observation_date") or getattr(event, "event_date")
            try:
                if not hasattr(observation_date, "year"):
                    observation_date = datetime.fromisoformat(str(observation_date)).date()
            except Exception:
                continue
            if observation_date >= today_:
                candidates.append((observation_date, event))
        if not candidates:
            return None, None
        observation_date, event = min(candidates, key=lambda item: item[0])
        return observation_date.isoformat(), event

    @staticmethod
    def _find_gain_per_semester(events: list[Any]) -> float | None:
        for event in events:
            metadata = getattr(event, "metadata", None) or {}
            if not metadata.get("expected", False):
                continue
            gps = metadata.get("gain_per_semester")
            if gps is not None:
                try:
                    return float(gps)
                except Exception:
                    continue
        for event in events:
            metadata = getattr(event, "metadata", None) or {}
            if not metadata.get("expected", False):
                continue
            coupon_rate = metadata.get("coupon_rate")
            if coupon_rate is None:
                continue
            try:
                coupon_rate = float(coupon_rate)
                return coupon_rate if coupon_rate <= 1.0 else coupon_rate / 100.0
            except Exception:
                continue
        return None

    @staticmethod
    def _find_coupon_pct(asset: Any, events: list[Any]) -> float | None:
        for event in events:
            metadata = getattr(event, "metadata", None) or {}
            if not metadata.get("expected", False):
                continue
            gps = metadata.get("gain_per_semester")
            if gps is None:
                continue
            try:
                return float(gps) * 100.0
            except Exception:
                pass
        for event in events:
            metadata = getattr(event, "metadata", None) or {}
            if not metadata.get("expected", False):
                continue
            coupon_rate = metadata.get("coupon_rate")
            if coupon_rate is None:
                continue
            try:
                return float(coupon_rate) * 100.0
            except Exception:
                pass
        coupon_rate = (asset.metadata or {}).get("coupon_rate")
        if coupon_rate is None:
            return None
        try:
            coupon_rate = float(coupon_rate)
            return coupon_rate * 100.0 if coupon_rate <= 1.0 else coupon_rate
        except Exception:
            return None

    @staticmethod
    def _parse_condition_threshold(condition: str) -> tuple[str | None, float | None]:
        if not isinstance(condition, str):
            return None, None
        match = re.search(r"([<>]=?)\s*([0-9]+(?:[.,][0-9]+)?)\s*%?", condition.strip())
        if not match:
            return None, None
        operator = match.group(1)
        raw = match.group(2).replace(",", ".")
        try:
            return operator, float(raw)
        except Exception:
            return operator, None

    def _load_structured_coupon_tracking(self, *, asset_id: str, engine: Any, valuation_date: date) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        overdue_coupons: list[dict[str, Any]] = []
        coupon_status: list[dict[str, Any]] = []
        events_file = self.market_data_dir / f"events_{asset_id}.yaml"
        if not events_file.exists():
            return overdue_coupons, coupon_status
        try:
            events_data = yaml.safe_load(events_file.read_text(encoding="utf-8")) or {}
            raw_realized = events_data.get("events") or []
            raw_expected = events_data.get("expected_events") or []
            realized_events = []
            for event_data in raw_realized:
                if isinstance(event_data, dict) and "type" in event_data and "date" in event_data:
                    try:
                        event_date = datetime.fromisoformat(str(event_data["date"])).date()
                        realized_events.append(
                            V2ValuationEvent(
                                event_type=str(event_data["type"]),
                                event_date=event_date,
                                amount=event_data.get("amount"),
                                description=event_data.get("description", ""),
                                metadata=event_data.get("metadata", {}),
                            )
                        )
                    except Exception:
                        pass
            expected_events = []
            for event_data in raw_expected:
                if isinstance(event_data, dict) and "type" in event_data and "date" in event_data:
                    try:
                        event_date = datetime.fromisoformat(str(event_data["date"])).date()
                        metadata = dict(event_data.get("metadata") or {})
                        metadata.setdefault("expected", True)
                        expected_events.append(
                            V2ValuationEvent(
                                event_type=str(event_data["type"]),
                                event_date=event_date,
                                amount=event_data.get("amount"),
                                description=event_data.get("description", ""),
                                metadata=metadata,
                            )
                        )
                    except Exception:
                        pass
            if hasattr(engine, "_overdue_expected_payments"):
                overdue_expected = engine._overdue_expected_payments(
                    expected_events=expected_events,
                    realized_events=realized_events,
                    valuation_date=valuation_date,
                    grace_days=7,
                )
                overdue_coupons = [event for event in overdue_expected if "coupon" in event.get("type", "").lower()]
            for event in expected_events:
                if "coupon" not in (event.event_type or "").lower() or event.event_date > valuation_date:
                    continue
                matched = False
                for realized in realized_events:
                    if (realized.event_type or "").lower() == "coupon" and abs((realized.event_date - event.event_date).days) <= 7:
                        matched = True
                        break
                coupon_status.append(
                    {
                        "date": event.event_date,
                        "amount": event.amount,
                        "paid": matched,
                        "description": event.description or f"Coupon {event.event_date}",
                    }
                )
        except Exception:
            pass
        return overdue_coupons, coupon_status

    def get_structured_view_rows(
        self,
        *,
        include_terminated: bool = False,
        portfolio_name: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        today = datetime.now().date()
        rows = []
        positions = self._filter_positions_by_portfolio(self.portfolio.list_all_positions(), portfolio_name)
        for position in positions:
            asset = self.portfolio.get_asset(position.asset_id)
            if not asset or asset.asset_type != AssetType.STRUCTURED_PRODUCT:
                continue
            engine = self.engines.get(asset.valuation_engine)
            result = engine.valuate(asset, position, today) if engine else None
            current_value = result.current_value if result else None
            lots = position.investment.lots or []
            invested_amount = position.investment.invested_amount
            if lots:
                invested_amount = self.calculate_invested_amounts(lots, position.position_id)["invested_total"]
            coupons_received = (result.metadata or {}).get("coupons_received") if result else None
            autocalled = (result.metadata or {}).get("autocalled") if result else None
            sell_date = self.extract_sell_date_from_lots(lots)
            sell_value_from_lots = self.extract_sell_value_from_lots(lots)
            valuation_date_for_months = self.get_valuation_date_for_months(position, lots, today)
            months = self.months_elapsed(position.investment.subscription_date, valuation_date_for_months)
            events = result.events if result else []
            next_obs, next_obs_event = self._next_observation_event(events, today)
            gps = self._find_gain_per_semester(events)
            coupon_pct = self._find_coupon_pct(asset, events)
            underlying_id = (asset.metadata or {}).get("underlying") or (asset.metadata or {}).get("underlying_id")
            is_cms_product = bool(underlying_id and isinstance(underlying_id, str) and "CMS" in underlying_id.upper())
            overdue_coupons: list[dict[str, Any]] = []
            coupon_status: list[dict[str, Any]] = []
            if is_cms_product and result and hasattr(result, "events"):
                overdue_coupons, coupon_status = self._load_structured_coupon_tracking(
                    asset_id=asset.asset_id,
                    engine=engine,
                    valuation_date=today,
                )
            period_months = (asset.metadata or {}).get("period_months") or 6
            try:
                sem_elapsed = max(0, months // int(period_months))
            except Exception:
                sem_elapsed = 0
            theoretical_value = None
            if invested_amount and gps is not None:
                theoretical_value = float(invested_amount) * (1.0 + gps * sem_elapsed)
            value_if_strike_next = None
            gain_if_strike_next = None
            perf_if_strike_next = None
            perf_if_strike_next_annualized = None
            is_sold = (
                (sell_date is not None)
                or (autocalled is True)
                or (
                    current_value is not None
                    and abs(float(current_value or 0)) < 0.01
                    and invested_amount
                    and float(invested_amount) > 0
                )
            )
            if not is_sold and next_obs and invested_amount and gps is not None:
                try:
                    next_obs_date = datetime.fromisoformat(str(next_obs)).date() if isinstance(next_obs, str) else next_obs
                    semester_number = next_obs_event.metadata.get("semester") if next_obs_event and next_obs_event.metadata else None
                    if semester_number is not None:
                        total_periods_with_coupon = int(semester_number)
                    else:
                        months_until_next = self.months_elapsed(position.investment.subscription_date, next_obs_date)
                        periods_until_next = max(0, months_until_next // int(period_months))
                        total_periods_with_coupon = periods_until_next + 1
                    cashflow_adjustments = result.metadata.get("cashflow_adjustments") or 0.0
                    value_if_strike_next = float(invested_amount) * (1.0 + gps * total_periods_with_coupon) + float(cashflow_adjustments)
                    gain_if_strike_next = value_if_strike_next - float(invested_amount)
                    if float(invested_amount) != 0:
                        perf_if_strike_next = (gain_if_strike_next / float(invested_amount)) * 100.0
                        months_until_next_obs = self.months_elapsed(position.investment.subscription_date, next_obs_date)
                        if months_until_next_obs > 0:
                            years_until_next = months_until_next_obs / 12.0
                            perf_if_strike_next_annualized = ((1.0 + perf_if_strike_next / 100.0) ** (1.0 / years_until_next) - 1.0) * 100.0
                except Exception:
                    pass
            if not underlying_id:
                for event in events:
                    metadata = getattr(event, "metadata", None) or {}
                    event_underlying = metadata.get("underlying")
                    if event_underlying:
                        underlying_id = event_underlying
                        break
            strike_val = None
            strike_date_used = None
            strike_note = None
            is_rate_like = isinstance(underlying_id, str) and underlying_id.upper().startswith("CMS_")
            if underlying_id and not is_rate_like:
                strike_date = position.investment.subscription_date
                iod = self._find_initial_observation_date(asset, events)
                if iod:
                    try:
                        strike_date = datetime.fromisoformat(iod).date()
                    except Exception:
                        pass
                initial_level = (asset.metadata or {}).get("initial_level")
                if initial_level is not None:
                    try:
                        strike_val = float(initial_level)
                        strike_date_used = strike_date
                        strike_note = "from_brochure_level"
                    except Exception:
                        strike_val = None
                        strike_date_used = None
                        strike_note = None
                strike = self.underlyings_provider.get_data(underlying_id, "underlying", strike_date)
                if strike_val is None and strike:
                    strike_val = strike.get("value")
                    strike_date_used = strike.get("date")
                    if strike_date_used and strike_date_used != strike_date:
                        strike_note = f"fallback<= {strike_date.isoformat()}"
                elif strike_val is None:
                    strike_note = f"no_data_for<= {strike_date.isoformat()}"
            underlying_current = None
            underlying_current_date = None
            perf_vs_strike = None
            underlying_current_note = None
            if underlying_id:
                current_underlying = self.underlyings_provider.get_data(underlying_id, "underlying", today)
                if current_underlying:
                    underlying_current = current_underlying.get("value")
                    date_value = current_underlying.get("date")
                    underlying_current_date = date_value.isoformat() if date_value else None
                if underlying_current is None:
                    current_rate = self.rates_provider.get_data(str(underlying_id), "rate", today)
                    if current_rate:
                        underlying_current = current_rate.get("value")
                        date_value = current_rate.get("date")
                        underlying_current_date = date_value.isoformat() if date_value else None
                        underlying_current_note = "rates"
                if underlying_current is None:
                    asset_metadata = asset.metadata or {}
                    manual_value = asset_metadata.get("underlying_current_level")
                    if manual_value is None:
                        manual_value = asset_metadata.get("current_level")
                    if manual_value is not None:
                        try:
                            underlying_current = float(manual_value)
                            manual_date = asset_metadata.get("underlying_current_date") or asset_metadata.get("current_level_date")
                            underlying_current_date = str(manual_date) if manual_date else today.isoformat()
                            underlying_current_note = "manual"
                        except Exception:
                            underlying_current = None
                            underlying_current_date = None
                            underlying_current_note = None
            if underlying_id and not is_rate_like and strike_val is None:
                history = self.underlyings_provider.get_history(underlying_id)
                if history:
                    strike_val = history[0].value
                    strike_date_used = history[0].point_date
                    strike_note = f"approx_first_available({history[0].point_date.isoformat()})"
            if strike_val is not None and underlying_current is not None and strike_val != 0:
                try:
                    perf_vs_strike = (float(underlying_current) / float(strike_val) - 1.0) * 100.0
                except Exception:
                    perf_vs_strike = None
            redemption_trigger = None
            redemption_trigger_level = None
            redemption_trigger_pct = None
            redemption_operator = None
            redemption_threshold_value = None
            redemption_missing_reason = None
            if next_obs_event is not None:
                metadata = getattr(next_obs_event, "metadata", None) or {}
                pct = metadata.get("autocall_threshold_pct_of_initial")
                if pct is None:
                    pct = metadata.get("autocall_barrier_pct_of_initial")
                if pct is not None and strike_val is not None:
                    try:
                        redemption_trigger_pct = float(pct)
                        level = float(strike_val) * float(pct) / 100.0
                        redemption_trigger_level = level
                        redemption_operator = ">="
                        redemption_threshold_value = level
                        redemption_trigger = f">= {level:.4g} ({float(pct):.2f}% du initial)"
                    except Exception:
                        redemption_trigger = None
                if redemption_trigger is None:
                    condition = metadata.get("autocall_condition") or ""
                    if isinstance(condition, str) and "cms" in condition.lower():
                        redemption_operator, redemption_threshold_value = self._parse_condition_threshold(condition)
                        redemption_trigger = condition
                    elif isinstance(condition, str) and "initial" in condition.lower():
                        if strike_val is not None:
                            redemption_operator = ">="
                            redemption_threshold_value = float(strike_val)
                            redemption_trigger_level = float(strike_val)
                        else:
                            redemption_operator = ">="
                            redemption_threshold_value = None
                            redemption_missing_reason = "strike manquant (renseigner metadata.initial_level ou un historique)"
                        redemption_trigger = condition
                    elif (getattr(next_obs_event, "event_type", "") or "").lower() in {"autocall_observation", "autocall_possible"}:
                        fallback = metadata.get("autocall_condition") or "Index >= Initial"
                        if isinstance(fallback, str) and "initial" in fallback.lower() and strike_val is not None:
                            redemption_operator = ">="
                            redemption_threshold_value = float(strike_val)
                            redemption_trigger_level = float(strike_val)
                        elif isinstance(fallback, str) and "initial" in fallback.lower():
                            redemption_operator = ">="
                            redemption_threshold_value = None
                            redemption_missing_reason = "strike manquant (renseigner metadata.initial_level ou un historique)"
                        redemption_trigger = fallback
            contract_name = position.wrapper.contract_name
            fees_total = self.calculate_fees_total(lots, result.metadata or {})
            row = {
                "name": asset.name,
                "display_name": asset.name,
                "contract_name": contract_name,
                "portfolio_name": self._short_portfolio_name(contract_name),
                "position_id": position.position_id,
                "subscription_date": position.investment.subscription_date,
                "months": months,
                "period_months": period_months,
                "current_value": current_value,
                "invested_amount": invested_amount,
                "coupons_received": coupons_received,
                "fees_total": fees_total,
                "autocalled": autocalled,
                "cms_coupons_confirmed": bool((asset.metadata or {}).get("cms_past_coupons_confirmed_paid", False)),
                "gain_per_semester": gps,
                "coupon_pct": coupon_pct,
                "semesters_elapsed": sem_elapsed,
                "theoretical_value": theoretical_value,
                "value_if_strike_next": value_if_strike_next,
                "gain_if_strike_next": gain_if_strike_next,
                "perf_if_strike_next": perf_if_strike_next,
                "perf_if_strike_next_annualized": perf_if_strike_next_annualized,
                "sell_date": sell_date.isoformat() if sell_date else None,
                "sell_value_from_lots": sell_value_from_lots,
                "sell_value": (result.metadata or {}).get("sell_value") if result and result.metadata else sell_value_from_lots,
                "strike": strike_val,
                "strike_date": strike_date_used.isoformat() if strike_date_used else None,
                "strike_note": strike_note,
                "next_obs": next_obs,
                "underlying_id": underlying_id,
                "underlying_current": underlying_current,
                "underlying_current_date": underlying_current_date,
                "underlying_current_note": underlying_current_note,
                "perf_vs_strike": perf_vs_strike,
                "redemption_trigger": redemption_trigger,
                "redemption_trigger_level": redemption_trigger_level,
                "redemption_trigger_pct": redemption_trigger_pct,
                "redemption_operator": redemption_operator,
                "redemption_threshold_value": redemption_threshold_value,
                "redemption_missing_reason": redemption_missing_reason,
                "autocall_condition_threshold": (asset.metadata or {}).get("autocall_condition_threshold"),
                "coupon_condition_threshold": (asset.metadata or {}).get("coupon_condition_threshold"),
                "overdue_coupons": overdue_coupons,
                "coupon_status": coupon_status,
                "result": result,
                "position": position,
                "asset": asset,
            }
            row["is_sold_or_terminated"] = self.is_structured_product_terminated(result, lots, current_value, invested_amount)
            rows.append(row)
        asset_id_counts: dict[str, int] = {}
        for row in rows:
            position = self.portfolio.get_position(row["position_id"])
            if position:
                asset_id_counts[position.asset_id] = asset_id_counts.get(position.asset_id, 0) + 1
        for row in rows:
            position = self.portfolio.get_position(row["position_id"])
            if position and asset_id_counts.get(position.asset_id, 0) > 1:
                row["display_name"] = f"{row['name']} ({row['contract_name']})"
        rows.sort(key=lambda row: (row["name"], row["position_id"]))
        if not include_terminated:
            rows = [row for row in rows if not row.get("is_sold_or_terminated", False)]
        return rows

    def get_fonds_euro_summary_rows(
        self,
        *,
        include_terminated: bool = False,
        portfolio_name: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        today = datetime.now().date()
        rows = []
        positions = self._filter_positions_by_portfolio(self.portfolio.list_all_positions(), portfolio_name)
        for position in positions:
            asset = self.portfolio.get_asset(position.asset_id)
            if not asset or asset.asset_type != AssetType.FONDS_EURO:
                continue
            engine = self.engines.get(asset.valuation_engine)
            if not engine:
                continue
            result = engine.valuate(asset, position, today)
            current_value = result.current_value or 0.0
            lots = position.investment.lots or []
            is_sold = self.is_position_sold(position)
            invested_amount = self.calculate_fonds_euro_invested_amount(position, lots)
            if is_sold and abs(current_value) < 0.01 and invested_amount < 0.01 and not include_terminated:
                continue
            subscription_date = position.investment.subscription_date
            ref_date_end = self.get_fonds_euro_reference_date(lots, position.position_id, today)
            value_for_perf, invested_for_perf = self.calculate_fonds_euro_performance_values(
                current_value, lots, position.position_id, ref_date_end
            )
            perf_metrics = self.calculate_performance_metrics(
                current_value=float(current_value) if current_value is not None else 0.0,
                invested_amount=float(invested_amount) if invested_amount else 0.0,
                subscription_date=subscription_date,
                position_id=position.position_id,
                end_date=ref_date_end,
                lots=lots,
                value_for_perf=value_for_perf,
                invested_for_perf=invested_for_perf if invested_for_perf and invested_for_perf > 0 else None,
            )
            fees = 0.0
            cashflow_adjustments = (result.metadata or {}).get("cashflow_adjustments")
            if cashflow_adjustments is not None:
                fees = abs(float(cashflow_adjustments))
            rows.append(
                {
                    "portfolio_name": self._short_portfolio_name(position.wrapper.contract_name),
                    "invested_amount": float(invested_amount) if invested_amount else 0.0,
                    "current_value": float(current_value),
                    "gain": perf_metrics["gain"],
                    "fees": fees,
                    "is_sold": is_sold,
                }
            )
        if not include_terminated:
            rows = [row for row in rows if not row["is_sold"]]
        return rows

    def get_fonds_euro_view_rows(
        self,
        *,
        include_terminated: bool = False,
        portfolio_name: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        today = datetime.now().date()
        rows = []
        positions = self._filter_positions_by_portfolio(self.portfolio.list_all_positions(), portfolio_name)
        for position in positions:
            asset = self.portfolio.get_asset(position.asset_id)
            if not asset or asset.asset_type != AssetType.FONDS_EURO:
                continue
            engine = self.engines.get(asset.valuation_engine)
            if not engine:
                continue
            result = engine.valuate(asset, position, today)
            current_value = result.current_value or 0.0
            lots = position.investment.lots or []
            is_sold = self.is_position_sold(position)
            invested_amount = self.calculate_fonds_euro_invested_amount(position, lots)
            subscription_date = position.investment.subscription_date
            ref_date_end = self.get_fonds_euro_reference_date(lots, position.position_id, today)
            sell_date = self.extract_sell_date_from_lots(lots) if is_sold else None
            valuation_date_for_months = sell_date if sell_date and sell_date < ref_date_end else ref_date_end
            months = self.months_elapsed(subscription_date, valuation_date_for_months)
            value_for_perf, invested_for_perf = self.calculate_fonds_euro_performance_values(
                current_value, lots, position.position_id, ref_date_end
            )
            perf_metrics = self.calculate_performance_metrics(
                current_value=float(current_value) if current_value is not None else 0.0,
                invested_amount=float(invested_amount) if invested_amount else 0.0,
                subscription_date=subscription_date,
                position_id=position.position_id,
                end_date=ref_date_end,
                lots=lots,
                value_for_perf=value_for_perf,
                invested_for_perf=invested_for_perf if invested_for_perf and invested_for_perf > 0 else None,
            )
            gain_amt = perf_metrics["gain"]
            perf_amt = perf_metrics["perf"]
            perf_annualized = perf_metrics["perf_annualized"]
            if perf_amt is not None and months > 0:
                years_from_months = months / 12.0
                if years_from_months > 0:
                    perf_annualized = ((1.0 + perf_amt / 100.0) ** (1.0 / years_from_months) - 1.0) * 100.0
            contract_name = position.wrapper.contract_name or ""
            portfolio_short_name = self._short_portfolio_name(contract_name)
            insurer = (asset.metadata or {}).get("insurer", "")
            md = result.metadata or {}
            fees_total = self.calculate_fees_total(lots, md)
            rows.append(
                {
                    "asset_id": asset.asset_id,
                    "name": asset.name,
                    "display_name": asset.name,
                    "insurer": insurer,
                    "contract_name": contract_name,
                    "portfolio_name": portfolio_short_name,
                    "position_id": position.position_id,
                    "subscription_date": subscription_date,
                    "months": months,
                    "invested_amount": invested_amount,
                    "current_value": current_value,
                    "gain": gain_amt,
                    "perf": perf_amt,
                    "perf_annualized": perf_annualized,
                    "is_sold": is_sold,
                    "sell_date": sell_date,
                    "fees_total": fees_total,
                    "result": result,
                    "position": position,
                    "asset": asset,
                }
            )
        if not include_terminated:
            rows = [row for row in rows if not row["is_sold"]]
        asset_id_counts: dict[str, int] = {}
        for row in rows:
            asset_id_counts[row["asset_id"]] = asset_id_counts.get(row["asset_id"], 0) + 1
        for row in rows:
            if asset_id_counts.get(row["asset_id"], 0) > 1:
                row["display_name"] = f"{row['name']} ({row['contract_name']})"
        rows.sort(key=lambda row: row["display_name"])
        return rows

    def build_global_recap(
        self,
        *,
        fonds_euro_rows: list[dict[str, Any]],
        uc_rows: list[dict[str, Any]],
        structured_rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        recap_by_portfolio = defaultdict(
            lambda: {
                "fonds_euro": {"invested": 0.0, "value": 0.0, "gain": 0.0, "fees": 0.0, "count": 0},
                "uc": {"invested": 0.0, "value": 0.0, "gain": 0.0, "fees": 0.0, "count": 0},
                "structured": {"invested": 0.0, "value": 0.0, "gain": 0.0, "fees": 0.0, "count": 0},
            }
        )
        recap_by_type = {
            "fonds_euro": {"invested": 0.0, "value": 0.0, "gain": 0.0, "fees": 0.0, "count": 0},
            "uc": {"invested": 0.0, "value": 0.0, "gain": 0.0, "fees": 0.0, "count": 0},
            "structured": {"invested": 0.0, "value": 0.0, "gain": 0.0, "fees": 0.0, "count": 0},
        }

        def add_row(row: dict[str, Any], bucket: str) -> None:
            portfolio_name = row["portfolio_name"] or "Autre"
            recap_by_portfolio[portfolio_name][bucket]["invested"] += row["invested_amount"]
            recap_by_portfolio[portfolio_name][bucket]["value"] += row["current_value"]
            recap_by_portfolio[portfolio_name][bucket]["gain"] += row["gain"]
            recap_by_portfolio[portfolio_name][bucket]["fees"] += row.get("fees", 0.0)
            recap_by_portfolio[portfolio_name][bucket]["count"] += 1
            recap_by_type[bucket]["invested"] += row["invested_amount"]
            recap_by_type[bucket]["value"] += row["current_value"]
            recap_by_type[bucket]["gain"] += row["gain"]
            recap_by_type[bucket]["fees"] += row.get("fees", 0.0)
            recap_by_type[bucket]["count"] += 1

        for row in fonds_euro_rows:
            add_row(row, "fonds_euro")
        for row in uc_rows:
            add_row(row, "uc")
        for row in structured_rows:
            add_row(row, "structured")

        return {
            "by_portfolio": dict(recap_by_portfolio),
            "by_type": recap_by_type,
            "totals": {
                "invested": sum(data["invested"] for data in recap_by_type.values()),
                "value": sum(data["value"] for data in recap_by_type.values()),
                "gain": sum(data["gain"] for data in recap_by_type.values()),
                "fees": sum(data["fees"] for data in recap_by_type.values()),
                "count": sum(data["count"] for data in recap_by_type.values()),
            },
        }

    @staticmethod
    def _to_jsonable(value: Any) -> Any:
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        if isinstance(value, dict):
            return {key: V2Runtime._to_jsonable(item) for key, item in value.items()}
        if isinstance(value, list):
            return [V2Runtime._to_jsonable(item) for item in value]
        return value

    def _serialize_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        serialized = []
        for row in rows:
            clean = {}
            for key, value in row.items():
                if key in {"position", "asset", "result"}:
                    continue
                clean[key] = self._to_jsonable(value)
            serialized.append(clean)
        return serialized

    def build_web_payload(
        self,
        *,
        include_terminated: bool = False,
        portfolio_name: Optional[str] = None,
    ) -> dict[str, Any]:
        uc_rows = self.get_uc_view_rows(include_terminated=include_terminated, portfolio_name=portfolio_name)
        fonds_euro_rows = self.get_fonds_euro_view_rows(include_terminated=include_terminated, portfolio_name=portfolio_name)
        structured_rows = self.get_structured_view_rows(include_terminated=include_terminated, portfolio_name=portfolio_name)
        recap = self.build_global_recap(
            fonds_euro_rows=self.get_fonds_euro_summary_rows(include_terminated=include_terminated, portfolio_name=portfolio_name),
            uc_rows=self.get_uc_summary_rows(include_terminated=include_terminated, portfolio_name=portfolio_name),
            structured_rows=self.get_structured_summary_rows(include_terminated=include_terminated, portfolio_name=portfolio_name),
        )
        return {
            "filters": {
                "include_terminated": include_terminated,
                "portfolio_name": portfolio_name,
            },
            "views": {
                "uc": self._serialize_rows(uc_rows),
                "fonds_euro": self._serialize_rows(fonds_euro_rows),
                "structured": self._serialize_rows(structured_rows),
            },
            "recap": self._to_jsonable(recap),
        }

    def is_position_sold(self, position: Position) -> bool:
        return self.analytics_service.is_position_sold(position)

    def extract_sell_date_from_lots(self, lots: list[dict[str, Any]]) -> Optional[date]:
        return self.analytics_service.extract_sell_date(
            position_id="__v2__",
            asset_id="__v2__",
            lots=lots,
            valuation_date=datetime.now().date(),
        )

    def extract_sell_value_from_lots(self, lots: list[dict[str, Any]]) -> Optional[float]:
        return self.analytics_service.extract_sell_value(
            position_id="__v2__",
            asset_id="__v2__",
            lots=lots,
            valuation_date=datetime.now().date(),
        )

    def calculate_fees_total(self, lots: list[dict[str, Any]], metadata: Optional[dict[str, Any]] = None) -> float:
        return self.analytics_service.calculate_fees_total(
            position_id="__v2__",
            asset_id="__v2__",
            lots=lots,
            metadata=metadata,
            valuation_date=datetime.now().date(),
        )

    def calculate_fonds_euro_invested_amount(self, position: Position, lots: list[dict[str, Any]]) -> float:
        return self.analytics_service.calculate_fonds_euro_invested_amount(
            position=position,
            lots=lots,
            valuation_date=datetime.now().date(),
        )

    def get_fonds_euro_reference_date(self, lots: list[dict[str, Any]], position_id: str, today: date) -> date:
        return self.analytics_service.get_fonds_euro_reference_date(
            lots=lots,
            position_id=position_id,
            today=today,
        )

    def calculate_fonds_euro_performance_values(
        self,
        current_value: float,
        lots: list[dict[str, Any]],
        position_id: str,
        ref_date_end: date,
    ) -> tuple[Optional[float], Optional[float]]:
        return self.analytics_service.calculate_fonds_euro_performance_values(
            current_value=current_value,
            lots=lots,
            position_id=position_id,
            asset_id="__v2__",
            ref_date_end=ref_date_end,
            valuation_date=datetime.now().date(),
        )

    def is_structured_product_terminated(
        self,
        result,
        lots: list[dict[str, Any]],
        current_value: Optional[float],
        invested_amount: Optional[float],
    ) -> bool:
        autocalled = (result.metadata or {}).get("autocalled") if result else None
        sell_date = self.extract_sell_date_from_lots(lots)
        sell_value_from_lots = self.extract_sell_value_from_lots(lots)
        return (
            autocalled is True
            or sell_date is not None
            or sell_value_from_lots is not None
            or (
                current_value is not None
                and abs(float(current_value or 0)) < 0.01
                and invested_amount
                and float(invested_amount or 0) > 0
            )
        )

    def calculate_invested_amounts(
        self,
        lots: list[dict[str, Any]],
        position_id: str,
        ref_date: Optional[date] = None,
    ) -> dict[str, float]:
        return self.analytics_service.calculate_invested_amounts(
            position_id=position_id,
            asset_id="__v2__",
            lots=lots,
            ref_date=ref_date,
            valuation_date=datetime.now().date(),
        )

    def build_cashflows_for_xirr(
        self,
        lots: list[dict[str, Any]],
        position_id: str,
        value_at_end: float,
        end_date: date,
    ) -> list[tuple[date, float]]:
        return self.analytics_service.build_cashflows_for_xirr(
            position_id=position_id,
            asset_id="__v2__",
            lots=lots,
            value_at_end=value_at_end,
            end_date=end_date,
        )

    def calculate_xirr(
        self,
        cashflows: list[tuple[date, float]],
        guess: float = 0.1,
        max_iter: int = 100,
        precision: float = 1e-6,
    ) -> Optional[float]:
        if not cashflows or len(cashflows) < 2:
            return None
        cashflows = sorted(cashflows, key=lambda x: x[0])
        ref_date = cashflows[0][0]
        cf_data = [(((dt - ref_date).days / 365.25), amt) for dt, amt in cashflows]
        rate = guess
        for _ in range(max_iter):
            npv = 0.0
            npv_deriv = 0.0
            for t, amt in cf_data:
                npv += amt / ((1 + rate) ** t)
                npv_deriv -= t * amt / ((1 + rate) ** (t + 1))
            if abs(npv) < precision:
                return rate
            if npv_deriv == 0:
                return None
            rate = rate - npv / npv_deriv
        return None

    def calculate_performance_metrics(
        self,
        current_value: float,
        invested_amount: float,
        subscription_date: date,
        position_id: str,
        end_date: Optional[date] = None,
        lots: Optional[list] = None,
        value_for_perf: Optional[float] = None,
        invested_for_perf: Optional[float] = None,
    ) -> dict[str, Any]:
        result = {"gain": 0.0, "perf": None, "perf_annualized": None}
        if not current_value or not invested_amount or invested_amount <= 0:
            return result
        if end_date is None:
            end_date = datetime.now().date()

        gain = float(current_value) - float(invested_amount)
        result["gain"] = gain

        if lots and value_for_perf is not None and invested_for_perf is not None:
            value_for_xirr = float(value_for_perf)
            invested_for_xirr = float(invested_for_perf)
            if value_for_xirr and invested_for_xirr > 0:
                try:
                    cashflows = self.build_cashflows_for_xirr(lots, position_id, value_for_xirr, end_date)
                    if cashflows:
                        xirr_result = self.calculate_xirr(cashflows)
                        if xirr_result is not None:
                            result["perf_annualized"] = xirr_result * 100.0
                            days_elapsed = (end_date - subscription_date).days
                            if days_elapsed > 0:
                                years_real = days_elapsed / 365.25
                                if years_real > 0:
                                    result["perf"] = ((1.0 + xirr_result) ** years_real - 1.0) * 100.0
                            return result
                except (OverflowError, ValueError, ZeroDivisionError):
                    pass

        perf = (gain / float(invested_amount)) * 100.0
        result["perf"] = perf
        days_elapsed = (end_date - subscription_date).days
        if days_elapsed > 0:
            years_real = days_elapsed / 365.25
            if years_real > 0:
                result["perf_annualized"] = ((1.0 + perf / 100.0) ** (1.0 / years_real) - 1.0) * 100.0
        return result

    def get_valuation_date_for_months(
        self,
        position: Position,
        lots: list[dict[str, Any]],
        default_date: date,
    ) -> date:
        sell_date = self.extract_sell_date_from_lots(lots)
        return sell_date if sell_date else default_date

    @staticmethod
    def months_elapsed(start_date: date, end_date: date) -> int:
        months = (end_date.year - start_date.year) * 12 + (end_date.month - start_date.month)
        if end_date.day < start_date.day:
            months -= 1
        return max(0, months)
