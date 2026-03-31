"""Extraction et application des arbitrages depuis les documents GED."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any
import unicodedata
from uuid import uuid4

from .bootstrap import ensure_v2_db, refresh_v2_derived_state
from .document_ingest import extract_pdf_text
from .runtime import V2Runtime
from .storage import connect, default_db_path


def _timestamp() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _parse_fr_number(raw: str) -> float | None:
    text = str(raw or "").strip()
    if not text:
        return None
    text = text.replace("\xa0", " ").replace("€", "").replace("Euros", "").strip()
    text = re.sub(r"\s+", "", text)
    text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def _parse_ddmmyyyy(raw: str) -> str | None:
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", str(raw or ""))
    if not m:
        return None
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"


def _extract_leg_amounts(block: str) -> tuple[float | None, float | None, float | None]:
    # SwissLife pattern: "<amount>€ <units> <nav> <date>"
    amt = r"([0-9]{1,3}(?:[ \xa0][0-9]{3})*,\d{2}|[0-9]+,\d{2})"
    qty = r"([0-9]{1,3}(?:[ \xa0][0-9]{3})*,\d{2,6}|[0-9]+,\d{2,6})"
    detailed = re.search(
        rf"(?<![0-9]){amt}\s*€?\s+{qty}\s+{qty}\s+\d{{2}}/\d{{2}}/\d{{4}}",
        block,
        re.IGNORECASE,
    )
    if detailed:
        return (
            _parse_fr_number(detailed.group(1)),
            _parse_fr_number(detailed.group(2)),
            _parse_fr_number(detailed.group(3)),
        )
    amount_only = re.search(r"([0-9 \xa0]+,\d{2})\s*€", block)
    if amount_only:
        return (_parse_fr_number(amount_only.group(1)), None, None)
    return (None, None, None)


def _strip_swisslife_leg_name(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    text = re.sub(
        r"\s+[0-9][0-9 \xa0]*,\d{2}\s*€.*$",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\s+", " ", text).strip(" -")
    return text


def _parse_swisslife_fonds_euro_legs(lines: list[str], start_idx: int, end_idx: int, *, direction: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for idx in range(start_idx, end_idx):
        line = str(lines[idx] or "").strip()
        match = re.match(r"^(?:Euros|Fonds\s*en\s*Euros?)\s+([0-9][0-9 \xa0]*,\d{2})\s*€?$", line, re.IGNORECASE)
        if match is None:
            continue
        out.append(
            {
                "isin": "SW0000000000",
                "name": "Fonds Euros Swisslife",
                "units": None,
                "nav": None,
                "amount": _parse_fr_number(match.group(1)),
                "direction": direction,
            }
        )
    return out


def _parse_swisslife_section(lines: list[str], start_idx: int, end_idx: int, *, direction: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    i = start_idx
    while i < end_idx:
        line = lines[i]
        isin_match = re.search(r"\b([A-Z]{2}[A-Z0-9]{10})\s*:\s*(.*)$", line)
        if not isin_match:
            i += 1
            continue
        isin = isin_match.group(1)
        name_parts = [_strip_swisslife_leg_name(isin_match.group(2))]
        metrics_block = line

        j = i + 1
        while j < end_idx:
            next_line = lines[j]
            if re.search(r"\b[A-Z]{2}[A-Z0-9]{10}\s*:", next_line):
                break
            low = next_line.lower()
            if any(
                marker in low
                for marker in [
                    "montantàréinvestir",
                    "montantréinvesti",
                    "une situation actualisée",
                    "situation de votre épargne",
                    "désinvestissement",
                    "investissement",
                ]
            ):
                break
            metrics_block += " " + next_line
            if not re.search(r"\d{2}/\d{2}/\d{4}", next_line):
                if not any(token in low for token in ["supports", "montant", "nombrede", "valeur", "datede"]):
                    name_parts.append(_strip_swisslife_leg_name(next_line))
            j += 1

        amount, units, nav = _extract_leg_amounts(metrics_block)
        name = " ".join(part for part in name_parts if part).strip()
        out.append(
            {
                "isin": isin,
                "name": name or None,
                "units": units,
                "nav": nav,
                "amount": amount,
                "direction": direction,
            }
        )
        i = j
    return out


def parse_arbitration_text(text: str, *, fallback_date: str | None = None) -> dict[str, Any]:
    """Parse un courrier d'arbitrage Generali en proposition structurée."""
    raw_lines = [str(line).strip() for line in (text or "").splitlines()]
    lines = [line for line in raw_lines if line]

    # Focus on the first arbitration block and ignore subsequent recap pages.
    start_idx = 0
    for idx, line in enumerate(lines):
        if "arbitrage d" in line.lower() and "montant" in line.lower():
            start_idx = max(0, idx - 12)
            break
    end_idx = len(lines)
    for idx in range(start_idx, len(lines)):
        low = lines[idx].lower()
        if "objet : valeur atteinte après arbitrage" in low or "objet : valeur atteinte apres arbitrage" in low:
            end_idx = idx
            break
        if low.startswith("-- 2 of"):
            end_idx = idx
            break
    lines = lines[start_idx:end_idx]

    header_blob = "\n".join(lines[:120])
    date_match = re.search(
        r"Arbitrage d['’]un montant de\s*:\s*([0-9 \xa0]+,\d{2})\s*Euros\s*en date du\s*(\d{2}/\d{2}/\d{4})",
        header_blob,
        re.IGNORECASE,
    )
    amount = _parse_fr_number(date_match.group(1)) if date_match else None
    effective_date = _parse_ddmmyyyy(date_match.group(2)) if date_match else None
    if not effective_date:
        effect_match = re.search(
            r"Date d['’]effet de l['’]opération\s*:\s*(\d{2}/\d{2}/\d{4})",
            header_blob,
            re.IGNORECASE,
        )
        effective_date = _parse_ddmmyyyy(effect_match.group(1)) if effect_match else None

    legs_from: list[dict[str, Any]] = []
    legs_to: list[dict[str, Any]] = []
    mode: str | None = None

    i = 0
    while i < len(lines):
        line = lines[i]
        low = line.lower()
        if "désinvestissement" in low or "desinvestissement" in low:
            mode = "from"
            i += 1
            continue
        if "réinvestissement" in low or "reinvestissement" in low:
            mode = "to"
            i += 1
            continue

        isin_match = re.search(r"ISIN\s*:\s*([A-Z]{2}[A-Z0-9]{10})", line)
        if isin_match and mode in {"from", "to"}:
            isin = isin_match.group(1)
            name = re.sub(r"\(ISIN\s*:.*\)", "", line, flags=re.IGNORECASE).strip(" -\t")

            units = None
            nav = None
            leg_amount = None
            for j in range(i + 1, min(i + 8, len(lines))):
                candidate = lines[j]
                parts_nav_amount = re.search(
                    r"([0-9 \xa0]+,\d{2,4})\s*Parts\s*à\s*([0-9 \xa0]+,\d{2})\s*Euros.*soit\s*([0-9 \xa0]+,\d{2})",
                    candidate,
                    re.IGNORECASE,
                )
                if parts_nav_amount:
                    units = _parse_fr_number(parts_nav_amount.group(1))
                    nav = _parse_fr_number(parts_nav_amount.group(2))
                    leg_amount = _parse_fr_number(parts_nav_amount.group(3))
                    break
                amt_only = re.search(r"soit\s*([0-9 \xa0]+,\d{2})", candidate, re.IGNORECASE)
                if amt_only:
                    leg_amount = _parse_fr_number(amt_only.group(1))
                    break

            leg = {
                "isin": isin,
                "name": name or None,
                "units": units,
                "nav": nav,
                "amount": leg_amount,
                "direction": mode,
            }
            if mode == "from":
                legs_from.append(leg)
            else:
                legs_to.append(leg)
        i += 1

    if not legs_from and not legs_to:
        # SwissLife layout fallback.
        from_start = next(
            (
                idx
                for idx, ln in enumerate(lines)
                if re.search(r"^(désinvestissement|desinvestissement)\s*:?", ln.strip(), re.IGNORECASE)
            ),
            None,
        )
        to_start = next(
            (
                idx
                for idx, ln in enumerate(lines)
                if re.search(r"^(réinvestissement|reinvestissement|investissement)\s*:?", ln.strip(), re.IGNORECASE)
            ),
            None,
        )
        if from_start is not None and to_start is not None and from_start < to_start:
            from_end = to_start
            to_end = len(lines)
            for idx in range(to_start + 1, len(lines)):
                low = lines[idx].lower()
                if "situation de votre épargne" in low or "une situation actualisée" in low:
                    to_end = idx
                    break
            legs_from = _parse_swisslife_section(lines, from_start + 1, from_end, direction="from")
            legs_from.extend(_parse_swisslife_fonds_euro_legs(lines, from_start + 1, from_end, direction="from"))
            legs_to = _parse_swisslife_section(lines, to_start + 1, to_end, direction="to")
            legs_to.extend(_parse_swisslife_fonds_euro_legs(lines, to_start + 1, to_end, direction="to"))
            if amount is None:
                amount = sum(float(leg.get("amount") or 0.0) for leg in legs_to) or None

    return {
        "effective_date": effective_date or fallback_date,
        "fallback_date": fallback_date,
        "amount": amount,
        "from_legs": legs_from,
        "to_legs": legs_to,
    }


def _ensure_arbitration_tables(db_path: Path) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS document_arbitration_proposals (
                document_id TEXT PRIMARY KEY,
                proposal_json TEXT NOT NULL,
                extraction_status TEXT NOT NULL,
                application_status TEXT NOT NULL,
                notes TEXT,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (document_id) REFERENCES documents(document_id)
            )
            """
        )


def _map_legs_to_positions(
    *,
    data_dir: Path,
    contract_name: str | None,
    proposal: dict[str, Any],
    db_path: Path | None = None,
) -> dict[str, Any]:
    runtime = V2Runtime(data_dir, db_path=db_path)
    by_isin: dict[str, dict[str, Any]] = {}
    candidates: list[dict[str, Any]] = []
    for position in runtime.portfolio.list_all_positions():
        if contract_name and str(position.wrapper.contract_name or "") != str(contract_name):
            continue
        asset = runtime.portfolio.get_asset(position.asset_id)
        if asset is None or not asset.isin:
            if asset is None:
                continue
            row = {
                "position_id": position.position_id,
                "asset_id": asset.asset_id,
                "asset_name": asset.name,
                "asset_isin": asset.isin,
            }
            candidates.append(row)
            continue
        row = {
            "position_id": position.position_id,
            "asset_id": asset.asset_id,
            "asset_name": asset.name,
            "asset_isin": asset.isin,
        }
        by_isin[str(asset.isin)] = row
        candidates.append(row)

    def normalize_tokens(text: str) -> list[str]:
        normalized = unicodedata.normalize("NFKD", text or "")
        normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
        normalized = normalized.lower()
        normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
        stop_words = {
            "emtn",
            "compartiment",
            "sicav",
            "fcp",
            "d",
            "de",
            "du",
            "des",
            "la",
            "le",
            "les",
            "eu",
            "eur",
            "act",
            "part",
            "parts",
            "for",
        }
        tokens = [token for token in normalized.split() if token and token not in stop_words]
        return tokens

    def suggest_by_name(leg: dict[str, Any]) -> dict[str, Any] | None:
        leg_name = str(leg.get("name") or "")
        leg_tokens = set(normalize_tokens(leg_name))
        if len(leg_tokens) < 2:
            return None
        best: tuple[float, dict[str, Any]] | None = None
        for candidate in candidates:
            asset_tokens = set(normalize_tokens(str(candidate.get("asset_name") or "")))
            if not asset_tokens:
                continue
            intersection = len(leg_tokens & asset_tokens)
            union = len(leg_tokens | asset_tokens)
            score = (intersection / union) if union else 0.0
            if best is None or score > best[0]:
                best = (score, candidate)
        if best is None:
            return None
        score, candidate = best
        if score < 0.55:
            return None
        return {
            **candidate,
            "score": round(score, 3),
        }

    def enrich_leg(leg: dict[str, Any]) -> dict[str, Any]:
        mapped = by_isin.get(str(leg.get("isin") or ""))
        if mapped:
            return {
                **leg,
                "mapping_status": "matched",
                "mapping_source": "isin",
                "position_id": mapped.get("position_id"),
                "asset_id": mapped.get("asset_id"),
                "asset_name": mapped.get("asset_name"),
            }
        suggestion = suggest_by_name(leg)
        return {
            **leg,
            "mapping_status": "suggested" if suggestion else "unmatched",
            "mapping_source": "name_similarity" if suggestion else "none",
            "position_id": suggestion.get("position_id") if suggestion else None,
            "asset_id": suggestion.get("asset_id") if suggestion else None,
            "asset_name": suggestion.get("asset_name") if suggestion else None,
            "mapping_score": suggestion.get("score") if suggestion else None,
        }

    return {
        **proposal,
        "from_legs": [enrich_leg(leg) for leg in (proposal.get("from_legs") or [])],
        "to_legs": [enrich_leg(leg) for leg in (proposal.get("to_legs") or [])],
    }


def _mapping_candidates(*, data_dir: Path, contract_name: str | None, db_path: Path | None = None) -> list[dict[str, Any]]:
    runtime = V2Runtime(data_dir, db_path=db_path)
    out: list[dict[str, Any]] = []
    for position in runtime.portfolio.list_all_positions():
        if contract_name and str(position.wrapper.contract_name or "") != str(contract_name):
            continue
        asset = runtime.portfolio.get_asset(position.asset_id)
        if asset is None:
            continue
        out.append(
            {
                "position_id": position.position_id,
                "asset_id": asset.asset_id,
                "asset_name": asset.name,
                "asset_isin": asset.isin,
                "contract_name": position.wrapper.contract_name,
                "label": f"{position.position_id} - {asset.name} ({asset.isin or 'ISIN inconnu'})",
            }
        )
    out.sort(key=lambda row: str(row["label"]))
    return out


def build_arbitration_proposal_for_document(
    data_dir: Path,
    document_id: str,
    *,
    db_path: Path | None = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    data_dir = Path(data_dir)
    db_path = Path(db_path or default_db_path(data_dir))
    ensure_v2_db(data_dir, db_path=db_path)
    _ensure_arbitration_tables(db_path)

    with connect(db_path) as conn:
        doc = conn.execute(
            """
            SELECT document_id, document_type, contract_name, filepath, original_filename, document_date
            FROM documents
            WHERE document_id = ?
            """,
            (document_id,),
        ).fetchone()
        if doc is None:
            raise KeyError(f"Document introuvable: {document_id}")
        if str(doc["document_type"]) != "arbitration_letter":
            raise ValueError("Le document n'est pas un courrier d'arbitrage")

        existing = conn.execute(
            """
            SELECT proposal_json, extraction_status, application_status, notes, updated_at
            FROM document_arbitration_proposals
            WHERE document_id = ?
            """,
            (document_id,),
        ).fetchone()

        if existing is not None and not force_refresh:
            proposal = json.loads(str(existing["proposal_json"]))
            proposal["mapping_candidates"] = _mapping_candidates(
                data_dir=data_dir,
                contract_name=str(doc["contract_name"] or "") or None,
                db_path=db_path,
            )
            return {
                "ok": True,
                "document_id": document_id,
                "proposal": proposal,
                "extraction_status": str(existing["extraction_status"]),
                "application_status": str(existing["application_status"]),
                "notes": existing["notes"],
                "updated_at": existing["updated_at"],
            }

        pdf_path = data_dir / str(doc["filepath"])
        text, extraction_method = extract_pdf_text(pdf_path)
        proposal = parse_arbitration_text(
            text,
            fallback_date=str(doc["document_date"] or "") or None,
        )
        proposal = _map_legs_to_positions(
            data_dir=data_dir,
            contract_name=str(doc["contract_name"] or "") or None,
            proposal=proposal,
            db_path=db_path,
        )
        proposal["extraction_method"] = extraction_method
        proposal["original_filename"] = doc["original_filename"]
        proposal["contract_name"] = doc["contract_name"]
        proposal["mapping_candidates"] = _mapping_candidates(
            data_dir=data_dir,
            contract_name=str(doc["contract_name"] or "") or None,
            db_path=db_path,
        )

        extraction_status = "ok" if proposal.get("from_legs") and proposal.get("to_legs") else "partial"
        conn.execute(
            """
            INSERT INTO document_arbitration_proposals (
                document_id, proposal_json, extraction_status, application_status, notes, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(document_id) DO UPDATE SET
                proposal_json = excluded.proposal_json,
                extraction_status = excluded.extraction_status,
                notes = excluded.notes,
                updated_at = excluded.updated_at
            """,
            (
                document_id,
                json.dumps(proposal, ensure_ascii=False, sort_keys=True),
                extraction_status,
                str(existing["application_status"]) if existing is not None else "pending",
                "Proposition d'arbitrage extraite automatiquement.",
                _timestamp(),
            ),
        )

    return {
        "ok": True,
        "document_id": document_id,
        "proposal": proposal,
        "extraction_status": extraction_status,
        "application_status": str(existing["application_status"]) if existing is not None else "pending",
    }


def save_arbitration_mappings(
    data_dir: Path,
    document_id: str,
    *,
    mappings: list[dict[str, Any]],
    db_path: Path | None = None,
) -> dict[str, Any]:
    data_dir = Path(data_dir)
    db_path = Path(db_path or default_db_path(data_dir))
    current = build_arbitration_proposal_for_document(data_dir, document_id, db_path=db_path, force_refresh=False)
    proposal = dict(current["proposal"])
    candidates = {
        str(row["position_id"]): row for row in (proposal.get("mapping_candidates") or [])
    }

    for mapping in mappings:
        direction = str(mapping.get("direction") or "").strip().lower()
        if direction not in {"from", "to"}:
            raise ValueError(f"Direction mapping invalide: {direction!r}")
        index = int(mapping.get("index"))
        position_id = str(mapping.get("position_id") or "").strip()
        legs_key = "from_legs" if direction == "from" else "to_legs"
        legs = list(proposal.get(legs_key) or [])
        if index < 0 or index >= len(legs):
            raise ValueError(f"Index hors borne pour {legs_key}: {index}")

        if not position_id:
            legs[index] = {
                **legs[index],
                "mapping_status": "unmatched",
                "position_id": None,
                "asset_id": None,
                "asset_name": None,
            }
        else:
            candidate = candidates.get(position_id)
            if candidate is None:
                raise ValueError(f"Position de mapping inconnue: {position_id}")
            legs[index] = {
                **legs[index],
                "mapping_status": "matched",
                "position_id": candidate["position_id"],
                "asset_id": candidate["asset_id"],
                "asset_name": candidate["asset_name"],
            }
        proposal[legs_key] = legs

    _ensure_arbitration_tables(db_path)
    with connect(db_path) as conn:
        conn.execute(
            """
            UPDATE document_arbitration_proposals
            SET proposal_json = ?, notes = ?, updated_at = ?
            WHERE document_id = ?
            """,
            (
                json.dumps(proposal, ensure_ascii=False, sort_keys=True),
                "Mappings arbitrage mis à jour manuellement.",
                _timestamp(),
                document_id,
            ),
        )

    return {
        "ok": True,
        "document_id": document_id,
        "proposal": proposal,
        "extraction_status": current["extraction_status"],
        "application_status": current["application_status"],
    }


def _bucket_from_position(runtime: V2Runtime, position_id: str) -> str:
    position = runtime.portfolio.get_position(position_id)
    if position is None:
        raise KeyError(f"Position introuvable: {position_id}")
    asset = runtime.portfolio.get_asset(position.asset_id)
    if asset is None:
        raise KeyError(f"Actif introuvable pour la position: {position_id}")
    if asset.asset_type.value == "fonds_euro":
        return "fonds_euro"
    if asset.asset_type.value in {"uc_fund", "uc_illiquid"}:
        return "uc"
    if asset.asset_type.value == "structured_product":
        return "structured"
    raise ValueError(f"Type d'actif non supporté pour arbitrage: {asset.asset_type.value}")


def _build_document_movement_rows(
    *,
    proposal: dict[str, Any],
    contract_id: str,
    contract_name: str,
    document_id: str,
    runtime: V2Runtime,
) -> list[dict[str, Any]]:
    effective_date = str(proposal.get("effective_date") or "")
    created_at = _timestamp()
    rows: list[dict[str, Any]] = []

    def append_leg(leg: dict[str, Any], *, raw_lot_type: str, signed_amount: float, signed_units: float | None) -> None:
        position_id = str(leg.get("position_id") or "").strip()
        if not position_id:
            return
        rows.append(
            {
                "document_movement_id": f"docmv_{uuid4().hex[:16]}",
                "document_id": document_id,
                "contract_id": contract_id,
                "contract_name": contract_name,
                "position_id": position_id,
                "asset_id": str(leg.get("asset_id") or ""),
                "asset_name": str(leg.get("asset_name") or leg.get("name") or ""),
                "bucket": _bucket_from_position(runtime, position_id),
                "effective_date": effective_date,
                "raw_lot_type": raw_lot_type,
                "movement_kind": "other",
                "cash_amount": signed_amount,
                "units_delta": signed_units,
                "unit_price": float(leg["nav"]) if leg.get("nav") is not None else None,
                "external_flag": 0,
                "notes": f"Mouvement issu du PDF d'arbitrage {document_id}",
                "created_at": created_at,
            }
        )

    for leg in proposal.get("from_legs") or []:
        amount = float(leg.get("amount") or 0.0)
        units = float(leg["units"]) if leg.get("units") is not None else None
        if units is None and leg.get("amount") is not None and leg.get("nav") not in (None, 0):
            units = float(leg["amount"]) / float(leg["nav"])
        append_leg(
            leg,
            raw_lot_type="sell",
            signed_amount=-abs(amount),
            signed_units=-abs(units) if units is not None else None,
        )

    for leg in proposal.get("to_legs") or []:
        amount = float(leg.get("amount") or 0.0)
        units = float(leg["units"]) if leg.get("units") is not None else None
        if units is None and leg.get("amount") is not None and leg.get("nav") not in (None, 0):
            units = float(leg["amount"]) / float(leg["nav"])
        append_leg(
            leg,
            raw_lot_type="buy",
            signed_amount=abs(amount),
            signed_units=abs(units) if units is not None else None,
        )

    return rows


def _document_movement_signature(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        str(row.get("position_id") or ""),
        str(row.get("asset_id") or ""),
        str(row.get("effective_date") or ""),
        str(row.get("raw_lot_type") or ""),
        str(row.get("movement_kind") or ""),
        round(float(row.get("cash_amount") or 0.0), 2),
        None if row.get("units_delta") is None else round(float(row["units_delta"]), 6),
        None if row.get("unit_price") is None else round(float(row["unit_price"]), 6),
        row.get("external_flag"),
    )


def apply_arbitration_proposal(
    data_dir: Path,
    document_id: str,
    *,
    db_path: Path | None = None,
) -> dict[str, Any]:
    data_dir = Path(data_dir)
    db_path = Path(db_path or default_db_path(data_dir))
    result = build_arbitration_proposal_for_document(data_dir, document_id, db_path=db_path, force_refresh=False)
    proposal = dict(result["proposal"])

    effective_date = str(proposal.get("effective_date") or "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", effective_date):
        raise ValueError("Date d'arbitrage introuvable dans le document")

    blocking_legs: list[str] = []
    for leg in (proposal.get("from_legs") or []) + (proposal.get("to_legs") or []):
        if str(leg.get("mapping_status")) == "matched":
            continue
        if leg.get("amount") is None and leg.get("units") is None:
            continue
        blocking_legs.append(str(leg.get("isin") or leg.get("name") or "unknown"))
    if blocking_legs:
        raise ValueError(
            "Arbitrage non applicable automatiquement: supports non mappés "
            + ", ".join(blocking_legs)
        )

    runtime = V2Runtime(data_dir, db_path=db_path)
    _ensure_arbitration_tables(db_path)
    created = 0
    skipped = 0

    with connect(db_path) as conn:
        contract_row = conn.execute(
            """
            SELECT c.contract_id, c.contract_name
            FROM documents d
            JOIN contracts c ON c.contract_name = d.contract_name
            WHERE d.document_id = ?
            """,
            (document_id,),
        ).fetchone()
        if contract_row is None:
            raise KeyError(f"Contrat introuvable pour le document: {document_id}")

        desired_rows = _build_document_movement_rows(
            proposal=proposal,
            contract_id=str(contract_row["contract_id"]),
            contract_name=str(contract_row["contract_name"]),
            document_id=document_id,
            runtime=runtime,
        )
        existing_rows = conn.execute(
            """
            SELECT position_id, asset_id, effective_date, raw_lot_type, movement_kind, cash_amount,
                   units_delta, unit_price, external_flag
            FROM document_movements
            WHERE document_id = ?
            ORDER BY effective_date, document_movement_id
            """,
            (document_id,),
        ).fetchall()

        existing_signatures = sorted(
            _document_movement_signature(dict(row))
            for row in existing_rows
        )
        desired_signatures = sorted(
            _document_movement_signature(row)
            for row in desired_rows
        )

        if existing_signatures != desired_signatures:
            conn.execute("DELETE FROM document_movements WHERE document_id = ?", (document_id,))
            for row in desired_rows:
                conn.execute(
                    """
                    INSERT INTO document_movements (
                        document_movement_id, document_id, contract_id, contract_name, position_id,
                        asset_id, asset_name, bucket, effective_date, raw_lot_type, movement_kind,
                        cash_amount, units_delta, unit_price, external_flag, notes, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["document_movement_id"],
                        row["document_id"],
                        row["contract_id"],
                        row["contract_name"],
                        row["position_id"],
                        row["asset_id"],
                        row["asset_name"],
                        row["bucket"],
                        row["effective_date"],
                        row["raw_lot_type"],
                        row["movement_kind"],
                        row["cash_amount"],
                        row["units_delta"],
                        row["unit_price"],
                        row["external_flag"],
                        row["notes"],
                        row["created_at"],
                    ),
                )
            created = len(desired_rows)
        else:
            skipped = len(desired_rows)

    refresh_v2_derived_state(data_dir, db_path=db_path)
    with connect(db_path) as conn:
        conn.execute(
            """
            UPDATE document_arbitration_proposals
            SET application_status = ?, notes = ?, updated_at = ?
            WHERE document_id = ?
            """,
            (
                "applied",
                f"Mouvements PDF persistés en base: created={created}, skipped={skipped}",
                _timestamp(),
                document_id,
            ),
        )

    return {
        "ok": True,
        "document_id": document_id,
        "created_movements": created,
        "created_lots": created,
        "skipped_legs": skipped,
        "effective_date": effective_date,
    }
