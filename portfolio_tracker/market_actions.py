"""Actions de marché V2 sans dépendance au CLI historique."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import yaml

from .bootstrap import ensure_v2_db
from .market_sync import (
    backfill_uc_navs,
    fetch_ecb_irs_rate,
    fetch_euronext_recent_history,
    fetch_investing_rate,
    fetch_merqube_indexhistory,
    fetch_natixis_index,
    fetch_solactive_indexhistory,
    update_uc_navs,
)
from .runtime import V2Runtime


def update_v2_uc_navs(
    data_dir: Path,
    *,
    target_date: Optional[str] = None,
    set_values: Optional[list[str]] = None,
    headless: bool = False,
    include_historical: bool = False,
    asset_ids: Optional[list[str]] = None,
) -> dict[str, Any]:
    ensure_v2_db(Path(data_dir))
    runtime = V2Runtime(Path(data_dir))
    if target_date:
        target = datetime.fromisoformat(str(target_date)).date()
    else:
        target = datetime.now().date()

    results, positions_changed = update_uc_navs(
        portfolio=runtime.portfolio,
        market_data_dir=runtime.market_data_dir,
        target_date=target,
        set_values=set_values,
        headless=headless,
        include_historical=include_historical,
        asset_ids={str(asset_id) for asset_id in (asset_ids or [])},
    )
    ok = [r for r in results if r.status == "ok"]
    skipped = [r for r in results if r.status == "skipped"]
    errors = [r for r in results if r.status == "error"]
    return {
        "ok": len(errors) == 0,
        "target_date": target.isoformat(),
        "positions_changed": positions_changed,
        "summary": {"ok": len(ok), "skipped": len(skipped), "errors": len(errors)},
        "results": [
            {
                "asset_id": row.asset_id,
                "target_date": row.target_date.isoformat(),
                "status": row.status,
                "message": row.message,
                "changed": row.changed,
            }
            for row in results
        ],
    }


def update_v2_underlyings(
    data_dir: Path,
    *,
    headless: bool = False,
    years: Optional[int] = None,
) -> dict[str, Any]:
    ensure_v2_db(Path(data_dir))
    runtime = V2Runtime(Path(data_dir))
    cfg_file = runtime.market_data_dir / "underlyings.yaml"
    if not cfg_file.exists():
        return {"ok": False, "error": f"fichier de config introuvable: {cfg_file}"}

    cfg = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) or {}
    underlyings = cfg.get("underlyings") or []
    if not underlyings:
        return {"ok": True, "summary": {"changed": 0, "processed": 0}, "results": []}

    start_date = None
    if years is not None and int(years) > 0:
        start_date = date.today() - timedelta(days=365 * int(years))

    total_changed = 0
    results: list[dict[str, Any]] = []
    for item in underlyings:
        if not isinstance(item, dict):
            continue
        underlying_id = item.get("underlying_id")
        source = item.get("source")
        identifier = item.get("identifier") or underlying_id
        url = item.get("url")
        if not underlying_id or not source:
            continue

        try:
            if source == "solactive":
                if not url:
                    raise ValueError("url manquante")
                res = fetch_solactive_indexhistory(url=url, identifier=str(identifier), headless=headless)
                points = [p for p in res.points if not start_date or p[0] >= start_date]
                changed = runtime.underlyings_provider.upsert_history(
                    underlying_id=str(underlying_id),
                    source=res.source,
                    identifier=str(identifier),
                    points=points,
                    extra={"url": url, "notes": item.get("notes")},
                )
            elif source == "euronext":
                res = fetch_euronext_recent_history(identifier=str(identifier), headless=headless)
                points = [p for p in res.points if not start_date or p[0] >= start_date]
                changed = runtime.underlyings_provider.upsert_history(
                    underlying_id=str(underlying_id),
                    source=res.source,
                    identifier=str(identifier),
                    points=points,
                    extra={"url": item.get("url"), "notes": item.get("notes")},
                )
            elif source == "merqube":
                metric = item.get("metric") or "total_return"
                res = fetch_merqube_indexhistory(
                    name=str(identifier),
                    metric=str(metric),
                    start_date=start_date,
                    headless=headless,
                )
                points = [p for p in res.points if not start_date or p[0] >= start_date]
                changed = runtime.underlyings_provider.upsert_history(
                    underlying_id=str(underlying_id),
                    source=res.source,
                    identifier=str(identifier),
                    points=points,
                    extra={"url": item.get("url") or res.metadata.get("source_page"), "notes": item.get("notes"), "metric": metric},
                )
            elif source == "natixis":
                if not url:
                    raise ValueError("url manquante")
                res = fetch_natixis_index(url=url, identifier=str(identifier), headless=headless)
                points = [p for p in res.points if not start_date or p[0] >= start_date]
                changed = runtime.underlyings_provider.upsert_history(
                    underlying_id=str(underlying_id),
                    source=res.source,
                    identifier=str(identifier),
                    points=points,
                    extra={"url": url, "notes": item.get("notes")},
                )
            elif source in ("investing", "ecb") and item.get("type") == "rate":
                # Essaie d'abord l'API ECB (fiable, sans scraping), puis fallback Investing.com
                try:
                    res = fetch_ecb_irs_rate(identifier=str(identifier), start_date=start_date)
                except Exception:
                    if not url:
                        raise ValueError("url manquante et ECB indisponible")
                    res = fetch_investing_rate(url=url, identifier=str(identifier), headless=headless)
                points = [p for p in res.points if not start_date or p[0] >= start_date]
                changed = runtime.rates_provider.upsert_history(
                    identifier=str(identifier),
                    source=res.source,
                    points=points,
                    extra={"url": url, "notes": item.get("notes")},
                )
            else:
                results.append(
                    {
                        "underlying_id": underlying_id,
                        "status": "skipped",
                        "message": f"source non supportée ({source})",
                        "changed": 0,
                    }
                )
                continue

            total_changed += changed
            if source in ("investing", "ecb") and item.get("type") == "rate":
                latest = runtime.rates_provider.get_data(str(identifier), "rate", None)
            else:
                latest = runtime.underlyings_provider.get_data(str(underlying_id), "underlying", None)
            latest_str = f"{latest['date'].isoformat()} -> {latest['value']}" if latest else "(aucune donnée)"
            results.append(
                {
                    "underlying_id": underlying_id,
                    "status": "ok",
                    "message": latest_str,
                    "changed": changed,
                }
            )
        except Exception as exc:
            results.append(
                {
                    "underlying_id": underlying_id,
                    "status": "error",
                    "message": str(exc),
                    "changed": 0,
                }
            )

    errors = [row for row in results if row["status"] == "error"]
    return {
        "ok": len(errors) == 0,
        "summary": {"changed": total_changed, "processed": len(results), "errors": len(errors)},
        "results": results,
    }


def backfill_v2_market_history(
    data_dir: Path,
    *,
    years: int | None = None,
    headless: bool = False,
    include_historical: bool = False,
    asset_ids: Optional[list[str]] = None,
) -> dict[str, Any]:
    ensure_v2_db(Path(data_dir))
    runtime = V2Runtime(Path(data_dir))
    end_date = datetime.now().date()
    if years is not None and int(years) <= 0:
        raise ValueError("--years doit être > 0")
    effective_years = int(years) if years is not None else None
    start_date = end_date - timedelta(days=365 * effective_years) if effective_years is not None else None
    uc_results = backfill_uc_navs(
        portfolio=runtime.portfolio,
        market_data_dir=runtime.market_data_dir,
        start_date=start_date,
        end_date=end_date,
        headless=headless,
        include_historical=include_historical,
        asset_ids={str(asset_id) for asset_id in (asset_ids or [])},
    )
    uc_ok = [r for r in uc_results if r.status == "ok"]
    uc_skipped = [r for r in uc_results if r.status == "skipped"]
    uc_errors = [r for r in uc_results if r.status == "error"]
    underlyings_result = update_v2_underlyings(data_dir, headless=headless, years=effective_years)
    return {
        "ok": len(uc_errors) == 0 and underlyings_result.get("ok", False),
        "period": {
            "start_date": start_date.isoformat() if start_date is not None else None,
            "end_date": end_date.isoformat(),
            "years": effective_years,
            "mode": "full" if effective_years is None else "window",
        },
        "uc_summary": {"ok": len(uc_ok), "skipped": len(uc_skipped), "errors": len(uc_errors)},
        "uc_results": [
            {
                "asset_id": row.asset_id,
                "status": row.status,
                "message": row.message,
                "points_fetched": row.points_fetched,
                "points_changed": row.points_changed,
            }
            for row in uc_results
        ],
        "underlyings": underlyings_result,
    }
