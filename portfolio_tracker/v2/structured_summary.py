"""Synthese partagee des produits structures pour CLI et web."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from .runtime import V2Runtime


def _annualized_return(total_return_pct: float, start_date: date | None, end_date: date | None) -> float | None:
    if start_date is None or end_date is None or end_date <= start_date:
        return None
    years = (end_date - start_date).days / 365.25
    if years <= 0:
        return None
    return ((1.0 + total_return_pct / 100.0) ** (1.0 / years) - 1.0) * 100.0


def _can_redeem_today(row: dict[str, Any]) -> str:
    current = row.get("underlying_current")
    threshold = row.get("redemption_threshold_value")
    operator = row.get("redemption_operator")
    if current is None or threshold is None or operator not in {">", ">=", "<", "<="}:
        return "n/a"

    current = float(current)
    threshold = float(threshold)
    decision = {
        ">": current > threshold,
        ">=": current >= threshold,
        "<": current < threshold,
        "<=": current <= threshold,
    }[operator]
    return "OUI" if decision else "non"


def build_structured_summary_rows(
    runtime: V2Runtime,
    *,
    valuation_date: date | None = None,
    portfolio_name: str | None = None,
) -> list[dict[str, Any]]:
    valuation_date = valuation_date or datetime.now().date()
    raw_rows = runtime.get_structured_view_rows(portfolio_name=portfolio_name)
    rows: list[dict[str, Any]] = []

    for raw in raw_rows:
        invested_amount = float(raw.get("invested_amount") or 0.0)
        current_value = float(raw.get("current_value") or 0.0)
        gain = current_value - invested_amount
        perf = (gain / invested_amount * 100.0) if invested_amount else 0.0

        subscription_date = raw.get("subscription_date")
        perf_annualized = _annualized_return(perf, subscription_date, valuation_date)

        value_if_strike = float(raw.get("value_if_strike_next") or 0.0)
        gain_if_strike = float(raw.get("gain_if_strike_next") or 0.0)
        perf_if_strike = raw.get("perf_if_strike_next")
        next_obs_date = None
        next_obs = raw.get("next_obs")
        if next_obs:
            try:
                next_obs_date = date.fromisoformat(str(next_obs))
            except Exception:
                next_obs_date = None

        perf_if_strike_annualized = None
        if perf_if_strike is not None:
            perf_if_strike_annualized = _annualized_return(
                float(perf_if_strike),
                subscription_date,
                next_obs_date,
            )

        rows.append(
            {
                "position_id": raw.get("position_id"),
                "name": raw.get("display_name") or raw.get("name") or "",
                "portfolio_name": raw.get("portfolio_name") or "",
                "subscription_date": subscription_date.isoformat() if subscription_date else None,
                "months": int(raw.get("months") or 0),
                "next_observation_date": next_obs,
                "redeem_if_today": _can_redeem_today(raw),
                "coupon_pct": float(raw.get("coupon_pct") or 0.0),
                "invested_amount": invested_amount,
                "current_value": current_value,
                "gain": gain,
                "perf": perf,
                "perf_annualized": perf_annualized,
                "perf_if_strike_annualized": perf_if_strike_annualized,
                "value_if_strike": value_if_strike,
                "gain_if_strike": gain_if_strike,
                "perf_if_strike": float(perf_if_strike) if perf_if_strike is not None else None,
            }
        )

    rows.sort(key=lambda row: (row["name"], row["portfolio_name"], row["subscription_date"] or ""))
    return rows
