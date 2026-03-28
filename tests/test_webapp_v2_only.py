from pathlib import Path
import threading
from urllib.request import urlopen

import pytest

from portfolio_tracker.web import create_server
from portfolio_tracker.web.app import STATIC_DIR


def test_v2_only_static_assets_exist():
    assert not (STATIC_DIR / "index.html").exists()
    assert not (STATIC_DIR / "styles.css").exists()
    assert not (STATIC_DIR / "app.js").exists()
    assert (STATIC_DIR / "v2.html").exists()
    assert (STATIC_DIR / "v2.css").exists()
    assert (STATIC_DIR / "v2.js").exists()


def test_webapp_v2_no_longer_imports_legacy_cli():
    root = Path(__file__).resolve().parents[1]
    source = (root / "portfolio_tracker" / "web" / "app.py").read_text(encoding="utf-8")
    assert "PortfolioCLI" not in source


def test_root_serves_v2_dashboard():
    root = Path(__file__).resolve().parents[1]
    data_dir = root / "portfolio_tracker" / "data"
    try:
        server = create_server(data_dir, host="127.0.0.1", port=0)
    except PermissionError:
        pytest.skip("Ouverture de port interdite dans ce sandbox")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        port = server.server_address[1]
        with urlopen(f"http://127.0.0.1:{port}/", timeout=30) as response:
            body = response.read().decode("utf-8")

        assert "Vue portefeuille V2" in body
        assert "/static/v2.css" in body
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_v2_document_endpoints_work():
    root = Path(__file__).resolve().parents[1]
    data_dir = root / "portfolio_tracker" / "data"
    try:
        server = create_server(data_dir, host="127.0.0.1", port=0)
    except PermissionError:
        pytest.skip("Ouverture de port interdite dans ce sandbox")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        port = server.server_address[1]
        with urlopen(
            f"http://127.0.0.1:{port}/api/documents/swisslife_releve_situation_2026_02_20",
            timeout=30,
        ) as response:
            payload = response.read().decode("utf-8")

        with urlopen(
            f"http://127.0.0.1:{port}/api/documents/swisslife_releve_situation_2026_02_20/file",
            timeout=30,
        ) as response:
            content_type = response.headers.get_content_type()
            body = response.read(5)

        assert "SwissLife - Releve" in payload
        assert "/api/documents/swisslife_releve_situation_2026_02_20/file" in payload
        assert content_type == "application/pdf"
        assert body == b"%PDF-"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
