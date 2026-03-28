from pathlib import Path
import json
import subprocess

from portfolio_tracker.schemas import InvestmentSchema


def test_investment_schema_accepts_purchase_nav_source_lots():
    investment = InvestmentSchema(
        subscription_date="2025-09-04",
        invested_amount=1000,
        units_held=10,
        purchase_nav=100,
        purchase_nav_source="lots",
    )

    assert investment.purchase_nav_source == "lots"


def test_investment_schema_accepts_purchase_nav_source_unknown():
    investment = InvestmentSchema(
        subscription_date="2025-09-04",
        invested_amount=1000,
        units_held=10,
        purchase_nav=100,
        purchase_nav_source="unknown",
    )

    assert investment.purchase_nav_source == "unknown"


def test_cli_status_alias_matches_global_output():
    root = Path(__file__).resolve().parents[1]
    python = root / ".venv" / "bin" / "python"
    data_dir = "portfolio_tracker/data"

    global_result = subprocess.run(
        [str(python), "-m", "portfolio_tracker.v2.cli", "--data-dir", data_dir, "global"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    status_result = subprocess.run(
        [str(python), "-m", "portfolio_tracker.v2.cli", "--data-dir", data_dir, "status"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )

    assert status_result.stdout == global_result.stdout


def test_web_payload_command_outputs_json():
    root = Path(__file__).resolve().parents[1]
    python = root / ".venv" / "bin" / "python"
    data_dir = "portfolio_tracker/data"

    result = subprocess.run(
        [str(python), "-m", "portfolio_tracker.v2.cli", "--data-dir", data_dir, "web-payload"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )

    payload = json.loads(result.stdout)

    assert "contracts" in payload
    assert "overview" in payload
    assert "documents" in payload
    assert "snapshots_by_contract" in payload


def test_structured_command_outputs_dedicated_summary_table():
    root = Path(__file__).resolve().parents[1]
    python = root / ".venv" / "bin" / "python"
    data_dir = "portfolio_tracker/data"

    result = subprocess.run(
        [str(python), "-m", "portfolio_tracker.v2.cli", "--data-dir", data_dir, "structured"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )

    stdout = result.stdout

    assert "PRODUITS STRUCTURÉS - Synthèse" in stdout
    assert "Remb. si ajd ?" in stdout
    assert "Perf si strike/an" in stdout
    assert "Valeur si strike" in stdout
    assert "D Coupon Kg Eni 0.91 Octobre 2023" in stdout
    assert "OUI" in stdout
