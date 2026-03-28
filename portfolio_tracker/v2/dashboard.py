"""Assemblage du premier dashboard V2."""

from __future__ import annotations

from datetime import datetime, date
from pathlib import Path
from typing import Any

from .bootstrap import bootstrap_v2_data
from .runtime import V2Runtime
from .storage import connect, default_db_path
from .structured_summary import build_structured_summary_rows


def _group_current_contract_metrics(runtime: V2Runtime) -> dict[str, dict[str, Any]]:
    dashboard = runtime.build_web_payload(include_terminated=False)

    opening_dates: dict[str, date] = {}
    for position in runtime.portfolio.list_all_positions():
        contract_name = str(position.wrapper.contract_name or "Sans contrat")
        sub_date = position.investment.subscription_date
        if sub_date and (contract_name not in opening_dates or sub_date < opening_dates[contract_name]):
            opening_dates[contract_name] = sub_date

    grouped: dict[str, dict[str, Any]] = {}
    for tab in ("fonds_euro", "uc", "structured"):
        for row in dashboard["views"].get(tab, []):
            contract_name = str(row.get("contract_name") or "Sans contrat")
            contract = grouped.setdefault(
                contract_name,
                {
                    "current_value": 0.0,
                    "active_positions_count": 0,
                    "by_type": {"fonds_euro": 0.0, "uc": 0.0, "structured": 0.0},
                    "opening_date": opening_dates.get(contract_name),
                },
            )
            contract["current_value"] += float(row.get("current_value") or 0.0)
            contract["active_positions_count"] += 1
            contract["by_type"][tab] += float(row.get("current_value") or 0.0)
    return grouped


def _compute_internal_transfer_adjustments(db_path: Path) -> dict[str, dict[int, dict[str, dict[str, float]]]]:
    adjustments: dict[str, dict[int, dict[str, dict[str, float]]]] = {}

    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT contract_name, fiscal_year, entry_date, bucket, direction, amount
            FROM contract_ledger_entries
            WHERE bucket IN ('uc', 'fonds_euro', 'structured')
              AND entry_kind IN ('internal_transfer_in', 'internal_transfer_out')
              AND direction IN ('credit', 'debit')
            ORDER BY contract_name, fiscal_year, entry_date
            """
        ).fetchall()

    events_by_contract_year_date: dict[str, dict[int, dict[str, list[dict[str, Any]]]]] = {}
    for row in rows:
        contract_name = str(row["contract_name"] or "")
        year = int(row["fiscal_year"])
        entry_date = str(row["entry_date"])
        date_bucket = events_by_contract_year_date.setdefault(contract_name, {}).setdefault(year, {}).setdefault(entry_date, [])
        signed_amount = float(row["amount"] or 0.0) * (1 if row["direction"] == "credit" else -1)
        date_bucket.append(
            {
                "bucket": str(row["bucket"]),
                "amount": signed_amount,
            }
        )

    for contract_name, years in events_by_contract_year_date.items():
        for year, dates in years.items():
            buckets = adjustments.setdefault(contract_name, {}).setdefault(
                year,
                {
                    "uc": {"in": 0.0, "out": 0.0},
                    "fonds_euro": {"in": 0.0, "out": 0.0},
                    "structured": {"in": 0.0, "out": 0.0},
                },
            )
            for events in dates.values():
                net_by_bucket: dict[str, float] = {"uc": 0.0, "fonds_euro": 0.0, "structured": 0.0}
                for event in events:
                    net_by_bucket[event["bucket"]] += float(event["amount"])

                inflows = [
                    {"bucket": bucket, "amount": amount}
                    for bucket, amount in net_by_bucket.items()
                    if amount > 0
                ]
                outflows = [
                    {"bucket": bucket, "amount": amount}
                    for bucket, amount in net_by_bucket.items()
                    if amount < 0
                ]
                if not inflows or not outflows:
                    continue

                total_in = sum(event["amount"] for event in inflows)
                total_out = sum(abs(event["amount"]) for event in outflows)
                matched = min(total_in, total_out)
                if matched <= 0:
                    continue

                for event in inflows:
                    buckets[event["bucket"]]["in"] += matched * (event["amount"] / total_in)
                for event in outflows:
                    buckets[event["bucket"]]["out"] += matched * (abs(event["amount"]) / total_out)

    return adjustments


def _compute_structured_snapshot_values(
    runtime: V2Runtime,
    snapshots: list[Any],
) -> dict[str, dict[str, float]]:
    reference_dates_by_contract: dict[str, set[date]] = {}
    for row in snapshots:
        contract_name = str(row["contract_name"])
        reference_date = datetime.fromisoformat(str(row["reference_date"])).date()
        reference_dates_by_contract.setdefault(contract_name, set()).add(reference_date)

    structured_values: dict[str, dict[str, float]] = {}
    for contract_name, ref_dates in reference_dates_by_contract.items():
        positions = [
            position
            for position in runtime.portfolio.list_all_positions()
            if (position.wrapper.contract_name or "") == contract_name
        ]
        for ref_date in sorted(ref_dates):
            total_value = 0.0
            for position in positions:
                asset = runtime.portfolio.get_asset(position.asset_id)
                if asset is None or asset.asset_type.value != "structured_product":
                    continue
                if runtime.analytics_service.is_position_sold(position, valuation_date=ref_date):
                    continue
                engine = runtime.engines.get(asset.valuation_engine)
                if engine is None:
                    continue
                result = engine.valuate(asset, position, ref_date)
                total_value += float(result.current_value or 0.0)
            structured_values.setdefault(contract_name, {})[ref_date.isoformat()] = round(total_value, 2)
    return structured_values


def _structured_coverage(runtime: V2Runtime, data_dir: Path, db_path: Path) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        brochure_rows = conn.execute(
            "SELECT asset_id, original_filename, filepath FROM documents WHERE document_type = 'structured_brochure'"
        ).fetchall()
    brochures_by_isin = {}
    for row in brochure_rows:
        if row["asset_id"]:
            brochures_by_isin[str(row["asset_id"])] = {
                "filename": row["original_filename"],
                "filepath": row["filepath"],
            }

    coverage = []
    for position in runtime.portfolio.list_all_positions():
        asset = runtime.portfolio.get_asset(position.asset_id)
        if asset is None or asset.asset_type.value != "structured_product":
            continue
        if str((asset.metadata or {}).get("status") or "").lower() == "historical":
            continue
        event_file = Path(data_dir) / "market_data" / f"events_{asset.asset_id}.yaml"
        brochure = brochures_by_isin.get(asset.isin or "")
        coverage.append(
            {
                "contract_name": position.wrapper.contract_name,
                "position_id": position.position_id,
                "asset_id": asset.asset_id,
                "asset_name": asset.name,
                "isin": asset.isin,
                "has_brochure": brochure is not None,
                "brochure_filename": brochure["filename"] if brochure else None,
                "has_events_file": event_file.exists(),
                "events_filename": event_file.name if event_file.exists() else None,
                "rule_status": (
                    "complete"
                    if brochure is not None and event_file.exists()
                    else "partial"
                    if brochure is not None
                    else "insufficient"
                ),
            }
        )
    coverage.sort(key=lambda row: (row["contract_name"], row["asset_name"]))
    return coverage


def build_v2_dashboard_data(
    data_dir: Path,
    db_path: Path | None = None,
    *,
    bootstrap: bool = True,
) -> dict[str, Any]:
    data_dir = Path(data_dir)
    db_path = Path(db_path or default_db_path(data_dir))
    if bootstrap or not db_path.exists():
        import_summary = bootstrap_v2_data(data_dir, db_path=db_path)
    else:
        import_summary = {
            "ok": True,
            "db_path": str(db_path),
            "imported_at": datetime.now().isoformat(timespec="seconds"),
            "totals": {"contracts": 0, "snapshots": 0, "documents": 0, "ledger_entries": 0},
            "imported": {
                "documents_indexed": 0,
                "brochures_indexed": 0,
                "snapshots_imported": 0,
                "ledger_entries_imported": 0,
            },
        }
    runtime = V2Runtime(data_dir)
    current_metrics = _group_current_contract_metrics(runtime)
    internal_adjustments = _compute_internal_transfer_adjustments(db_path)

    with connect(db_path) as conn:
        contracts = conn.execute(
            """
            SELECT contract_id, contract_name, insurer, holder_type, fiscal_applicability,
                   status, external_contributions_total, external_withdrawals_total, notes
            FROM contracts
            ORDER BY contract_name
            """
        ).fetchall()

        snapshots = conn.execute(
            """
            SELECT snapshot_id, contract_id, contract_name, reference_date, statement_date,
                   official_total_value, official_uc_value, official_fonds_euro_value,
                   official_euro_interest_net, official_notes
            FROM annual_snapshots
            ORDER BY contract_name, reference_date
            """
        ).fetchall()

        documents = conn.execute(
            """
            SELECT contract_name, document_type, COUNT(*) AS count
            FROM documents
            GROUP BY contract_name, document_type
            """
        ).fetchall()
        external_flow_rows = conn.execute(
            """
            SELECT c.contract_name, cef.flow_year, cef.contributions_total, cef.withdrawals_total
            FROM contract_external_flows cef
            JOIN contracts c ON c.contract_id = cef.contract_id
            ORDER BY c.contract_name, cef.flow_year
            """
        ).fetchall()
        ledger_summary_rows = conn.execute(
            """
            SELECT
                contract_name,
                fiscal_year,
                SUM(CASE WHEN entry_kind = 'internal_credit' AND direction = 'credit' THEN amount ELSE 0 END) AS credited_income_total,
                SUM(CASE WHEN entry_kind = 'structured_redemption' AND direction = 'debit' THEN amount ELSE 0 END) AS structured_redemptions_total,
                SUM(CASE WHEN entry_kind = 'fee' AND direction = 'debit' THEN amount ELSE 0 END) AS fees_total,
                SUM(CASE WHEN entry_kind = 'tax' AND direction = 'debit' THEN amount ELSE 0 END) AS taxes_total
            FROM contract_ledger_entries
            GROUP BY contract_name, fiscal_year
            ORDER BY contract_name, fiscal_year
            """
        ).fetchall()
        document_rows = conn.execute(
            """
            SELECT document_id, document_type, insurer, contract_name, asset_id, document_date,
                   coverage_year, status, filepath, original_filename
            FROM documents
            ORDER BY COALESCE(contract_name, ''), COALESCE(document_date, '') DESC, original_filename
            """
        ).fetchall()

    structured_snapshot_values = _compute_structured_snapshot_values(runtime, snapshots)

    documents_by_contract: dict[str, dict[str, int]] = {}
    for row in documents:
        contract_name = str(row["contract_name"] or "Sans contrat")
        documents_by_contract.setdefault(contract_name, {})[str(row["document_type"])] = int(row["count"])

    external_flows_by_contract_year: dict[str, dict[int, dict[str, float]]] = {}
    for row in external_flow_rows:
        external_flows_by_contract_year.setdefault(str(row["contract_name"]), {})[int(row["flow_year"])] = {
            "contributions_total": float(row["contributions_total"] or 0.0),
            "withdrawals_total": float(row["withdrawals_total"] or 0.0),
        }

    ledger_summary_by_contract_year: dict[str, dict[int, dict[str, float]]] = {}
    for row in ledger_summary_rows:
        ledger_summary_by_contract_year.setdefault(str(row["contract_name"]), {})[int(row["fiscal_year"])] = {
            "credited_income_total": float(row["credited_income_total"] or 0.0),
            "structured_redemptions_total": float(row["structured_redemptions_total"] or 0.0),
            "fees_total": float(row["fees_total"] or 0.0),
            "taxes_total": float(row["taxes_total"] or 0.0),
        }

    snapshots_by_contract: dict[str, list[dict[str, Any]]] = {}
    for row in snapshots:
        contract_name = str(row["contract_name"])
        reference_date = str(row["reference_date"])
        reference_year = int(reference_date[:4])
        total_value = float(row["official_total_value"] or 0.0)
        euro_value = float(row["official_fonds_euro_value"] or 0.0)
        uc_gross_value = float(row["official_uc_value"] or 0.0)
        structured_value = float(structured_snapshot_values.get(contract_name, {}).get(reference_date) or 0.0)
        uc_pure_value = round(max(total_value - euro_value - structured_value, 0.0), 2)
        external_flow_summary = external_flows_by_contract_year.get(contract_name, {}).get(
            reference_year,
            {"contributions_total": 0.0, "withdrawals_total": 0.0},
        )
        ledger_flow_summary = ledger_summary_by_contract_year.get(contract_name, {}).get(
            reference_year,
            {
                "credited_income_total": 0.0,
                "structured_redemptions_total": 0.0,
                "fees_total": 0.0,
                "taxes_total": 0.0,
            },
        )
        snapshots_by_contract.setdefault(str(row["contract_name"]), []).append(
            {
                "snapshot_id": row["snapshot_id"],
                "reference_date": reference_date,
                "statement_date": row["statement_date"],
                "official_total_value": total_value,
                "official_uc_gross_value": uc_gross_value,
                "official_uc_value": uc_pure_value,
                "official_structured_value": structured_value,
                "official_fonds_euro_value": euro_value,
                "official_euro_interest_net": float(row["official_euro_interest_net"] or 0.0),
                "official_notes": row["official_notes"],
                "annual_flow_summary": {
                    "external_contributions_total": external_flow_summary["contributions_total"],
                    "external_withdrawals_total": external_flow_summary["withdrawals_total"],
                    "credited_income_total": ledger_flow_summary["credited_income_total"],
                    "structured_redemptions_total": ledger_flow_summary["structured_redemptions_total"],
                    "fees_total": ledger_flow_summary["fees_total"],
                    "taxes_total": ledger_flow_summary["taxes_total"],
                },
                "internal_transfer_adjustments": internal_adjustments.get(contract_name, {}).get(
                    int(reference_date[:4]),
                    {
                        "uc": {"in": 0.0, "out": 0.0},
                        "fonds_euro": {"in": 0.0, "out": 0.0},
                        "structured": {"in": 0.0, "out": 0.0},
                    },
                ),
            }
        )

    today = date.today()
    prev_dec31_str = f"{today.year - 1}-12-31"

    contract_cards = []
    for row in contracts:
        contract_name = str(row["contract_name"])
        current = current_metrics.get(contract_name, {})
        current_value = float(current.get("current_value") or 0.0)
        contributions = float(row["external_contributions_total"] or 0.0)
        withdrawals = float(row["external_withdrawals_total"] or 0.0)
        performance_amount = current_value - contributions + withdrawals
        denominator = contributions if contributions else 0.0
        performance_pct = (performance_amount / denominator * 100.0) if denominator else None
        timeline = snapshots_by_contract.get(contract_name, [])
        last_snapshot = timeline[-1] if timeline else None
        year_progress_amount = None
        year_progress_pct = None
        year_progress_reference_date = None
        if last_snapshot:
            year_progress_reference_date = last_snapshot["reference_date"]
            last_official_value = float(last_snapshot["official_total_value"] or 0.0)
            year_progress_amount = current_value - last_official_value
            year_progress_pct = (year_progress_amount / last_official_value * 100.0) if last_official_value else None

        # % YTD depuis le 31/12 de l'année précédente
        ytd_amount: float | None = None
        ytd_pct: float | None = None
        dec31_snap = next((s for s in timeline if s["reference_date"] == prev_dec31_str), None)
        if dec31_snap:
            dec31_value = float(dec31_snap["official_total_value"] or 0.0)
            ytd_amount = current_value - dec31_value
            ytd_pct = (ytd_amount / dec31_value * 100.0) if dec31_value else None

        # Date d'ouverture et ancienneté
        opening_date: date | None = current.get("opening_date")
        opening_date_str = opening_date.isoformat() if opening_date else None
        months_since_opening: int | None = None
        if opening_date:
            months_since_opening = (today.year - opening_date.year) * 12 + (today.month - opening_date.month)
            if today.day < opening_date.day:
                months_since_opening -= 1

        contract_cards.append(
            {
                "contract_id": row["contract_id"],
                "contract_name": contract_name,
                "insurer": row["insurer"],
                "holder_type": row["holder_type"],
                "fiscal_applicability": row["fiscal_applicability"],
                "current_value": current_value,
                "external_contributions_total": contributions,
                "external_withdrawals_total": withdrawals,
                "performance_simple_amount": performance_amount,
                "performance_simple_pct": performance_pct,
                "active_positions_count": int(current.get("active_positions_count") or 0),
                "current_by_type": current.get("by_type") or {"fonds_euro": 0.0, "uc": 0.0, "structured": 0.0},
                "latest_snapshot": last_snapshot,
                "year_progress_amount": year_progress_amount,
                "year_progress_pct": year_progress_pct,
                "year_progress_reference_date": year_progress_reference_date,
                "ytd_amount": ytd_amount,
                "ytd_pct": ytd_pct,
                "ytd_reference_date": prev_dec31_str if dec31_snap else None,
                "opening_date": opening_date_str,
                "months_since_opening": months_since_opening,
                "snapshot_count": len(timeline),
                "document_counts": documents_by_contract.get(contract_name, {}),
            }
        )

    overview_current_value = sum(card["current_value"] for card in contract_cards)
    overview_contributions = sum(card["external_contributions_total"] for card in contract_cards)
    overview_gain = sum(card["performance_simple_amount"] for card in contract_cards)
    overview_pct = (overview_gain / overview_contributions * 100.0) if overview_contributions else None
    document_list = [
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
    ]

    return {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "data_dir": str(data_dir.resolve()),
            "db_path": import_summary["db_path"],
        },
        "import_summary": import_summary,
        "overview": {
            "current_value": overview_current_value,
            "external_contributions_total": overview_contributions,
            "performance_simple_amount": overview_gain,
            "performance_simple_pct": overview_pct,
            "contracts_count": len(contract_cards),
            "active_positions_count": sum(card["active_positions_count"] for card in contract_cards),
        },
        "contracts": contract_cards,
        "snapshots_by_contract": snapshots_by_contract,
        "structured_summary": build_structured_summary_rows(runtime),
        "structured_coverage": _structured_coverage(runtime, data_dir, db_path),
        "documents_by_contract": documents_by_contract,
        "documents": document_list,
    }
