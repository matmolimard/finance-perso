"""Providers de donnees de marche legers, propres a la V2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
import re

import yaml


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_nav_sources_cfg(market_data_dir: Path) -> dict[str, dict[str, Any]]:
    cfg_file = Path(market_data_dir) / "nav_sources.yaml"
    if not cfg_file.exists():
        return {}
    payload = _load_yaml(cfg_file)
    sources = payload.get("nav_sources") or {}
    return {str(key): value for key, value in sources.items() if isinstance(value, dict)}


class QuantalysProvider:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self._ratings_cache: dict[str, dict[str, Any]] | None = None

    def _load(self) -> dict[str, dict[str, Any]]:
        if self._ratings_cache is not None:
            return self._ratings_cache
        path = self.data_dir / "quantalys_ratings.yaml"
        if not path.exists():
            self._ratings_cache = {}
            return self._ratings_cache
        payload = _load_yaml(path)
        ratings: dict[str, dict[str, Any]] = {}
        for row in payload.get("ratings") or []:
            isin = row.get("isin")
            if not isin:
                continue
            ratings[str(isin)] = {
                "rating": row.get("quantalys_rating"),
                "category": row.get("quantalys_category"),
                "last_update": row.get("last_update"),
                "notes": row.get("notes"),
            }
        self._ratings_cache = ratings
        return ratings

    def get_rating_display(self, isin: str) -> str:
        info = self._load().get(isin)
        if not info:
            return "N/A"
        rating = info.get("rating")
        if rating is None:
            return "Non note"
        return f"{'⭐' * int(rating)} ({int(rating)}/5)"

    def get_rating(self, isin: str) -> dict[str, Any] | None:
        return self._load().get(isin)

    def upsert_rating(
        self,
        *,
        isin: str,
        name: str,
        rating: int | None,
        category: str | None = None,
        update_date: date | None = None,
    ) -> bool:
        update_date = update_date or datetime.now().date()
        path = self.data_dir / "quantalys_ratings.yaml"
        payload = _load_yaml(path) if path.exists() else {}
        ratings = list(payload.get("ratings") or [])

        new_entry = {
            "isin": isin,
            "name": name,
            "quantalys_rating": rating,
            "quantalys_category": category,
            "last_update": update_date.isoformat(),
            "notes": "Note globale Quantalys" if rating is not None else "Non note par Quantalys",
        }

        changed = False
        for entry in ratings:
            if entry.get("isin") != isin:
                continue
            if (
                entry.get("name") != name
                or entry.get("quantalys_rating") != rating
                or entry.get("quantalys_category") != category
            ):
                entry.update(new_entry)
                changed = True
            break
        else:
            ratings.append(new_entry)
            changed = True

        if not changed:
            return False

        ratings.sort(key=lambda row: str(row.get("isin") or ""))
        payload["ratings"] = ratings
        path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=100), encoding="utf-8")
        self._ratings_cache = None
        return True


def _sanitize_underlying_id(underlying_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", underlying_id)


@dataclass(frozen=True)
class UnderlyingPoint:
    point_date: date
    value: float


class UnderlyingProvider:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)

    def _file_path(self, underlying_id: str) -> Path:
        return self.data_dir / f"underlying_{_sanitize_underlying_id(underlying_id)}.yaml"

    def get_data(self, identifier: str, data_type: str = "underlying", target_date: date | None = None) -> dict[str, Any] | None:
        target_date = target_date or datetime.now().date()
        path = self._file_path(identifier)
        if not path.exists():
            return None
        payload = _load_yaml(path)
        candidates = []
        for row in payload.get("history") or []:
            try:
                point_date = datetime.fromisoformat(str(row["date"])).date()
                if point_date <= target_date:
                    candidates.append((point_date, float(row["value"])))
            except Exception:
                continue
        if not candidates:
            return None
        point_date, value = max(candidates, key=lambda item: item[0])
        return {"date": point_date, "value": value, "source": payload.get("source")}

    def get_history(self, identifier: str, start_date: date | None = None, end_date: date | None = None) -> list[UnderlyingPoint]:
        path = self._file_path(identifier)
        if not path.exists():
            return []
        payload = _load_yaml(path)
        points: list[UnderlyingPoint] = []
        for row in payload.get("history") or []:
            try:
                point_date = datetime.fromisoformat(str(row["date"])).date()
                if start_date and point_date < start_date:
                    continue
                if end_date and point_date > end_date:
                    continue
                points.append(UnderlyingPoint(point_date=point_date, value=float(row["value"])))
            except Exception:
                continue
        return sorted(points, key=lambda point: point.point_date)

    def upsert_history(
        self,
        *,
        underlying_id: str,
        source: str,
        identifier: str,
        points: list[tuple[date, float]],
        extra: dict[str, Any] | None = None,
    ) -> int:
        path = self._file_path(underlying_id)
        payload = _load_yaml(path) if path.exists() else {}
        existing: dict[str, dict[str, Any]] = {}
        for row in payload.get("history") or []:
            if not isinstance(row, dict) or not row.get("date"):
                continue
            existing[str(row["date"])] = row

        changed = 0
        for point_date, value in points:
            key = point_date.isoformat()
            row = {"date": key, "value": float(value)}
            previous = existing.get(key)
            if previous is None or float(previous.get("value") or 0.0) != float(value):
                existing[key] = row
                changed += 1

        payload = {
            "underlying_id": underlying_id,
            "source": source,
            "identifier": identifier,
            "last_updated": datetime.now().isoformat(timespec="seconds"),
            "history": [existing[key] for key in sorted(existing)],
        }
        if extra:
            payload.update(extra)
        path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=100), encoding="utf-8")
        return changed


class RatesProvider:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)

    def get_data(self, identifier: str, data_type: str = "rate", target_date: date | None = None) -> dict[str, Any] | None:
        target_date = target_date or datetime.now().date()
        path = self.data_dir / f"rates_{identifier}.yaml"
        if not path.exists():
            return None
        payload = _load_yaml(path)
        candidates = []
        for row in payload.get("history") or []:
            try:
                point_date = datetime.fromisoformat(str(row["date"])).date()
                if point_date <= target_date:
                    candidates.append((point_date, float(row["value"])))
            except Exception:
                continue
        if not candidates:
            return None
        point_date, value = max(candidates, key=lambda item: item[0])
        return {"date": point_date, "value": value}

    def upsert_history(
        self,
        *,
        identifier: str,
        source: str,
        points: list[tuple[date, float]],
        extra: dict[str, Any] | None = None,
    ) -> int:
        path = self.data_dir / f"rates_{identifier}.yaml"
        payload = _load_yaml(path) if path.exists() else {}
        existing: dict[str, dict[str, Any]] = {}
        for row in payload.get("history") or []:
            if not isinstance(row, dict) or not row.get("date"):
                continue
            existing[str(row["date"])] = row

        changed = 0
        for point_date, value in points:
            key = point_date.isoformat()
            row = {"date": key, "value": float(value)}
            previous = existing.get(key)
            if previous is None or float(previous.get("value") or 0.0) != float(value):
                existing[key] = row
                changed += 1

        payload = {
            "identifier": identifier,
            "source": source,
            "units": payload.get("units", "pct"),
            "last_updated": datetime.now().isoformat(timespec="seconds"),
            "history": [existing[key] for key in sorted(existing)],
        }
        if extra:
            payload.update(extra)
        path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=100), encoding="utf-8")
        return changed
