"""Saisie manuelle de données de marché et édition de l'URL source."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .storage import default_db_path, upsert_market_series_metadata, upsert_market_series_points


def save_manual_market_point(
    data_dir: Path,
    kind: str,
    identifier: str,
    point_date: str,
    value: float,
) -> dict[str, Any]:
    """Enregistre un point manuel dans la série de marché (écrase si même date)."""
    data_dir = Path(data_dir)
    db_kind = "uc" if kind == "uc" else ("rate" if kind == "rate" else "underlying")
    upsert_market_series_points(
        default_db_path(data_dir),
        kind=db_kind,
        identifier=identifier,
        points=[
            {
                "date": point_date,
                "value": float(value),
                "currency": "EUR" if kind == "uc" else None,
                "source": "manual",
            }
        ],
        source="manual",
        currency="EUR" if kind == "uc" else None,
    )
    return {"ok": True, "kind": kind, "identifier": identifier, "date": point_date, "value": value}


def save_market_source_url(
    data_dir: Path,
    kind: str,
    identifier: str,
    url: str,
) -> dict[str, Any]:
    """Met à jour l'URL source pour un actif de marché."""
    data_dir = Path(data_dir)
    db_kind = "uc" if kind == "uc" else ("rate" if kind == "rate" else "underlying")
    upsert_market_series_metadata(
        default_db_path(data_dir),
        kind=db_kind,
        identifier=identifier,
        source_url=url,
    )

    if kind in ("underlying", "rate"):
        market_data_dir = data_dir / "market_data"
        underlyings_cfg = market_data_dir / "underlyings.yaml"
        if underlyings_cfg.exists():
            cfg = yaml.safe_load(underlyings_cfg.read_text(encoding="utf-8")) or {}
            for item in cfg.get("underlyings") or []:
                if isinstance(item, dict) and str(item.get("underlying_id")) == identifier:
                    item["url"] = url
                    break
            underlyings_cfg.write_text(
                yaml.dump(cfg, allow_unicode=True, sort_keys=False, default_flow_style=False),
                encoding="utf-8",
            )

    return {"ok": True, "kind": kind, "identifier": identifier, "url": url}
