"""
Import des mouvements Himalia (export texte copié/collé).

Objectif:
- Ajouter des lots sur les positions UC (par enveloppe HIMALIA) :
  - buy: versement/arbitrage (units > 0, net_amount > 0)
  - fee: frais de gestion (units < 0, net_amount < 0)
  - tax: taxes/prélèvements (units < 0, net_amount < 0)
  - income: distribution (units >= 0, net_amount > 0)

Le parsing est volontairement tolérant: on s'appuie surtout sur:
- la ligne "TYPE - dd/mm/yyyy"
- les blocs instrument: CODE (ISIN ou identifiant), Nom, puis Quantité/Cours/Montant net
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple


def _norm_line(s: str) -> str:
    return (s or "").strip()


def _parse_fr_number(raw: str) -> float:
    """
    Parse nombres FR:
      "1 733,945 €" -> 1733.945
      "-312,68 €" -> -312.68
      "25,975" -> 25.975
    """
    s = _norm_line(raw)
    s = s.replace("€", "").replace("\u00a0", " ").strip()
    s = re.sub(r"\s+", " ", s)
    s = s.replace(" ", "")  # remove thousands sep
    s = s.replace(",", ".")
    return float(s)


def _parse_ddmmyyyy(raw: str) -> date:
    return datetime.strptime(_norm_line(raw), "%d/%m/%Y").date()


_MOV_HDR_RE = re.compile(r"^(?P<label>.+?)\s*-\s*(?P<d>\d{2}/\d{2}/\d{4})$")
_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{10}$")
_CODE_RE = re.compile(r"^[A-Z0-9]{5,12}$")


@dataclass(frozen=True)
class MovementItem:
    code: str
    name: str
    units: Optional[float]
    nav: Optional[float]
    net_amount: Optional[float]


@dataclass(frozen=True)
class Movement:
    movement_date: date
    label: str
    kind: str  # buy|fee|tax|income|other
    items: List[MovementItem]


def classify_movement(label: str) -> str:
    l = (label or "").lower()
    if "frais de gestion" in l or l.startswith("frais"):
        return "fee"
    if "tax" in l or "prélèvements" in l or "prelevements" in l or "fiscal" in l:
        return "tax"
    if "distribution" in l or "revenus" in l:
        return "income"
    if "versement" in l or "arbitrage" in l or "ost" in l:
        return "buy"
    return "other"


def parse_himalia_text(text: str) -> List[Movement]:
    lines = [_norm_line(x) for x in (text or "").splitlines()]
    lines = [x for x in lines if x != ""]

    out: List[Movement] = []
    i = 0
    current: Optional[Movement] = None
    cur_items: List[MovementItem] = []

    def flush():
        nonlocal current, cur_items
        if current is not None:
            out.append(
                Movement(
                    movement_date=current.movement_date,
                    label=current.label,
                    kind=current.kind,
                    items=list(cur_items),
                )
            )
        current = None
        cur_items = []

    while i < len(lines):
        m = _MOV_HDR_RE.match(lines[i])
        if m:
            flush()
            d = _parse_ddmmyyyy(m.group("d"))
            label = m.group("label").strip()
            current = Movement(movement_date=d, label=label, kind=classify_movement(label), items=[])
            i += 1
            continue

        if current is None:
            i += 1
            continue

        # Instrument block starts with a code-like token
        code = lines[i]
        if not _CODE_RE.match(code):
            i += 1
            continue

        name = lines[i + 1] if i + 1 < len(lines) else ""
        i += 2

        units = None
        nav = None
        net_amount = None

        # scan until next movement header or next instrument code
        while i < len(lines):
            if _MOV_HDR_RE.match(lines[i]):
                break
            if _CODE_RE.match(lines[i]) and not lines[i].lower().startswith("cours") and not lines[i].lower().startswith("quantité"):
                # start of next instrument
                break

            if lines[i].lower() in {"quantité", "quantite"} and i + 1 < len(lines):
                try:
                    units = _parse_fr_number(lines[i + 1])
                except Exception:
                    pass
                i += 2
                continue

            if lines[i].lower() == "cours" and i + 1 < len(lines):
                try:
                    nav = _parse_fr_number(lines[i + 1])
                except Exception:
                    pass
                i += 2
                continue

            if lines[i].lower() == "montant net" and i + 1 < len(lines):
                try:
                    net_amount = _parse_fr_number(lines[i + 1])
                except Exception:
                    pass
                i += 2
                continue

            i += 1

        cur_items.append(MovementItem(code=code, name=name, units=units, nav=nav, net_amount=net_amount))

    flush()
    return out


def movement_summary(movements: List[Movement]) -> Dict[str, Any]:
    by_kind: Dict[str, int] = {}
    items = 0
    for mv in movements:
        by_kind[mv.kind] = by_kind.get(mv.kind, 0) + 1
        items += len(mv.items)
    return {"movements": len(movements), "items": items, "by_kind": by_kind}







