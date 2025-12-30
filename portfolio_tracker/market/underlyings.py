"""
Underlyings - Séries temporelles des sous-jacents (indices, taux, etc.)

Objectif:
- stocker un historique (date -> valeur) par sous-jacent dans market_data/
- fournir un accès homogène pour consulter une valeur à une date cible
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional, Dict, Any, List, Iterable, Tuple
import re
import yaml

from .providers import MarketDataProvider


def _sanitize_underlying_id(underlying_id: str) -> str:
    # safe filename: keep alnum, '-', '_' only; replace others with '_'
    return re.sub(r"[^A-Za-z0-9_-]+", "_", underlying_id)


@dataclass(frozen=True)
class UnderlyingPoint:
    point_date: date
    value: float


class UnderlyingProvider(MarketDataProvider):
    """
    Provider de séries temporelles de sous-jacents.

    Fichier attendu:
      underlying_<underlying_id_sanitized>.yaml
    """

    def _file_path(self, underlying_id: str) -> Path:
        return self.data_dir / f"underlying_{_sanitize_underlying_id(underlying_id)}.yaml"

    def get_data(
        self,
        identifier: str,
        data_type: str = "underlying",
        target_date: Optional[date] = None
    ) -> Optional[Dict[str, Any]]:
        if target_date is None:
            target_date = datetime.now().date()

        f = self._file_path(identifier)
        if not f.exists():
            return None

        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        history = data.get("history") or []
        if not isinstance(history, list) or not history:
            return None

        candidates = []
        for row in history:
            if not isinstance(row, dict):
                continue
            ds = row.get("date")
            if not ds:
                continue
            try:
                d = datetime.fromisoformat(str(ds)).date()
            except Exception:
                continue
            if d <= target_date:
                candidates.append((d, row))

        if not candidates:
            return None

        d, row = max(candidates, key=lambda x: x[0])
        return {
            "date": d,
            "value": float(row["value"]),
            "source": data.get("source"),
            "identifier": data.get("identifier"),
        }

    def is_data_available(self, identifier: str, data_type: str = "underlying") -> bool:
        return self._file_path(identifier).exists()

    def get_latest_date(self, identifier: str, data_type: str = "underlying") -> Optional[date]:
        d = self.get_data(identifier, data_type, date.max)
        return d["date"] if d else None

    def get_history(
        self,
        identifier: str,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None
    ) -> List[UnderlyingPoint]:
        f = self._file_path(identifier)
        if not f.exists():
            return []
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        history = data.get("history") or []
        out: List[UnderlyingPoint] = []
        for row in history:
            if not isinstance(row, dict):
                continue
            ds = row.get("date")
            if not ds:
                continue
            try:
                d = datetime.fromisoformat(str(ds)).date()
            except Exception:
                continue
            if start_date and d < start_date:
                continue
            if end_date and d > end_date:
                continue
            try:
                v = float(row["value"])
            except Exception:
                continue
            out.append(UnderlyingPoint(point_date=d, value=v))
        return sorted(out, key=lambda p: p.point_date)

    def upsert_history(
        self,
        underlying_id: str,
        *,
        source: str,
        identifier: str,
        points: Iterable[Tuple[date, float]],
        extra: Optional[Dict[str, Any]] = None
    ) -> int:
        """
        Upsert des points (date, value) dans le fichier underlying_<id>.yaml.
        Retourne le nombre de points ajoutés/modifiés.
        """
        f = self._file_path(underlying_id)
        data = {}
        if f.exists():
            data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}

        existing = {}
        for row in (data.get("history") or []):
            if not isinstance(row, dict):
                continue
            ds = row.get("date")
            if not ds:
                continue
            existing[str(ds)] = row

        changed = 0
        for d, v in points:
            key = d.isoformat()
            row = {"date": key, "value": float(v)}
            if key not in existing or float(existing[key].get("value")) != float(v):
                existing[key] = row
                changed += 1

        merged_history = sorted(existing.values(), key=lambda r: r["date"])

        out = {
            "underlying_id": underlying_id,
            "source": source,
            "identifier": identifier,
            "last_updated": datetime.now().isoformat(timespec="seconds"),
            "history": merged_history,
        }
        if extra:
            out.update(extra)

        f.write_text(
            yaml.safe_dump(out, sort_keys=False, allow_unicode=True, width=100),
            encoding="utf-8"
        )
        return changed


