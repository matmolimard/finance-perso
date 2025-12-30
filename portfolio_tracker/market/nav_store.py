"""
NAV Store - lecture/écriture de l'historique des VL (nav_*.yaml)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml


@dataclass(frozen=True)
class NavPoint:
    point_date: date
    value: float
    currency: str = "EUR"
    source: Optional[str] = None


def _parse_date(d: Any) -> date:
    if isinstance(d, date) and not isinstance(d, datetime):
        return d
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, str):
        return datetime.fromisoformat(d).date()
    raise ValueError(f"Date invalide: {d!r}")


def load_nav_history(nav_file: Path) -> List[NavPoint]:
    """
    Charge l'historique de VL avec validation stricte.
    Les entrées invalides génèrent des warnings mais ne bloquent pas.
    """
    from ..validation import validate_nav_history_file
    from ..errors import PortfolioDataError
    
    validated_points, report = validate_nav_history_file(nav_file)
    
    if report.warnings:
        print(f"[WARNING] {nav_file.name} : {len(report.warnings)} entrée(s) ignorée(s)")
        for w in report.warnings[:5]:
            print(f"  {w}")
    
    if report.has_errors:
        error_summary = report.format_summary()
        raise PortfolioDataError(
            f"Fichier NAV corrompu: {nav_file.name}\n{error_summary}"
        )
    
    out = [
        NavPoint(
            point_date=p.point_date,
            value=float(p.value),
            currency=p.currency,
            source=p.source
        )
        for p in validated_points
    ]
    out.sort(key=lambda p: p.point_date)
    return out


def upsert_nav_point(
    *,
    market_data_dir: Path,
    identifier: str,
    point: NavPoint,
) -> Tuple[Path, bool]:
    """
    Upsert un point de VL avec validation AVANT écriture.

    Returns:
        (path, changed) où changed=True si le fichier a été modifié.
    """
    from ..validation import validate_nav_history_file
    import tempfile
    
    market_data_dir = Path(market_data_dir)
    market_data_dir.mkdir(parents=True, exist_ok=True)

    nav_file = market_data_dir / f"nav_{identifier}.yaml"
    history = load_nav_history(nav_file)

    changed = False
    replaced = False
    new_hist: List[NavPoint] = []
    for p in history:
        if p.point_date == point.point_date:
            new_hist.append(point)
            replaced = True
            if (p.value != point.value) or (p.currency != point.currency) or (p.source != point.source):
                changed = True
        else:
            new_hist.append(p)

    if not replaced:
        new_hist.append(point)
        changed = True

    new_hist.sort(key=lambda p: p.point_date)

    if not changed:
        return nav_file, False

    payload: Dict[str, Any] = {
        "nav_history": [
            {
                "date": p.point_date.isoformat(),
                "value": float(p.value),
                "currency": p.currency,
                **({"source": p.source} if p.source else {}),
            }
            for p in new_hist
        ]
    }
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False, encoding='utf-8') as tmp:
        tmp_path = Path(tmp.name)
        yaml.safe_dump(payload, tmp, sort_keys=False, allow_unicode=True)
    
    try:
        _, report = validate_nav_history_file(tmp_path)
        
        if report.has_errors:
            tmp_path.unlink()
            error_summary = report.format_summary()
            raise ValueError(f"Validation pré-sauvegarde NAV échouée : {nav_file.name}\n{error_summary}")
        
        tmp_path.replace(nav_file)
    except Exception as e:
        if tmp_path.exists():
            tmp_path.unlink()
        raise
    
    return nav_file, True


