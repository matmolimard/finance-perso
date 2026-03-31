"""Audit PDF des snapshots et opérations visibles stockés en base."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .bootstrap import ensure_v2_db
from .storage import connect, default_db_path


def _resolve_contract(conn, contract_ref: str):
    contract_ref = str(contract_ref or "").strip()
    if not contract_ref:
        raise ValueError("contract requis")
    row = conn.execute(
        """
        SELECT contract_id, contract_name, insurer, status
        FROM contracts
        WHERE contract_id = ? OR contract_name = ?
        LIMIT 1
        """,
        (contract_ref, contract_ref),
    ).fetchone()
    if row is None:
        raise KeyError(f"Contrat introuvable: {contract_ref}")
    return row


def build_contract_pdf_audit(
    data_dir: Path,
    contract_ref: str,
    *,
    year: int | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    data_dir = Path(data_dir)
    db_path = Path(db_path or default_db_path(data_dir))
    ensure_v2_db(data_dir, db_path=db_path)

    with connect(db_path) as conn:
        contract_row = _resolve_contract(conn, contract_ref)
        params: list[Any] = [contract_row["contract_id"]]
        year_clause = ""
        if year is not None:
            year_clause = " AND substr(reference_date, 1, 4) = ?"
            params.append(str(year))

        snapshot_rows = conn.execute(
            f"""
            SELECT snapshot_id, reference_date, statement_date, status, source_document_id,
                   official_total_value, official_uc_value, official_fonds_euro_value
            FROM annual_snapshots
            WHERE contract_id = ?{year_clause}
            ORDER BY reference_date
            """,
            params,
        ).fetchall()
        snapshot_ids = [str(row["snapshot_id"]) for row in snapshot_rows]

        snapshot_positions: list[dict[str, Any]] = []
        snapshot_operations_by_id: dict[str, dict[str, Any]] = {}
        snapshot_operation_legs: list[dict[str, Any]] = []
        manual_movements: list[dict[str, Any]] = []

        if snapshot_ids:
            placeholders = ", ".join("?" for _ in snapshot_ids)
            snapshot_positions = [
                {
                    "snapshot_position_id": row["snapshot_position_id"],
                    "snapshot_id": row["snapshot_id"],
                    "position_id": row["position_id"],
                    "asset_id": row["asset_id"],
                    "asset_type": row["asset_type"],
                    "asset_name_raw": row["asset_name_raw"],
                    "isin": row["isin"],
                    "valuation_date": row["valuation_date"],
                    "quantity": float(row["quantity"]) if row["quantity"] is not None else None,
                    "unit_value": float(row["unit_value"]) if row["unit_value"] is not None else None,
                    "official_value": float(row["official_value"] or 0.0),
                    "status": row["status"],
                    "notes": row["notes"],
                }
                for row in conn.execute(
                    f"""
                    SELECT snapshot_position_id, snapshot_id, position_id, asset_id, asset_type, asset_name_raw,
                           isin, valuation_date, quantity, unit_value, official_value, status, notes
                    FROM snapshot_positions
                    WHERE snapshot_id IN ({placeholders})
                    ORDER BY snapshot_id, official_value DESC, asset_name_raw
                    """,
                    snapshot_ids,
                ).fetchall()
            ]

            operation_rows = conn.execute(
                f"""
                SELECT snapshot_operation_id, snapshot_id, operation_label, operation_type, effective_date,
                       headline_amount, fees_info_raw, status, notes
                FROM snapshot_operations_visible
                WHERE snapshot_id IN ({placeholders})
                ORDER BY snapshot_id, effective_date, snapshot_operation_id
                """,
                snapshot_ids,
            ).fetchall()
            snapshot_operations_by_id = {
                str(row["snapshot_operation_id"]): {
                    "snapshot_operation_id": row["snapshot_operation_id"],
                    "snapshot_id": row["snapshot_id"],
                    "operation_label": row["operation_label"],
                    "operation_type": row["operation_type"],
                    "effective_date": row["effective_date"],
                    "headline_amount": float(row["headline_amount"]) if row["headline_amount"] is not None else None,
                    "fees_info_raw": row["fees_info_raw"],
                    "status": row["status"],
                    "notes": row["notes"],
                    "legs": [],
                }
                for row in operation_rows
            }
            snapshot_operation_legs = [
                {
                    "snapshot_operation_leg_id": row["snapshot_operation_leg_id"],
                    "snapshot_operation_id": row["snapshot_operation_id"],
                    "snapshot_id": row["snapshot_id"],
                    "position_id": row["position_id"],
                    "asset_id": row["asset_id"],
                    "asset_type": row["asset_type"],
                    "asset_name_raw": row["asset_name_raw"],
                    "effective_date": row["effective_date"],
                    "cash_amount": float(row["cash_amount"] or 0.0),
                    "quantity": float(row["quantity"]) if row["quantity"] is not None else None,
                    "unit_value": float(row["unit_value"]) if row["unit_value"] is not None else None,
                    "direction": row["direction"],
                    "notes": row["notes"],
                }
                for row in conn.execute(
                    f"""
                    SELECT snapshot_operation_leg_id, snapshot_operation_id, snapshot_id, position_id, asset_id,
                           asset_type, asset_name_raw, effective_date, cash_amount, quantity, unit_value,
                           direction, notes
                    FROM snapshot_operation_legs_visible
                    WHERE snapshot_id IN ({placeholders})
                    ORDER BY snapshot_id, snapshot_operation_id, snapshot_operation_leg_id
                    """,
                    snapshot_ids,
                ).fetchall()
            ]
            for leg in snapshot_operation_legs:
                snapshot_operations_by_id[str(leg["snapshot_operation_id"])]["legs"].append(leg)

        manual_params: list[Any] = [contract_row["contract_id"]]
        manual_year_clause = ""
        if year is not None:
            manual_year_clause = " AND substr(effective_date, 1, 4) = ?"
            manual_params.append(str(year))
        manual_movements = [
            {
                "manual_movement_id": row["manual_movement_id"],
                "position_id": row["position_id"],
                "asset_id": row["asset_id"],
                "asset_name": row["asset_name"],
                "effective_date": row["effective_date"],
                "raw_lot_type": row["raw_lot_type"],
                "movement_kind": row["movement_kind"],
                "cash_amount": float(row["cash_amount"] or 0.0),
                "units_delta": float(row["units_delta"]) if row["units_delta"] is not None else None,
                "external_flag": row["external_flag"],
                "reason": row["reason"],
                "notes": row["notes"],
            }
            for row in conn.execute(
                f"""
                SELECT manual_movement_id, position_id, asset_id, asset_name, effective_date, raw_lot_type,
                       movement_kind, cash_amount, units_delta, external_flag, reason, notes
                FROM manual_movements
                WHERE contract_id = ?{manual_year_clause}
                ORDER BY effective_date, manual_movement_id
                """,
                manual_params,
            ).fetchall()
        ]

    positions_by_snapshot: dict[str, list[dict[str, Any]]] = {}
    for row in snapshot_positions:
        positions_by_snapshot.setdefault(str(row["snapshot_id"]), []).append(row)

    operations_by_snapshot: dict[str, list[dict[str, Any]]] = {}
    for row in snapshot_operations_by_id.values():
        operations_by_snapshot.setdefault(str(row["snapshot_id"]), []).append(row)

    snapshots = []
    for row in snapshot_rows:
        snapshot_id = str(row["snapshot_id"])
        positions = positions_by_snapshot.get(snapshot_id, [])
        operations = operations_by_snapshot.get(snapshot_id, [])
        operation_legs_count = sum(len(operation["legs"]) for operation in operations)
        snapshots.append(
            {
                "snapshot_id": snapshot_id,
                "reference_date": row["reference_date"],
                "statement_date": row["statement_date"],
                "status": row["status"],
                "source_document_id": row["source_document_id"],
                "official_total_value": float(row["official_total_value"] or 0.0),
                "official_uc_value": float(row["official_uc_value"]) if row["official_uc_value"] is not None else None,
                "official_fonds_euro_value": (
                    float(row["official_fonds_euro_value"])
                    if row["official_fonds_euro_value"] is not None
                    else None
                ),
                "positions_count": len(positions),
                "positions_mapped_count": sum(1 for position in positions if position["asset_id"]),
                "positions_unmapped_count": sum(1 for position in positions if not position["asset_id"]),
                "positions_official_value_total": round(
                    sum(float(position["official_value"] or 0.0) for position in positions),
                    2,
                ),
                "visible_operations_count": len(operations),
                "visible_operation_legs_count": operation_legs_count,
                "visible_operation_legs_mapped_count": sum(
                    1
                    for operation in operations
                    for leg in operation["legs"]
                    if leg["asset_id"]
                ),
                "visible_operation_legs_unmapped_count": sum(
                    1
                    for operation in operations
                    for leg in operation["legs"]
                    if not leg["asset_id"]
                ),
            }
        )

    visible_operations = []
    for snapshot in snapshots:
        for operation in operations_by_snapshot.get(str(snapshot["snapshot_id"]), []):
            visible_operations.append(
                {
                    **operation,
                    "reference_date": snapshot["reference_date"],
                    "statement_date": snapshot["statement_date"],
                }
            )

    summary = {
        "snapshots_count": len(snapshots),
        "snapshot_positions_count": len(snapshot_positions),
        "snapshot_positions_mapped_count": sum(1 for row in snapshot_positions if row["asset_id"]),
        "snapshot_positions_unmapped_count": sum(1 for row in snapshot_positions if not row["asset_id"]),
        "snapshot_positions_official_value_total": round(
            sum(float(row["official_value"] or 0.0) for row in snapshot_positions),
            2,
        ),
        "visible_operations_count": len(visible_operations),
        "visible_operation_legs_count": len(snapshot_operation_legs),
        "visible_operation_legs_mapped_count": sum(1 for row in snapshot_operation_legs if row["asset_id"]),
        "visible_operation_legs_unmapped_count": sum(1 for row in snapshot_operation_legs if not row["asset_id"]),
        "manual_movements_count": len(manual_movements),
    }

    return {
        "ok": True,
        "meta": {
            "db_path": str(db_path),
            "year": int(year) if year is not None else None,
        },
        "contract": {
            "contract_id": contract_row["contract_id"],
            "contract_name": contract_row["contract_name"],
            "insurer": contract_row["insurer"],
            "status": contract_row["status"],
        },
        "summary": summary,
        "snapshots": snapshots,
        "snapshot_positions": snapshot_positions,
        "visible_operations": visible_operations,
        "manual_movements": manual_movements,
    }
