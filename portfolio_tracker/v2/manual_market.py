"""Saisie manuelle de données de marché et édition de l'URL source."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _save_yaml(path: Path, data: dict[str, Any]) -> None:
    path.write_text(
        yaml.dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )


def _resolve_path(market_data_dir: Path, kind: str, identifier: str) -> Path:
    safe_id = identifier.replace(":", "_").replace("/", "_")
    if kind == "uc":
        return market_data_dir / f"nav_{safe_id}.yaml"
    if kind == "rate":
        return market_data_dir / f"rates_{safe_id}.yaml"
    return market_data_dir / f"underlying_{safe_id}.yaml"


def _history_key(kind: str) -> str:
    return "nav_history" if kind == "uc" else "history"


def save_manual_market_point(
    data_dir: Path,
    kind: str,
    identifier: str,
    point_date: str,
    value: float,
) -> dict[str, Any]:
    """Enregistre un point manuel dans la série de marché (écrase si même date)."""
    data_dir = Path(data_dir)
    path = _resolve_path(data_dir / "market_data", kind, identifier)
    parsed_date = date.fromisoformat(point_date)
    payload = _load_yaml(path)
    key = _history_key(kind)
    history = list(payload.get(key) or [])
    history = [p for p in history if str(p.get("date")) != point_date]
    entry: dict[str, Any] = {"date": parsed_date.isoformat(), "value": float(value)}
    if kind == "uc":
        entry["source"] = "manual"
    history.append(entry)
    history.sort(key=lambda p: str(p.get("date")))
    payload[key] = history
    if kind != "uc":
        payload["last_updated"] = datetime.now().isoformat(timespec="seconds")
    _save_yaml(path, payload)
    return {"ok": True, "kind": kind, "identifier": identifier, "date": point_date, "value": value}


def save_market_source_url(
    data_dir: Path,
    kind: str,
    identifier: str,
    url: str,
) -> dict[str, Any]:
    """Met à jour l'URL source pour un actif de marché."""
    data_dir = Path(data_dir)
    market_data_dir = data_dir / "market_data"
    path = _resolve_path(market_data_dir, kind, identifier)
    payload = _load_yaml(path)
    url_key = "source_url" if kind == "uc" else "url"
    payload[url_key] = url
    _save_yaml(path, payload)

    if kind in ("underlying", "rate"):
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
