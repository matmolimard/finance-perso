"""Import minimal de données réelles dans le socle V2."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any

from pypdf import PdfReader
import yaml

from ..domain.movements import MovementNormalizer
from .runtime import V2Runtime
from .storage import connect, default_db_path, init_db


CONTRACT_SEEDS: list[dict[str, Any]] = [
    {
        "contract_id": "83914927",
        "contract_name": "HIMALIA",
        "insurer": "Generali",
        "holder_type": "individual",
        "fiscal_applicability": "applicable",
        "status": "active",
        "notes": "Contrat personnel HIMALIA.",
    },
    {
        "contract_id": "0010645288001",
        "contract_name": "SwissLife Capi Stratégic Premium",
        "insurer": "SwissLife",
        "holder_type": "holding",
        "fiscal_applicability": "not_applicable",
        "status": "active",
        "notes": "Contrat SwissLife détenu via holding.",
    },
]


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _to_amount(raw: str) -> float:
    cleaned = raw.replace("\xa0", " ").replace("€", "").strip()
    cleaned = cleaned.replace(" ", "").replace(",", ".")
    return float(cleaned)


def _pdf_text(path: Path) -> str:
    reader = PdfReader(str(path))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _match_amount(text: str, patterns: list[str]) -> float | None:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            return _to_amount(match.group(1))
    return None


def _parse_statement_snapshot(doc: dict[str, Any], data_dir: Path) -> dict[str, Any]:
    pdf_path = Path(data_dir) / str(doc["filepath"])
    text = _pdf_text(pdf_path)
    insurer = str(doc["insurer"])
    coverage_year = int(doc["coverage_year"])

    if insurer.lower().startswith("swiss"):
        total = _match_amount(
            text,
            [
                r"Montant\s*de\s*l['’]épargne\s*(?:\(\*\))?\s*([0-9 \xa0]+,\d{2})\s*€",
            ],
        )
        uc_value = _match_amount(
            text,
            [
                r"Epargne\s*investie\s*en\s*unités\s*de\s*compte\s*([0-9 \xa0]+,\d{2})\s*€",
            ],
        )
        euro_value = _match_amount(
            text,
            [
                r"Epargne\s*investie\s*sur\s*le\s*fonds\s*en\s*euros\s*([0-9 \xa0]+,\d{2})\s*€",
            ],
        )
        euro_interest = _match_amount(
            text,
            [
                r"titre\s*de\s*l['’]année\s*%s\s*([0-9 \xa0]+,\d{2})\s*€" % coverage_year,
            ],
        )
    else:
        total = _match_amount(
            text,
            [
                r"EPARGNE ATTEINTE DE VOTRE CONTRAT AU 31/12/%s\s*([0-9 \xa0]+,\d{2})\s*€"
                % coverage_year,
            ],
        )
        euro_match = re.search(
            r"Actif Général Generali Vie\s+31/12/%s\s+([0-9 \xa0]+,\d{2})\s*€\s+([0-9 \xa0]+,\d{2})\s*€"
            % coverage_year,
            text,
            re.IGNORECASE | re.MULTILINE,
        )
        euro_interest = _to_amount(euro_match.group(1)) if euro_match else None
        euro_value = _to_amount(euro_match.group(2)) if euro_match else None
        uc_value = round(float(total or 0) - float(euro_value or 0), 2) if total is not None and euro_value is not None else None

    if total is None:
        raise ValueError(f"Impossible d'extraire le snapshot du relevé {pdf_path.name}")

    return {
        "snapshot_id": f"{_slug(doc['contract_name'])}_{coverage_year}",
        "contract_name": doc["contract_name"],
        "coverage_year": coverage_year,
        "reference_date": f"{coverage_year}-12-31",
        "statement_date": doc.get("statement_date") or doc.get("document_date"),
        "source_document_id": doc["document_id"],
        "status": "validated",
        "official_total_value": total,
        "official_uc_value": uc_value,
        "official_fonds_euro_value": euro_value,
        "official_euro_interest_net": euro_interest,
        "official_notes": f"Snapshot importé depuis le relevé assureur {Path(doc['filepath']).name}.",
    }


def _import_contract_seeds(conn) -> int:
    count = 0
    for seed in CONTRACT_SEEDS:
        conn.execute(
            """
            INSERT INTO contracts (
                contract_id, contract_name, insurer, holder_type, fiscal_applicability,
                status, external_contributions_total, external_withdrawals_total, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(contract_id) DO UPDATE SET
                contract_name = excluded.contract_name,
                insurer = excluded.insurer,
                holder_type = excluded.holder_type,
                fiscal_applicability = excluded.fiscal_applicability,
                status = excluded.status,
                notes = excluded.notes
            """,
            (
                seed["contract_id"],
                seed["contract_name"],
                seed["insurer"],
                seed["holder_type"],
                seed["fiscal_applicability"],
                seed["status"],
                0.0,
                0.0,
                seed["notes"],
            ),
        )
        count += 1
    return count


def _import_external_flow_snapshots(conn, data_dir: Path) -> None:
    snapshot_files = [
        Path(data_dir) / "market_data" / "contract_snapshots_himalia.yaml",
        Path(data_dir) / "market_data" / "contract_snapshots_swisslife.yaml",
    ]
    for path in snapshot_files:
        if not path.exists():
            continue
        data = _load_yaml(path)
        contract_id = str(data.get("contract_id") or "")
        conn.execute(
            """
            UPDATE contracts
            SET external_contributions_total = ?, external_withdrawals_total = ?
            WHERE contract_id = ?
            """,
            (
                float(data.get("versements_total") or 0.0),
                float(data.get("retraits_total") or 0.0),
                contract_id,
            ),
        )
        conn.execute("DELETE FROM contract_external_flows WHERE contract_id = ?", (contract_id,))
        flows = data.get("flux_externes_par_annee") or {}
        for year, contributions in flows.items():
            conn.execute(
                """
                INSERT INTO contract_external_flows (
                    contract_id, flow_year, contributions_total, withdrawals_total
                ) VALUES (?, ?, ?, ?)
                """,
                (contract_id, int(year), float(contributions or 0.0), 0.0),
            )


def _import_documents_from_index(conn, data_dir: Path, index_path: Path) -> int:
    if not index_path.exists():
        return 0
    payload = _load_yaml(index_path)
    imported_at = datetime.now().isoformat(timespec="seconds")
    count = 0
    for doc in payload.get("documents", []):
        document_date = doc.get("document_date") or doc.get("statement_date")
        conn.execute(
            """
            INSERT INTO documents (
                document_id, document_type, insurer, contract_name, asset_id, document_date,
                coverage_year, status, filepath, original_filename, sha256, notes, imported_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(document_id) DO UPDATE SET
                document_type = excluded.document_type,
                insurer = excluded.insurer,
                contract_name = excluded.contract_name,
                asset_id = excluded.asset_id,
                document_date = excluded.document_date,
                coverage_year = excluded.coverage_year,
                status = excluded.status,
                filepath = excluded.filepath,
                original_filename = excluded.original_filename,
                sha256 = excluded.sha256,
                notes = excluded.notes,
                imported_at = excluded.imported_at
            """,
            (
                doc["document_id"],
                doc["document_type"],
                doc["insurer"],
                doc.get("contract_name"),
                doc.get("asset_id"),
                document_date,
                doc.get("coverage_year"),
                doc.get("status", "active"),
                str(doc["filepath"]),
                doc.get("original_filename"),
                doc.get("sha256"),
                doc.get("notes"),
                imported_at,
            ),
        )
        count += 1
    return count


def _import_brochures(conn, data_dir: Path) -> int:
    brochure_dir = Path(data_dir) / "product_brochure"
    brochure_index = brochure_dir / "index.yaml"
    imported_at = datetime.now().isoformat(timespec="seconds")
    count = 0
    indexed_files: set[str] = set()

    if brochure_index.exists():
        payload = _load_yaml(brochure_index)
        for doc in payload.get("documents", []):
            doc_id = str(doc.get("document_id") or f"structured_brochure_{_slug(Path(str(doc.get('filepath') or '')).stem)}")
            filepath = str(doc.get("filepath") or "")
            if filepath:
                indexed_files.add(Path(filepath).name)
            conn.execute(
                """
                INSERT INTO documents (
                    document_id, document_type, insurer, contract_name, asset_id, document_date,
                    coverage_year, status, filepath, original_filename, sha256, notes, imported_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(document_id) DO UPDATE SET
                    asset_id = excluded.asset_id,
                    filepath = excluded.filepath,
                    original_filename = excluded.original_filename,
                    sha256 = excluded.sha256,
                    notes = excluded.notes,
                    imported_at = excluded.imported_at
                """,
                (
                    doc_id,
                    str(doc.get("document_type") or "structured_brochure"),
                    str(doc.get("insurer") or "Mixed"),
                    doc.get("contract_name"),
                    doc.get("asset_id"),
                    doc.get("document_date"),
                    doc.get("coverage_year"),
                    str(doc.get("status") or "active"),
                    filepath,
                    doc.get("original_filename"),
                    doc.get("sha256"),
                    doc.get("notes") or "Brochure de produit structuré importée depuis index.yaml.",
                    imported_at,
                ),
            )
            count += 1

    for brochure in sorted(brochure_dir.glob("*.pdf")):
        if brochure.name in indexed_files:
            continue
        doc_id = f"structured_brochure_{_slug(brochure.stem)}"
        isin_match = re.search(r"(FR[A-Z0-9]{10}|LU[A-Z0-9]{10})", brochure.name)
        asset_id = isin_match.group(1) if isin_match else None
        conn.execute(
            """
            INSERT INTO documents (
                document_id, document_type, insurer, contract_name, asset_id, document_date,
                coverage_year, status, filepath, original_filename, sha256, notes, imported_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(document_id) DO UPDATE SET
                asset_id = excluded.asset_id,
                filepath = excluded.filepath,
                original_filename = excluded.original_filename,
                imported_at = excluded.imported_at
            """,
            (
                doc_id,
                "structured_brochure",
                "Mixed",
                None,
                asset_id,
                None,
                None,
                "active",
                str(Path("product_brochure") / brochure.name),
                brochure.name,
                None,
                "Brochure de produit structuré importée automatiquement.",
                imported_at,
            ),
        )
        count += 1
    return count


def _import_statement_snapshots(conn, data_dir: Path) -> tuple[int, list[str]]:
    rows = conn.execute(
        """
        SELECT document_id, insurer, contract_name, document_date, coverage_year, filepath
        FROM documents
        WHERE document_type = 'insurer_statement'
          AND coverage_year IS NOT NULL
        ORDER BY contract_name, coverage_year
        """
    ).fetchall()
    imported_at = datetime.now().isoformat(timespec="seconds")
    count = 0
    warnings: list[str] = []
    for row in rows:
        try:
            snapshot = _parse_statement_snapshot(dict(row), data_dir)
        except Exception as exc:
            warnings.append(f"{row['document_id']}: {exc}")
            continue
        contract_id_row = conn.execute(
            "SELECT contract_id FROM contracts WHERE contract_name = ?",
            (snapshot["contract_name"],),
        ).fetchone()
        if contract_id_row is None:
            continue
        conn.execute(
            """
            INSERT INTO annual_snapshots (
                snapshot_id, contract_id, contract_name, reference_date, statement_date,
                source_document_id, status, official_total_value, official_uc_value,
                official_fonds_euro_value, official_euro_interest_net, official_notes, imported_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(snapshot_id) DO UPDATE SET
                statement_date = excluded.statement_date,
                source_document_id = excluded.source_document_id,
                status = excluded.status,
                official_total_value = excluded.official_total_value,
                official_uc_value = excluded.official_uc_value,
                official_fonds_euro_value = excluded.official_fonds_euro_value,
                official_euro_interest_net = excluded.official_euro_interest_net,
                official_notes = excluded.official_notes,
                imported_at = excluded.imported_at
            """,
            (
                snapshot["snapshot_id"],
                contract_id_row["contract_id"],
                snapshot["contract_name"],
                snapshot["reference_date"],
                snapshot["statement_date"],
                snapshot["source_document_id"],
                snapshot["status"],
                snapshot["official_total_value"],
                snapshot["official_uc_value"],
                snapshot["official_fonds_euro_value"],
                snapshot["official_euro_interest_net"],
                snapshot["official_notes"],
                imported_at,
            ),
        )
        count += 1
    return count, warnings


def _bucket_for_asset_type(asset_type: str) -> str | None:
    if asset_type == "fonds_euro":
        return "fonds_euro"
    if asset_type in {"uc_fund", "uc_illiquid"}:
        return "uc"
    if asset_type == "structured_product":
        return "structured"
    return None


def _ledger_entry_kind(*, bucket: str, movement) -> str:
    if movement.movement_kind.value == "external_contribution":
        return "external_contribution"
    if movement.movement_kind.value == "internal_capitalization":
        return "internal_credit"
    if movement.movement_kind.value == "fee":
        return "fee"
    if movement.movement_kind.value == "tax":
        if bucket == "structured" and (movement.units_delta or 0) < -1:
            return "structured_redemption"
        return "tax"
    if movement.movement_kind.value == "withdrawal":
        if bucket == "structured":
            return "structured_redemption"
        return "withdrawal"
    return "other"


def _is_transfer_candidate(entry: dict[str, Any]) -> bool:
    return entry["direction"] in {"credit", "debit"} and entry["entry_kind"] in {
        "external_contribution",
        "withdrawal",
        "structured_redemption",
        "other",
    }


def _copy_entry(entry: dict[str, Any], *, suffix: str, amount: float, entry_kind: str) -> dict[str, Any]:
    copied = dict(entry)
    copied["entry_id"] = f"{entry['entry_id']}::{suffix}"
    copied["amount"] = round(float(amount), 2)
    copied["entry_kind"] = entry_kind
    return copied


def _reconcile_internal_transfers(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for entry in entries:
        grouped.setdefault((entry["contract_id"], entry["entry_date"]), []).append(entry)

    reconciled: list[dict[str, Any]] = []
    for (_contract_id, _entry_date), group in grouped.items():
        candidates = [dict(entry, remaining=float(entry["amount"])) for entry in group if _is_transfer_candidate(entry)]
        others = [entry for entry in group if not _is_transfer_candidate(entry)]

        debits = [entry for entry in candidates if entry["direction"] == "debit"]
        credits = [entry for entry in candidates if entry["direction"] == "credit"]

        split_index = 0
        for debit in debits:
            for credit in credits:
                if debit["remaining"] <= 0.005 or credit["remaining"] <= 0.005:
                    continue
                if debit["bucket"] == credit["bucket"]:
                    continue
                matched = min(float(debit["remaining"]), float(credit["remaining"]))
                if matched <= 0.005:
                    continue
                split_index += 1
                reconciled.append(
                    _copy_entry(
                        debit,
                        suffix=f"transfer_out_{split_index}",
                        amount=matched,
                        entry_kind="internal_transfer_out",
                    )
                )
                reconciled.append(
                    _copy_entry(
                        credit,
                        suffix=f"transfer_in_{split_index}",
                        amount=matched,
                        entry_kind="internal_transfer_in",
                    )
                )
                debit["remaining"] -= matched
                credit["remaining"] -= matched

        for entry in candidates:
            if entry["remaining"] > 0.005:
                reconciled.append(
                    _copy_entry(
                        entry,
                        suffix="residual",
                        amount=entry["remaining"],
                        entry_kind=entry["entry_kind"],
                    )
                )

        reconciled.extend(others)

    return reconciled


def _import_contract_ledger_entries(conn, data_dir: Path) -> int:
    runtime = V2Runtime(data_dir)
    normalizer = MovementNormalizer()
    imported_at = datetime.now().isoformat(timespec="seconds")
    count = 0
    raw_entries: list[dict[str, Any]] = []

    contract_ids = {
        str(row["contract_name"]): str(row["contract_id"])
        for row in conn.execute("SELECT contract_id, contract_name FROM contracts").fetchall()
    }

    for position in runtime.portfolio.list_all_positions():
        asset = runtime.portfolio.get_asset(position.asset_id)
        if asset is None:
            continue
        bucket = _bucket_for_asset_type(asset.asset_type.value)
        if bucket is None:
            continue

        contract_name = str(position.wrapper.contract_name or "")
        contract_id = contract_ids.get(contract_name)
        if not contract_id:
            continue

        movements = normalizer.normalize_lots(
            position_id=position.position_id,
            asset_id=position.asset_id,
            lots=position.investment.lots or [],
        )
        for movement in movements:
            if abs(float(movement.cash_amount or 0.0)) < 0.00001 and movement.units_delta is None:
                continue

            direction = "credit" if movement.cash_amount > 0 else "debit" if movement.cash_amount < 0 else "neutral"
            amount = abs(float(movement.cash_amount or 0.0))
            raw_entries.append(
                {
                    "entry_id": f"entry_{movement.movement_id}",
                    "contract_id": contract_id,
                    "contract_name": contract_name,
                    "position_id": position.position_id,
                    "asset_id": position.asset_id,
                    "asset_name": asset.name,
                    "bucket": bucket,
                    "entry_date": movement.effective_date.isoformat(),
                    "fiscal_year": movement.effective_date.year,
                    "direction": direction,
                    "amount": amount,
                    "units_delta": movement.units_delta,
                    "movement_kind": movement.movement_kind.value,
                    "entry_kind": _ledger_entry_kind(bucket=bucket, movement=movement),
                    "raw_lot_type": movement.raw_lot_type,
                    "external_flag": 1 if movement.external is True else 0 if movement.external is False else None,
                    "source_movement_id": movement.movement_id,
                    "raw_lot_json": json.dumps(movement.raw_lot, ensure_ascii=False, sort_keys=True, default=str),
                    "imported_at": imported_at,
                }
            )

    conn.execute("DELETE FROM contract_ledger_entries")
    for entry in _reconcile_internal_transfers(raw_entries):
        conn.execute(
            """
            INSERT INTO contract_ledger_entries (
                entry_id, contract_id, contract_name, position_id, asset_id, asset_name, bucket,
                entry_date, fiscal_year, direction, amount, units_delta, movement_kind, entry_kind,
                raw_lot_type, external_flag, source_movement_id, raw_lot_json, imported_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry["entry_id"],
                entry["contract_id"],
                entry["contract_name"],
                entry["position_id"],
                entry["asset_id"],
                entry["asset_name"],
                entry["bucket"],
                entry["entry_date"],
                entry["fiscal_year"],
                entry["direction"],
                entry["amount"],
                entry["units_delta"],
                entry["movement_kind"],
                entry["entry_kind"],
                entry["raw_lot_type"],
                entry["external_flag"],
                entry["source_movement_id"],
                entry["raw_lot_json"],
                entry["imported_at"],
            ),
        )
        count += 1
    return count


def bootstrap_v2_data(data_dir: Path, db_path: Path | None = None) -> dict[str, Any]:
    data_dir = Path(data_dir)
    db_path = Path(db_path or default_db_path(data_dir))
    init_db(db_path)

    indexes = sorted((data_dir / "documents" / "insurer").glob("**/index.yaml"))

    with connect(db_path) as conn:
        contracts_count = _import_contract_seeds(conn)
        _import_external_flow_snapshots(conn, data_dir)
        documents_count = 0
        for index_path in indexes:
            documents_count += _import_documents_from_index(conn, data_dir, index_path)
        brochures_count = _import_brochures(conn, data_dir)
        snapshots_count, snapshot_warnings = _import_statement_snapshots(conn, data_dir)
        ledger_entries_count = _import_contract_ledger_entries(conn, data_dir)
        totals = {
            "contracts": conn.execute("SELECT COUNT(*) AS c FROM contracts").fetchone()["c"],
            "documents": conn.execute("SELECT COUNT(*) AS c FROM documents").fetchone()["c"],
            "snapshots": conn.execute("SELECT COUNT(*) AS c FROM annual_snapshots").fetchone()["c"],
            "ledger_entries": conn.execute("SELECT COUNT(*) AS c FROM contract_ledger_entries").fetchone()["c"],
        }

    return {
        "ok": True,
        "db_path": str(db_path),
        "imported": {
            "contracts_seeded": contracts_count,
            "documents_indexed": documents_count,
            "brochures_indexed": brochures_count,
            "snapshots_imported": snapshots_count,
            "ledger_entries_imported": ledger_entries_count,
        },
        "totals": totals,
        "warnings": {
            "statement_snapshots": snapshot_warnings,
        },
        "imported_at": datetime.now().isoformat(timespec="seconds"),
    }
