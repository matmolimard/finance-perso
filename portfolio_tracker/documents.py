"""Détails document pour la V2."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .bootstrap import ensure_v2_db
from .storage import connect, default_db_path


def build_v2_document_detail(data_dir: Path, document_id: str, db_path: Path | None = None) -> dict:
    data_dir = Path(data_dir)
    db_path = Path(db_path or default_db_path(data_dir))
    import_summary = ensure_v2_db(data_dir, db_path=db_path)

    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT document_id, document_type, insurer, contract_name, asset_id, document_date,
                   coverage_year, status, filepath, original_filename, notes
            FROM documents
            WHERE document_id = ?
            """,
            (document_id,),
        ).fetchone()

    if row is None:
        raise KeyError(f"Document introuvable: {document_id}")

    return {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "db_path": str(db_path),
        },
        "import_summary": import_summary,
        "document": {
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
            "notes": row["notes"],
            "file_url": f"/api/documents/{row['document_id']}/file",
        },
    }
