"""Saisie et validations manuelles pour la V2."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

from .bootstrap import bootstrap_v2_data
from .runtime import V2Runtime
from .storage import connect, default_db_path


def _timestamp() -> str:
    return datetime.now().isoformat(timespec="seconds")


def save_structured_product_rule(data_dir: Path, asset_id: str, payload: dict[str, Any], db_path: Path | None = None) -> dict[str, Any]:
    data_dir = Path(data_dir)
    db_path = Path(db_path or default_db_path(data_dir))
    bootstrap_v2_data(data_dir, db_path=db_path)

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
    bootstrap_v2_data(data_dir, db_path=db_path)

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


def save_document_validation(data_dir: Path, document_id: str, payload: dict[str, Any], db_path: Path | None = None) -> dict[str, Any]:
    data_dir = Path(data_dir)
    db_path = Path(db_path or default_db_path(data_dir))
    bootstrap_v2_data(data_dir, db_path=db_path)

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
    bootstrap_v2_data(data_dir, db_path=db_path)

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

    runtime = V2Runtime(data_dir)
    pilotage = _compute_fonds_euro_pilotage(
        runtime,
        str(contract_row["contract_name"]),
        float(snapshot_row["official_fonds_euro_value"] or 0.0) if snapshot_row else 0.0,
        reference_date,
        annual_rate,
    )
    return {"ok": True, "pilotage": pilotage}
