from datetime import datetime
from pathlib import Path
import json
import threading
from urllib.request import Request, urlopen

import pytest
import yaml

from portfolio_tracker.v2.document_ingest import ingest_uploaded_document
from portfolio_tracker.web import create_server


def _write_yaml(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _seed_minimal_contract_indexes(data_dir: Path) -> None:
    _write_yaml(
        data_dir / "documents" / "insurer" / "swisslife" / "index.yaml",
        {
            "documents": [
                {
                    "document_id": "swisslife_releve_situation_2025_02_21",
                    "document_type": "insurer_statement",
                    "insurer": "SwissLife",
                    "contract_name": "SwissLife Capi Stratégic Premium",
                    "statement_date": "2025-02-21",
                    "coverage_year": 2024,
                    "status": "active",
                    "filepath": "documents/insurer/swisslife/releves/2025-02-21_releve_situation_swisslife.pdf",
                    "original_filename": "existing.pdf",
                    "sha256": "abc",
                }
            ]
        },
    )
    _write_yaml(
        data_dir / "documents" / "insurer" / "swisslife" / "courriers" / "index.yaml",
        {
            "documents": [
                {
                    "document_id": "swisslife_arbitrage_2025_01_07",
                    "document_type": "arbitration_letter",
                    "insurer": "SwissLife",
                    "contract_name": "SwissLife Capi Stratégic Premium",
                    "document_date": "2025-01-07",
                    "status": "active",
                    "filepath": "documents/insurer/swisslife/courriers/arbitrages/2025-01-07_arbitrage_swisslife.pdf",
                    "original_filename": "existing_arbitrage.pdf",
                    "sha256": "def",
                }
            ]
        },
    )


def test_ingest_uploaded_document_routes_statement_into_contract_index(tmp_path):
    data_dir = tmp_path
    _seed_minimal_contract_indexes(data_dir)

    result = ingest_uploaded_document(
        data_dir,
        file_bytes=b"%PDF-1.4\nfake statement\n",
        original_filename="SwissLife - Releve de situation - mars 2026.pdf",
        contract_name="SwissLife Capi Stratégic Premium",
        document_date="2026-03-27",
        now=datetime(2026, 3, 27, 10, 30, 0),
    )

    assert result["ok"] is True
    assert result["duplicate"] is False
    assert result["classification"]["document_type"] == "insurer_statement"
    assert result["classification"]["source"] in {"heuristic", "llm"}
    assert result["storage"]["path"] == "documents/insurer/swisslife/releves/2026-03-27_releve_situation_swisslife.pdf"
    assert (data_dir / result["storage"]["path"]).exists()

    payload = yaml.safe_load(
        (data_dir / "documents" / "insurer" / "swisslife" / "index.yaml").read_text(encoding="utf-8")
    )
    uploaded = next(doc for doc in payload["documents"] if doc["document_id"] == "swisslife_releve_situation_2026_03_27")
    assert uploaded["statement_date"] == "2026-03-27"
    assert uploaded["contract_name"] == "SwissLife Capi Stratégic Premium"
    assert uploaded["filepath"] == "documents/insurer/swisslife/releves/2026-03-27_releve_situation_swisslife.pdf"


def test_web_upload_endpoint_accepts_multipart_and_indexes_brochure(tmp_path):
    data_dir = tmp_path
    (data_dir / "product_brochure").mkdir(parents=True, exist_ok=True)

    try:
        server = create_server(data_dir, host="127.0.0.1", port=0)
    except PermissionError:
        pytest.skip("Ouverture de port interdite dans ce sandbox")

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    boundary = "----PortfolioTrackerBoundary"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="BROCHURE D RENDEMENT FEV 2025 FR001400TBR1.pdf"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
    ).encode("utf-8") + b"%PDF-1.4\nfake brochure\n" + (
        f"\r\n--{boundary}--\r\n"
    ).encode("utf-8")

    try:
        port = server.server_address[1]
        request = Request(
            f"http://127.0.0.1:{port}/api/documents/upload",
            data=body,
            method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))

        assert payload["ok"] is True
        assert payload["classification"]["document_type"] == "structured_brochure"
        assert payload["storage"]["index_path"] == "product_brochure/index.yaml"
        assert (data_dir / payload["storage"]["path"]).exists()

        brochure_index = yaml.safe_load((data_dir / "product_brochure" / "index.yaml").read_text(encoding="utf-8"))
        assert brochure_index["documents"][0]["document_type"] == "structured_brochure"
        assert brochure_index["documents"][0]["original_filename"] == "BROCHURE D RENDEMENT FEV 2025 FR001400TBR1.pdf"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
