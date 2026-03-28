"""GED V2 filtrable."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .bootstrap import bootstrap_v2_data
from .storage import connect, default_db_path


def build_v2_ged_data(
    data_dir: Path,
    *,
    db_path: Path | None = None,
    contract_name: str | None = None,
    document_type: str | None = None,
    year: int | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    data_dir = Path(data_dir)
    db_path = Path(db_path or default_db_path(data_dir))
    import_summary = bootstrap_v2_data(data_dir, db_path=db_path)

    filters = []
    params: list[Any] = []
    if contract_name:
        filters.append("contract_name = ?")
        params.append(contract_name)
    if document_type:
        filters.append("document_type = ?")
        params.append(document_type)
    if year is not None:
        filters.append("(coverage_year = ? OR substr(COALESCE(document_date, ''), 1, 4) = ?)")
        params.extend([year, str(year)])
    if status:
        filters.append("status = ?")
        params.append(status)

    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""

    with connect(db_path) as conn:
        document_rows = conn.execute(
            f"""
            SELECT document_id, document_type, insurer, contract_name, asset_id, document_date,
                   coverage_year, status, filepath, original_filename
            FROM documents
            {where_clause}
            ORDER BY COALESCE(document_date, '') DESC, original_filename
            """,
            params,
        ).fetchall()
        contract_rows = conn.execute(
            "SELECT DISTINCT contract_name FROM documents WHERE contract_name IS NOT NULL ORDER BY contract_name"
        ).fetchall()
        type_rows = conn.execute(
            "SELECT DISTINCT document_type FROM documents ORDER BY document_type"
        ).fetchall()
        status_rows = conn.execute(
            "SELECT DISTINCT status FROM documents ORDER BY status"
        ).fetchall()
        year_rows = conn.execute(
            """
            SELECT DISTINCT COALESCE(coverage_year, CAST(substr(document_date, 1, 4) AS INTEGER)) AS document_year
            FROM documents
            WHERE coverage_year IS NOT NULL OR document_date IS NOT NULL
            ORDER BY document_year DESC
            """
        ).fetchall()

    documents = [
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
            "db_path": str(db_path),
        },
        "import_summary": import_summary,
        "filters": {
            "contract_name": contract_name,
            "document_type": document_type,
            "year": year,
            "status": status,
        },
        "options": {
            "contract_names": [row["contract_name"] for row in contract_rows],
            "document_types": [row["document_type"] for row in type_rows],
            "statuses": [row["status"] for row in status_rows],
            "years": [row["document_year"] for row in year_rows if row["document_year"] is not None],
        },
        "documents": documents,
    }
