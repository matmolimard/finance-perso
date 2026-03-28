"""Détails contrat et support pour la V2."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from .dashboard import build_v2_dashboard_data
from .manual import _compute_fonds_euro_pilotage
from .runtime import V2Runtime
from .storage import connect, default_db_path


def _dashboard_payload(data_dir: Path) -> dict[str, Any]:
    return V2Runtime(data_dir).build_web_payload(include_terminated=False)


def _view_rows_for_contract(dashboard: dict[str, Any], contract_name: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for view_name in ("fonds_euro", "uc", "structured"):
        for row in dashboard["views"].get(view_name, []):
            if str(row.get("contract_name")) != contract_name:
                continue
            enriched = dict(row)
            enriched["view_name"] = view_name
            rows.append(enriched)
    rows.sort(key=lambda row: (row["view_name"], row.get("display_name") or row.get("name") or ""))
    return rows


def _row_for_position(dashboard: dict[str, Any], position_id: str) -> dict[str, Any] | None:
    for view_name in ("fonds_euro", "uc", "structured"):
        for row in dashboard["views"].get(view_name, []):
            if str(row.get("position_id")) == position_id:
                enriched = dict(row)
                enriched["view_name"] = view_name
                return enriched
    return None


def build_v2_contract_detail(data_dir: Path, contract_id: str, db_path: Path | None = None) -> dict[str, Any]:
    data_dir = Path(data_dir)
    db_path = Path(db_path or default_db_path(data_dir))
    dashboard_v2 = build_v2_dashboard_data(data_dir, db_path=db_path)
    import_summary = dashboard_v2["import_summary"]
    dashboard_v1 = _dashboard_payload(data_dir)

    contract_card = next((card for card in dashboard_v2["contracts"] if card["contract_id"] == contract_id), None)
    if contract_card is None:
        raise KeyError(f"Contrat introuvable: {contract_id}")

    contract_name = contract_card["contract_name"]
    rows = _view_rows_for_contract(dashboard_v1, contract_name)
    with connect(db_path) as conn:
        document_rows = conn.execute(
            """
            SELECT document_id, document_type, document_date, coverage_year, status, filepath, original_filename
            FROM documents
            WHERE contract_name = ?
            ORDER BY COALESCE(document_date, '') DESC, original_filename
            """,
            (contract_name,),
        ).fetchall()
        document_validation_rows = conn.execute(
            """
            SELECT dv.document_id, dv.validation_status, dv.notes
            FROM document_validations dv
            JOIN documents d ON d.document_id = dv.document_id
            WHERE d.contract_name = ?
            """,
            (contract_name,),
        ).fetchall()
        pilotage_row = conn.execute(
            """
            SELECT pilotage_year, annual_rate, reference_date, notes
            FROM fonds_euro_pilotage
            WHERE contract_id = ?
            """,
            (contract_id,),
        ).fetchone()

    positions_by_type = {"fonds_euro": [], "uc": [], "structured": []}
    for row in rows:
        positions_by_type[row["view_name"]].append(row)

    latest_snapshot = contract_card.get("latest_snapshot")
    official_gap = None
    if latest_snapshot:
        official_gap = float(contract_card["current_value"]) - float(latest_snapshot["official_total_value"])
    document_validations = {
        str(row["document_id"]): {
            "validation_status": row["validation_status"],
            "notes": row["notes"],
        }
        for row in document_validation_rows
    }
    documents = []
    for row in document_rows:
        validation = document_validations.get(str(row["document_id"]), {})
        documents.append(
            {
                "document_id": row["document_id"],
                "document_type": row["document_type"],
                "document_date": row["document_date"],
                "coverage_year": row["coverage_year"],
                "status": row["status"],
                "filepath": row["filepath"],
                "original_filename": row["original_filename"],
                "validation_status": validation.get("validation_status", "pending"),
                "validation_notes": validation.get("notes"),
            }
        )

    pilotage = None
    if pilotage_row and latest_snapshot:
        runtime = V2Runtime(data_dir)
        pilotage = _compute_fonds_euro_pilotage(
            runtime,
            contract_name,
            float(latest_snapshot["official_fonds_euro_value"] or 0.0),
            datetime.fromisoformat(str(pilotage_row["reference_date"])).date(),
            float(pilotage_row["annual_rate"] or 0.0),
        )
        pilotage["notes"] = pilotage_row["notes"]

    return {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "contract_id": contract_id,
            "db_path": str(db_path),
        },
        "import_summary": import_summary,
        "contract": contract_card,
        "positions": rows,
        "positions_by_type": positions_by_type,
        "snapshots": dashboard_v2["snapshots_by_contract"].get(contract_name, []),
        "documents": documents,
        "fonds_euro_pilotage": {
            "annual_rate": float(pilotage_row["annual_rate"] or 0.0),
            "reference_date": pilotage_row["reference_date"],
            "notes": pilotage_row["notes"],
        }
        if pilotage_row
        else None,
        "fonds_euro_pilotage_summary": pilotage,
        "summary": {
            "current_value": float(contract_card["current_value"]),
            "official_total_value": float(latest_snapshot["official_total_value"]) if latest_snapshot else None,
            "official_gap": official_gap,
            "active_positions_count": int(contract_card["active_positions_count"]),
        },
    }


def build_v2_support_detail(data_dir: Path, position_id: str, db_path: Path | None = None) -> dict[str, Any]:
    data_dir = Path(data_dir)
    db_path = Path(db_path or default_db_path(data_dir))
    dashboard_v2 = build_v2_dashboard_data(data_dir, db_path=db_path)
    import_summary = dashboard_v2["import_summary"]
    dashboard_v1 = _dashboard_payload(data_dir)
    runtime = V2Runtime(data_dir)

    position = runtime.portfolio.get_position(position_id)
    if position is None:
        raise KeyError(f"Position introuvable: {position_id}")
    asset = runtime.portfolio.get_asset(position.asset_id)
    if asset is None:
        raise KeyError(f"Actif introuvable pour la position: {position_id}")

    current_row = _row_for_position(dashboard_v1, position_id)
    structured_row = next(
        (row for row in dashboard_v2["structured_coverage"] if row["position_id"] == position_id),
        None,
    )
    contract_card = next(
        (card for card in dashboard_v2["contracts"] if card["contract_name"] == position.wrapper.contract_name),
        None,
    )

    related_asset_ids = {position.asset_id}
    if asset.isin:
        related_asset_ids.add(asset.isin)

    events_file = Path(data_dir) / "market_data" / f"events_{asset.asset_id}.yaml"
    event_payload = {}
    if events_file.exists():
        event_payload = yaml.safe_load(events_file.read_text(encoding="utf-8")) or {}
    expected_events = list(event_payload.get("expected_events") or [])
    observed_events = list(event_payload.get("events") or [])
    first_expected = expected_events[0] if expected_events else None
    maturity_expected = next((event for event in expected_events if str(event.get("type", "")).startswith("maturity")), None)
    next_expected = next((event for event in expected_events if event.get("date") and str(event["date"]) >= datetime.now().date().isoformat()), None)
    brochure_doc = next(
        (
            row
            for row in dashboard_v2["documents"]
            if row["document_type"] == "structured_brochure" and asset.isin and row.get("asset_id") == asset.isin
        ),
        None,
    )

    with connect(db_path) as conn:
        document_rows = conn.execute(
            """
            SELECT document_id, document_type, insurer, contract_name, asset_id, document_date,
                   coverage_year, status, filepath, original_filename
            FROM documents
            WHERE contract_name = ?
               OR asset_id IN ({placeholders})
            ORDER BY COALESCE(document_date, '') DESC, original_filename
            """.format(placeholders=", ".join("?" for _ in related_asset_ids)),
            (position.wrapper.contract_name, *sorted(related_asset_ids)),
        ).fetchall()
        rule_row = conn.execute(
            """
            SELECT display_name_override, isin_override, rule_source_mode, coupon_payment_mode,
                   coupon_frequency, coupon_rule_summary, autocall_rule_summary,
                   capital_rule_summary, brochure_document_id, notes
            FROM structured_product_rules
            WHERE asset_id = ?
            """,
            (asset.asset_id,),
        ).fetchone()
        validation_rows = conn.execute(
            """
            SELECT event_key, event_type, event_date, validation_status, notes
            FROM structured_event_validations
            WHERE asset_id = ?
            ORDER BY event_date, event_key
            """,
            (asset.asset_id,),
        ).fetchall()

    event_validations = {
        str(row["event_key"]): {
            "validation_status": row["validation_status"],
            "notes": row["notes"],
        }
        for row in validation_rows
    }
    structured_rule_form = {
        "display_name_override": rule_row["display_name_override"] if rule_row else asset.name,
        "isin_override": rule_row["isin_override"] if rule_row else asset.isin,
        "rule_source_mode": rule_row["rule_source_mode"] if rule_row else "mixed",
        "coupon_payment_mode": rule_row["coupon_payment_mode"] if rule_row else "unknown",
        "coupon_frequency": rule_row["coupon_frequency"] if rule_row else (
            "Semestriel" if first_expected and "semester" in (first_expected.get("metadata") or {}) else ""
        ),
        "coupon_rule_summary": rule_row["coupon_rule_summary"] if rule_row else ((first_expected or {}).get("description") or ""),
        "autocall_rule_summary": rule_row["autocall_rule_summary"] if rule_row else (
            ((first_expected or {}).get("metadata") or {}).get("autocall_condition") or ""
        ),
        "capital_rule_summary": rule_row["capital_rule_summary"] if rule_row else (
            ((maturity_expected or {}).get("description") or "")
        ),
        "brochure_document_id": rule_row["brochure_document_id"] if rule_row else (brochure_doc or {}).get("document_id"),
        "notes": rule_row["notes"] if rule_row else "",
    }
    expected_events_with_validation = []
    for event in expected_events:
        event_key = f"{event.get('type')}::{event.get('date')}::{(event.get('description') or '')[:60]}"
        expected_events_with_validation.append(
            {
                **event,
                "event_key": event_key,
                "validation_status": event_validations.get(event_key, {}).get("validation_status", "unknown"),
                "validation_notes": event_validations.get(event_key, {}).get("notes"),
            }
        )
    return {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "position_id": position_id,
            "db_path": str(db_path),
        },
        "import_summary": import_summary,
        "contract": contract_card,
        "position": {
            "position_id": position.position_id,
            "holder_type": position.holder_type.value,
            "contract_name": position.wrapper.contract_name,
            "insurer": position.wrapper.insurer,
            "wrapper_type": position.wrapper.wrapper_type.value,
            "subscription_date": position.investment.subscription_date.isoformat(),
            "invested_amount": position.investment.invested_amount,
            "units_held": position.investment.units_held,
            "purchase_nav": position.investment.purchase_nav,
            "purchase_nav_source": position.investment.purchase_nav_source,
            "lots": list(position.investment.lots or []),
        },
        "asset": {
            "asset_id": asset.asset_id,
            "asset_type": asset.asset_type.value,
            "name": asset.name,
            "valuation_engine": asset.valuation_engine.value,
            "isin": asset.isin,
            "metadata": dict(asset.metadata or {}),
        },
        "current": current_row,
        "structured_rule": structured_row,
        "structured_summary": {
            "has_brochure": brochure_doc is not None,
            "brochure_document": brochure_doc,
            "has_events_file": events_file.exists(),
            "events_filename": events_file.name if events_file.exists() else None,
            "expected_events_count": len(expected_events),
            "observed_events_count": len(observed_events),
            "first_expected_date": first_expected.get("date") if first_expected else None,
            "next_expected_date": next_expected.get("date") if next_expected else None,
            "maturity_date": maturity_expected.get("date") if maturity_expected else None,
            "gain_per_period": (first_expected or {}).get("metadata", {}).get("gain_per_semester"),
            "underlying": (first_expected or {}).get("metadata", {}).get("underlying"),
            "completeness": {
                "isin_present": bool(asset.isin),
                "brochure_present": brochure_doc is not None,
                "events_file_present": events_file.exists(),
                "rule_status": structured_row["rule_status"] if structured_row else "n/a",
            },
            "expected_events": expected_events_with_validation,
            "observed_events": observed_events,
        },
        "structured_rule_form": structured_rule_form,
        "snapshots": dashboard_v2["snapshots_by_contract"].get(position.wrapper.contract_name, []),
        "documents": [
            {
                "document_id": row["document_id"],
                "document_type": row["document_type"],
                "insurer": row["insurer"],
                "contract_name": row["contract_name"],
                "asset_id": row["asset_id"],
                "document_date": row["document_date"],
                "coverage_year": row["coverage_year"],
                "status": row["status"],
                "filepath": row["filepath"],
                "original_filename": row["original_filename"],
            }
            for row in document_rows
        ],
    }
