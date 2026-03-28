"""Données de marché V2."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from .providers import load_nav_sources_cfg
from .runtime import V2Runtime


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _build_quantalys_lookup(market_data_dir: Path) -> dict[str, dict[str, Any]]:
    ratings_path = Path(market_data_dir) / "quantalys_ratings.yaml"
    if not ratings_path.exists():
        return {}
    payload = _load_yaml(ratings_path)
    lookup: dict[str, dict[str, Any]] = {}
    for row in payload.get("ratings") or []:
        isin = row.get("isin")
        if not isin:
            continue
        lookup[str(isin)] = {
            "rating": row.get("quantalys_rating"),
            "category": row.get("quantalys_category"),
            "last_update": row.get("last_update"),
            "notes": row.get("notes"),
        }
    return lookup


def _morningstar_url(secid: str | None) -> str | None:
    if not secid:
        return None
    clean_secid = str(secid).strip()
    if not clean_secid:
        return None
    return f"https://www.morningstar.fr/fr/funds/snapshot/snapshot.aspx?id={clean_secid}"


def _filter_points(points: list[dict[str, Any]], date_from: str | None, date_to: str | None) -> list[dict[str, Any]]:
    start = date.fromisoformat(date_from) if date_from else None
    end = date.fromisoformat(date_to) if date_to else None
    filtered = []
    for point in points:
        point_date = date.fromisoformat(str(point["date"]))
        if start and point_date < start:
            continue
        if end and point_date > end:
            continue
        filtered.append(point)
    return filtered


def load_market_series(
    data_dir: Path,
    *,
    kind: str,
    identifier: str,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    data_dir = Path(data_dir)
    market_data_dir = data_dir / "market_data"

    if kind == "uc":
        path = market_data_dir / f"nav_{identifier}.yaml"
        if not path.exists():
            raise KeyError(f"Série UC introuvable: {identifier}")
        payload = _load_yaml(path)
        points = [
            {
                "date": row["date"],
                "value": float(row["value"]),
                "source": row.get("source"),
                "currency": row.get("currency"),
            }
            for row in (payload.get("nav_history") or [])
        ]
    elif kind == "underlying":
        safe_identifier = identifier.replace(":", "_").replace("/", "_")
        path = market_data_dir / f"underlying_{safe_identifier}.yaml"
        if not path.exists():
            raise KeyError(f"Série sous-jacent introuvable: {identifier}")
        payload = _load_yaml(path)
        points = [
            {
                "date": row["date"],
                "value": float(row["value"]),
                "source": payload.get("source"),
            }
            for row in (payload.get("history") or [])
        ]
    elif kind == "rate":
        safe_identifier = identifier.replace(":", "_").replace("/", "_")
        path = market_data_dir / f"rates_{safe_identifier}.yaml"
        if not path.exists():
            raise KeyError(f"Série taux introuvable: {identifier}")
        payload = _load_yaml(path)
        points = [
            {
                "date": row["date"],
                "value": float(row["value"]),
                "source": payload.get("source"),
            }
            for row in (payload.get("history") or [])
        ]
    else:
        raise KeyError(f"Type de série inconnu: {kind}")

    filtered = _filter_points(points, date_from, date_to)
    return {
        "kind": kind,
        "identifier": identifier,
        "date_from": date_from,
        "date_to": date_to,
        "points": filtered,
    }


def build_v2_market_data(data_dir: Path) -> dict[str, Any]:
    data_dir = Path(data_dir)
    runtime = V2Runtime(data_dir)
    market_data_dir = data_dir / "market_data"
    quantalys_lookup = _build_quantalys_lookup(market_data_dir)
    nav_sources_lookup = load_nav_sources_cfg(market_data_dir)

    uc_rows_by_asset: dict[str, list[dict[str, Any]]] = {}
    structured_rows: list[dict[str, Any]] = []
    today = datetime.now().date()

    for position in runtime.portfolio.list_all_positions():
        asset = runtime.portfolio.get_asset(position.asset_id)
        if not asset:
            continue
        contract_name = str(position.wrapper.contract_name or "Sans contrat")
        subscription_date = position.investment.subscription_date.isoformat() if position.investment.subscription_date else None
        lots = position.investment.lots or []

        if asset.asset_type.value in {"uc_fund", "uc_illiquid"}:
            invested_amount = float(position.investment.invested_amount or 0.0)
            if lots:
                invested_amount = runtime.calculate_invested_amounts(lots, position.position_id)["invested_total"]
            current_value = 0.0
            nav_path = market_data_dir / f"nav_{asset.asset_id}.yaml"
            if nav_path.exists():
                payload = _load_yaml(nav_path)
                history = list(payload.get("nav_history") or [])
                if history:
                    latest_value = float(history[-1]["value"])
                    units = float(position.investment.units_held or 0.0)
                    current_value = units * latest_value if units else 0.0
            uc_rows_by_asset.setdefault(asset.asset_id, []).append(
                {
                    "position_id": position.position_id,
                    "contract_name": contract_name,
                    "subscription_date": subscription_date,
                    "current_value": current_value,
                    "invested_amount": invested_amount,
                }
            )

        if asset.asset_type.value == "structured_product":
            engine = runtime.engines.get(asset.valuation_engine)
            if not engine:
                continue
            result = engine.valuate(asset, position, today)
            current_value = float(result.current_value or 0.0)
            underlying_id = (asset.metadata or {}).get("underlying") or (asset.metadata or {}).get("underlying_id")
            if not underlying_id and result and getattr(result, "events", None):
                for event in result.events:
                    metadata = getattr(event, "metadata", None) or {}
                    if metadata.get("underlying"):
                        underlying_id = metadata.get("underlying")
                        break
            next_obs_event = None
            next_obs = None
            if result and getattr(result, "events", None):
                next_obs, next_obs_event = runtime._next_observation_event(result.events, today)
            redemption_trigger_level = None
            if next_obs_event is not None:
                metadata = getattr(next_obs_event, "metadata", None) or {}
                strike_val = None
                initial_level = (asset.metadata or {}).get("initial_level")
                if initial_level is not None:
                    try:
                        strike_val = float(initial_level)
                    except Exception:
                        strike_val = None
                pct = metadata.get("autocall_threshold_pct_of_initial")
                if pct is None:
                    pct = metadata.get("autocall_barrier_pct_of_initial")
                if pct is not None and strike_val is not None:
                    try:
                        redemption_trigger_level = float(strike_val) * float(pct) / 100.0
                    except Exception:
                        redemption_trigger_level = None
                elif isinstance(metadata.get("autocall_condition"), str) and "initial" in metadata.get("autocall_condition", "").lower() and strike_val is not None:
                    redemption_trigger_level = strike_val

            structured_rows.append(
                {
                    "underlying_id": underlying_id,
                    "display_name": asset.name,
                    "contract_name": contract_name,
                    "subscription_date": subscription_date,
                    "current_value": current_value,
                    "position_id": position.position_id,
                    "redemption_trigger_level": redemption_trigger_level,
                    "redemption_trigger": (
                        (getattr(next_obs_event, "metadata", None) or {}).get("autocall_condition")
                        if next_obs_event is not None
                        else None
                    ),
                }
            )

    # Load underlyings config to know type (rate vs underlying)
    underlyings_cfg_path = market_data_dir / "underlyings.yaml"
    underlyings_type_map: dict[str, str] = {}
    if underlyings_cfg_path.exists():
        ucfg = _load_yaml(underlyings_cfg_path)
        for item in ucfg.get("underlyings") or []:
            if isinstance(item, dict) and item.get("underlying_id") and item.get("type"):
                underlyings_type_map[str(item["underlying_id"])] = str(item["type"])

    uc_assets = []
    for asset in runtime.portfolio.list_all_assets():
        if asset.asset_type.value not in {"uc_fund", "uc_illiquid"}:
            continue
        asset_rows = uc_rows_by_asset.get(asset.asset_id, [])
        if not asset_rows:
            continue
        path = market_data_dir / f"nav_{asset.asset_id}.yaml"
        latest_value = None
        earliest_date = None
        latest_date = None
        points_count = 0
        source = None
        source_url = None
        nav_cfg = nav_sources_lookup.get(asset.asset_id) or {}
        if path.exists():
            payload = _load_yaml(path)
            history = list(payload.get("nav_history") or [])
            points_count = len(history)
            if history:
                earliest_date = history[0]["date"]
                last = history[-1]
                latest_value = float(last["value"])
                latest_date = last["date"]
                source = last.get("source")
            source_url = payload.get("source_url") or None
        if not source_url:
            source_url = nav_cfg.get("source_url") or None
        if not source_url and nav_cfg.get("morningstar_secid"):
            source_url = _morningstar_url(nav_cfg.get("morningstar_secid"))
        linked_contracts = sorted({str(row.get("contract_name") or "Sans contrat") for row in asset_rows})
        purchase_dates = sorted({str(row.get("subscription_date")) for row in asset_rows if row.get("subscription_date")})
        holding_amount = sum(float(row.get("current_value") or 0.0) for row in asset_rows)
        quantalys_info = quantalys_lookup.get(asset.isin or "")
        uc_assets.append(
            {
                "asset_id": asset.asset_id,
                "name": asset.name,
                "isin": asset.isin,
                "earliest_date": earliest_date,
                "latest_value": latest_value,
                "latest_date": latest_date,
                "points_count": points_count,
                "source": source,
                "source_url": source_url,
                "has_series": path.exists(),
                "linked_contracts": linked_contracts,
                "purchase_dates": purchase_dates,
                "holding_amount": holding_amount,
                "holdings": [
                    {
                        "position_id": row.get("position_id"),
                        "contract_name": row.get("contract_name"),
                        "subscription_date": row.get("subscription_date"),
                        "current_value": float(row.get("current_value") or 0.0),
                        "invested_amount": float(row.get("invested_amount") or 0.0),
                        "support_url": f"/supports/{row.get('position_id')}" if row.get("position_id") else None,
                    }
                    for row in asset_rows
                ],
                "quantalys_rating": quantalys_info.get("rating") if quantalys_info else None,
                "quantalys_category": quantalys_info.get("category") if quantalys_info else None,
                "quantalys_last_update": quantalys_info.get("last_update") if quantalys_info else None,
                "quantalys_notes": quantalys_info.get("notes") if quantalys_info else None,
                "quantalys_search_url": (
                    f"https://www.google.com/search?q=site%3Aquantalys.com+{asset.isin}"
                    if asset.isin
                    else None
                ),
            }
        )
    uc_assets.sort(key=lambda row: row["name"])

    underlying_map: dict[str, dict[str, Any]] = {}
    for row in structured_rows:
        underlying_id = row.get("underlying_id")
        if not underlying_id:
            continue
        safe_identifier = str(underlying_id).replace(":", "_").replace("/", "_")
        kind_for_id = underlyings_type_map.get(str(underlying_id), "underlying")
        if kind_for_id == "rate":
            path = market_data_dir / f"rates_{safe_identifier}.yaml"
        else:
            path = market_data_dir / f"underlying_{safe_identifier}.yaml"
        entry = underlying_map.setdefault(
            str(underlying_id),
            {
                "underlying_id": underlying_id,
                "kind": "rate" if kind_for_id == "rate" else "underlying",
                "products": [],
                "product_names": [],
                "earliest_date": None,
                "latest_value": None,
                "latest_date": None,
                "points_count": 0,
                "source": None,
                "url": None,
                "has_series": path.exists(),
                "linked_contracts": [],
                "purchase_dates": [],
                "holding_amount": 0.0,
                "holdings": [],
                "redemption_levels": [],
            },
        )
        entry["products"].append(row["display_name"])
        if row["display_name"] not in entry["product_names"]:
            entry["product_names"].append(row["display_name"])
        contract_name = str(row.get("contract_name") or "Sans contrat")
        if contract_name not in entry["linked_contracts"]:
            entry["linked_contracts"].append(contract_name)
        subscription_date = row.get("subscription_date")
        if subscription_date and subscription_date not in entry["purchase_dates"]:
            entry["purchase_dates"].append(subscription_date)
        entry["holding_amount"] += float(row.get("current_value") or 0.0)
        entry["holdings"].append(
            {
                "position_id": row.get("position_id"),
                "contract_name": contract_name,
                "subscription_date": subscription_date,
                "current_value": float(row.get("current_value") or 0.0),
                "product_name": row.get("display_name"),
                "support_url": f"/supports/{row.get('position_id')}" if row.get("position_id") else None,
            }
        )
        redemption_level = row.get("redemption_trigger_level")
        if redemption_level is not None:
            level_entry = {
                "product_name": row.get("display_name"),
                "level": float(redemption_level),
                "label": row.get("redemption_trigger"),
            }
            if level_entry not in entry["redemption_levels"]:
                entry["redemption_levels"].append(level_entry)
        if path.exists():
            payload = _load_yaml(path)
            history = list(payload.get("history") or [])
            entry["points_count"] = len(history)
            entry["source"] = payload.get("source")
            entry["url"] = payload.get("url") or None
            if history:
                entry["earliest_date"] = history[0]["date"]
                last = history[-1]
                entry["latest_value"] = float(last["value"])
                entry["latest_date"] = last["date"]
    for entry in underlying_map.values():
        entry["linked_contracts"].sort()
        entry["purchase_dates"].sort()
        entry["redemption_levels"].sort(key=lambda row: row["level"])
    underlyings = sorted(underlying_map.values(), key=lambda row: row["underlying_id"])

    return {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        },
        "summary": {
            "uc_count": len(uc_assets),
            "uc_with_series": sum(1 for row in uc_assets if row["has_series"]),
            "underlying_count": len(underlyings),
            "underlying_with_series": sum(1 for row in underlyings if row["has_series"]),
        },
        "uc_assets": uc_assets,
        "underlyings": underlyings,
    }
