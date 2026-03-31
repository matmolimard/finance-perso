"""Saisie et validations manuelles pour la V2."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .bootstrap import ensure_v2_db, refresh_v2_derived_state
from .runtime import V2Runtime
from .storage import connect, default_db_path


def _timestamp() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _resolve_contract(conn, contract_ref: str):
    contract_ref = str(contract_ref or "").strip()
    if not contract_ref:
        raise ValueError("contract requis")
    row = conn.execute(
        """
        SELECT contract_id, contract_name
        FROM contracts
        WHERE contract_id = ? OR contract_name = ?
        LIMIT 1
        """,
        (contract_ref, contract_ref),
    ).fetchone()
    if row is None:
        raise KeyError(f"Contrat introuvable: {contract_ref}")
    return row


def _resolve_manual_context(runtime: V2Runtime, *, contract_name: str, asset_id: str | None, position_id: str | None) -> dict[str, Any]:
    position = None
    if position_id:
        position = runtime.portfolio.get_position(position_id)
        if position is None:
            raise KeyError(f"Position introuvable: {position_id}")
        if str(position.wrapper.contract_name or "") != contract_name:
            raise ValueError(f"La position {position_id} n'appartient pas au contrat {contract_name}")
        if asset_id and str(position.asset_id) != str(asset_id):
            raise ValueError(f"La position {position_id} ne correspond pas à l'actif {asset_id}")
        asset_id = position.asset_id

    if not asset_id:
        raise ValueError("asset_id requis")

    asset = runtime.portfolio.get_asset(str(asset_id))
    if asset is None:
        raise KeyError(f"Actif introuvable: {asset_id}")

    bucket = {
        "fonds_euro": "fonds_euro",
        "uc_fund": "uc",
        "uc_illiquid": "uc",
        "structured_product": "structured",
    }.get(asset.asset_type.value)
    if bucket is None:
        raise ValueError(f"Type d'actif non supporté pour ledger manuel: {asset.asset_type.value}")

    return {
        "position_id": position.position_id if position else None,
        "asset_id": asset.asset_id,
        "asset_name": asset.name,
        "bucket": bucket,
    }


def list_manual_movements(
    data_dir: Path,
    *,
    contract_ref: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    data_dir = Path(data_dir)
    db_path = Path(db_path or default_db_path(data_dir))
    ensure_v2_db(data_dir, db_path=db_path)

    query = (
        "SELECT manual_movement_id, contract_id, contract_name, position_id, asset_id, asset_name, "
        "bucket, effective_date, raw_lot_type, movement_kind, cash_amount, units_delta, unit_price, "
        "external_flag, linked_document_id, reason, notes, created_at "
        "FROM manual_movements"
    )
    params: list[Any] = []
    if contract_ref:
        query += " WHERE contract_id = ? OR contract_name = ?"
        params.extend([contract_ref, contract_ref])
    query += " ORDER BY effective_date, created_at, manual_movement_id"

    with connect(db_path) as conn:
        rows = conn.execute(query, params).fetchall()
    return {
        "ok": True,
        "manual_movements": [
            {
                "manual_movement_id": row["manual_movement_id"],
                "contract_id": row["contract_id"],
                "contract_name": row["contract_name"],
                "position_id": row["position_id"],
                "asset_id": row["asset_id"],
                "asset_name": row["asset_name"],
                "bucket": row["bucket"],
                "effective_date": row["effective_date"],
                "raw_lot_type": row["raw_lot_type"],
                "movement_kind": row["movement_kind"],
                "cash_amount": float(row["cash_amount"] or 0.0),
                "units_delta": float(row["units_delta"]) if row["units_delta"] is not None else None,
                "unit_price": float(row["unit_price"]) if row["unit_price"] is not None else None,
                "external_flag": row["external_flag"],
                "linked_document_id": row["linked_document_id"],
                "reason": row["reason"],
                "notes": row["notes"],
                "created_at": row["created_at"],
            }
            for row in rows
        ],
    }


def save_manual_movement(data_dir: Path, payload: dict[str, Any], db_path: Path | None = None) -> dict[str, Any]:
    data_dir = Path(data_dir)
    db_path = Path(db_path or default_db_path(data_dir))
    ensure_v2_db(data_dir, db_path=db_path)

    contract_ref = str(payload.get("contract") or payload.get("contract_ref") or "").strip()
    effective_date = date.fromisoformat(str(payload.get("effective_date") or payload.get("date") or ""))
    raw_lot_type = str(payload.get("raw_lot_type") or payload.get("lot_type") or payload.get("type") or "").strip().lower()
    movement_kind = str(payload.get("movement_kind") or payload.get("kind") or "").strip()
    if raw_lot_type not in {"buy", "sell", "fee", "tax", "other"}:
        raise ValueError(f"Type de lot manuel invalide: {raw_lot_type}")
    if movement_kind not in {"external_contribution", "internal_capitalization", "withdrawal", "fee", "tax", "other"}:
        raise ValueError(f"movement_kind invalide: {movement_kind}")
    reason = str(payload.get("reason") or "").strip()
    if not reason:
        raise ValueError("reason requis")

    cash_amount = float(payload.get("cash_amount") if payload.get("cash_amount") is not None else payload.get("amount"))
    units_delta = float(payload["units_delta"]) if payload.get("units_delta") is not None else (
        float(payload["units"]) if payload.get("units") is not None else None
    )
    unit_price = float(payload["unit_price"]) if payload.get("unit_price") is not None else (
        float(payload["nav"]) if payload.get("nav") is not None else None
    )
    external_flag = payload.get("external_flag")
    if external_flag is None:
        if payload.get("external") is True:
            external_flag = 1
        elif payload.get("external") is False:
            external_flag = 0

    runtime = V2Runtime(data_dir, db_path=db_path)
    with connect(db_path) as conn:
        contract_row = _resolve_contract(conn, contract_ref)
        context = _resolve_manual_context(
            runtime,
            contract_name=str(contract_row["contract_name"]),
            asset_id=str(payload.get("asset_id") or "").strip() or None,
            position_id=str(payload.get("position_id") or "").strip() or None,
        )
        manual_movement_id = f"manual_{uuid4().hex[:16]}"
        conn.execute(
            """
            INSERT INTO manual_movements (
                manual_movement_id, contract_id, contract_name, position_id, asset_id, asset_name, bucket,
                effective_date, raw_lot_type, movement_kind, cash_amount, units_delta, unit_price,
                external_flag, linked_document_id, reason, notes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                manual_movement_id,
                contract_row["contract_id"],
                contract_row["contract_name"],
                context["position_id"],
                context["asset_id"],
                context["asset_name"],
                context["bucket"],
                effective_date.isoformat(),
                raw_lot_type,
                movement_kind,
                cash_amount,
                units_delta,
                unit_price,
                external_flag,
                payload.get("linked_document_id") or payload.get("document_id") or None,
                reason,
                payload.get("notes") or None,
                _timestamp(),
            ),
        )

    refresh_v2_derived_state(data_dir, db_path=db_path)
    created = next(
        row
        for row in list_manual_movements(data_dir, contract_ref=str(contract_row["contract_id"]), db_path=db_path)["manual_movements"]
        if row["manual_movement_id"] == manual_movement_id
    )
    return {
        "ok": True,
        "manual_movement": created,
    }


def delete_manual_movement(data_dir: Path, manual_movement_id: str, db_path: Path | None = None) -> dict[str, Any]:
    data_dir = Path(data_dir)
    db_path = Path(db_path or default_db_path(data_dir))
    ensure_v2_db(data_dir, db_path=db_path)

    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT manual_movement_id FROM manual_movements WHERE manual_movement_id = ?",
            (manual_movement_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Mouvement manuel introuvable: {manual_movement_id}")
        conn.execute(
            "DELETE FROM manual_movements WHERE manual_movement_id = ?",
            (manual_movement_id,),
        )

    refresh_v2_derived_state(data_dir, db_path=db_path)
    return {"ok": True, "manual_movement_id": manual_movement_id, "deleted": True}


def save_structured_product_rule(data_dir: Path, asset_id: str, payload: dict[str, Any], db_path: Path | None = None) -> dict[str, Any]:
    data_dir = Path(data_dir)
    db_path = Path(db_path or default_db_path(data_dir))
    ensure_v2_db(data_dir, db_path=db_path)

    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO structured_product_rules (
                asset_id, display_name_override, isin_override, rule_source_mode,
                coupon_payment_mode, coupon_frequency, coupon_rule_summary,
                autocall_rule_summary, capital_rule_summary, brochure_document_id,
                notes, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(asset_id) DO UPDATE SET
                display_name_override = excluded.display_name_override,
                isin_override = excluded.isin_override,
                rule_source_mode = excluded.rule_source_mode,
                coupon_payment_mode = excluded.coupon_payment_mode,
                coupon_frequency = excluded.coupon_frequency,
                coupon_rule_summary = excluded.coupon_rule_summary,
                autocall_rule_summary = excluded.autocall_rule_summary,
                capital_rule_summary = excluded.capital_rule_summary,
                brochure_document_id = excluded.brochure_document_id,
                notes = excluded.notes,
                updated_at = excluded.updated_at
            """,
            (
                asset_id,
                payload.get("display_name_override") or None,
                payload.get("isin_override") or None,
                payload.get("rule_source_mode") or None,
                payload.get("coupon_payment_mode") or None,
                payload.get("coupon_frequency") or None,
                payload.get("coupon_rule_summary") or None,
                payload.get("autocall_rule_summary") or None,
                payload.get("capital_rule_summary") or None,
                payload.get("brochure_document_id") or None,
                payload.get("notes") or None,
                _timestamp(),
            ),
        )
    return {"ok": True}


def save_structured_event_validation(data_dir: Path, asset_id: str, payload: dict[str, Any], db_path: Path | None = None) -> dict[str, Any]:
    data_dir = Path(data_dir)
    db_path = Path(db_path or default_db_path(data_dir))
    ensure_v2_db(data_dir, db_path=db_path)

    event_key = str(payload.get("event_key") or "").strip()
    if not event_key:
        raise ValueError("event_key requis")

    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO structured_event_validations (
                asset_id, event_key, event_type, event_date, validation_status, notes, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(asset_id, event_key) DO UPDATE SET
                event_type = excluded.event_type,
                event_date = excluded.event_date,
                validation_status = excluded.validation_status,
                notes = excluded.notes,
                updated_at = excluded.updated_at
            """,
            (
                asset_id,
                event_key,
                payload.get("event_type") or "",
                payload.get("event_date") or None,
                payload.get("validation_status") or "unknown",
                payload.get("notes") or None,
                _timestamp(),
            ),
        )
    return {"ok": True}


def save_snapshot_validation(data_dir: Path, snapshot_id: str, payload: dict[str, Any], db_path: Path | None = None) -> dict[str, Any]:
    """Validate or reject a proposed annual snapshot."""
    data_dir = Path(data_dir)
    db_path = Path(db_path or default_db_path(data_dir))
    ensure_v2_db(data_dir, db_path=db_path)

    new_status = str(payload.get("status") or "validated").strip()
    if new_status not in {"validated", "rejected", "proposed"}:
        raise ValueError(f"Statut invalide: {new_status}")

    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT snapshot_id, status FROM annual_snapshots WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Snapshot introuvable: {snapshot_id}")

        updates = {"status": new_status, "official_notes": payload.get("notes")}
        for field in ("official_total_value", "official_uc_value", "official_fonds_euro_value", "official_euro_interest_net"):
            if field in payload and payload[field] is not None:
                updates[field] = float(payload[field])

        set_clauses = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [snapshot_id]
        conn.execute(
            f"UPDATE annual_snapshots SET {set_clauses} WHERE snapshot_id = ?",
            values,
        )
    return {"ok": True, "snapshot_id": snapshot_id, "status": new_status}


def save_document_validation(data_dir: Path, document_id: str, payload: dict[str, Any], db_path: Path | None = None) -> dict[str, Any]:
    data_dir = Path(data_dir)
    db_path = Path(db_path or default_db_path(data_dir))
    ensure_v2_db(data_dir, db_path=db_path)

    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO document_validations (
                document_id, validation_status, notes, updated_at
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(document_id) DO UPDATE SET
                validation_status = excluded.validation_status,
                notes = excluded.notes,
                updated_at = excluded.updated_at
            """,
            (
                document_id,
                payload.get("validation_status") or "pending",
                payload.get("notes") or None,
                _timestamp(),
            ),
        )
    return {"ok": True}


def _flow_amount(lot: dict[str, Any]) -> float:
    for key in ("net_amount", "gross_amount"):
        value = lot.get(key)
        if value is not None:
            return float(value)
    return 0.0


def _compute_fonds_euro_pilotage(
    runtime: V2Runtime,
    contract_name: str,
    start_value: float,
    reference_date: date,
    annual_rate: float,
) -> dict[str, Any]:
    positions = [
        position
        for position in runtime.portfolio.list_all_positions()
        if position.wrapper.contract_name == contract_name
        and runtime.portfolio.get_asset(position.asset_id) is not None
        and runtime.portfolio.get_asset(position.asset_id).asset_type.value == "fonds_euro"
    ]
    year_start = date(reference_date.year, 1, 1)
    cursor = year_start
    balance = float(start_value)
    accrued_gain = 0.0
    net_flows = 0.0

    lots: list[dict[str, Any]] = []
    for position in positions:
        for lot in position.investment.lots or []:
            lot_date = date.fromisoformat(str(lot["date"]))
            if year_start <= lot_date <= reference_date:
                lots.append({"date": lot_date, "amount": _flow_amount(lot), "position_id": position.position_id})
    lots.sort(key=lambda row: (row["date"], row["position_id"]))

    for lot in lots:
        days = max((lot["date"] - cursor).days, 0)
        accrued_gain += balance * annual_rate * days / 365.0
        balance += lot["amount"]
        net_flows += lot["amount"]
        cursor = lot["date"]

    days = max((reference_date - cursor).days, 0)
    accrued_gain += balance * annual_rate * days / 365.0

    return {
        "reference_date": reference_date.isoformat(),
        "annual_rate": annual_rate,
        "start_value": float(start_value),
        "net_flows": net_flows,
        "accrued_gain": accrued_gain,
        "pilotage_value": float(start_value) + net_flows + accrued_gain,
        "flows_count": len(lots),
    }


def save_fonds_euro_pilotage(data_dir: Path, contract_id: str, payload: dict[str, Any], db_path: Path | None = None) -> dict[str, Any]:
    data_dir = Path(data_dir)
    db_path = Path(db_path or default_db_path(data_dir))
    ensure_v2_db(data_dir, db_path=db_path)

    reference_date = date.fromisoformat(str(payload.get("reference_date") or date.today().isoformat()))
    annual_rate = float(payload.get("annual_rate") or 0.0)

    with connect(db_path) as conn:
        contract_row = conn.execute(
            "SELECT contract_name FROM contracts WHERE contract_id = ?",
            (contract_id,),
        ).fetchone()
        if contract_row is None:
            raise KeyError(f"Contrat introuvable: {contract_id}")
        snapshot_row = conn.execute(
            """
            SELECT official_fonds_euro_value
            FROM annual_snapshots
            WHERE contract_id = ?
            ORDER BY reference_date DESC
            LIMIT 1
            """,
            (contract_id,),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO fonds_euro_pilotage (
                contract_id, pilotage_year, annual_rate, reference_date, notes, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(contract_id) DO UPDATE SET
                pilotage_year = excluded.pilotage_year,
                annual_rate = excluded.annual_rate,
                reference_date = excluded.reference_date,
                notes = excluded.notes,
                updated_at = excluded.updated_at
            """,
            (
                contract_id,
                reference_date.year,
                annual_rate,
                reference_date.isoformat(),
                payload.get("notes") or None,
                _timestamp(),
            ),
        )

    runtime = V2Runtime(data_dir, db_path=db_path)
    pilotage = _compute_fonds_euro_pilotage(
        runtime,
        str(contract_row["contract_name"]),
        float(snapshot_row["official_fonds_euro_value"] or 0.0) if snapshot_row else 0.0,
        reference_date,
        annual_rate,
    )
    return {"ok": True, "pilotage": pilotage}
