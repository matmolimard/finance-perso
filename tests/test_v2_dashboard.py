from pathlib import Path

from portfolio_tracker.v2.bootstrap import bootstrap_v2_data
from portfolio_tracker.v2.dashboard import build_v2_dashboard_data
from portfolio_tracker.v2.details import build_v2_contract_detail, build_v2_support_detail
from portfolio_tracker.v2.ged import build_v2_ged_data
from portfolio_tracker.v2.manual import (
    save_document_validation,
    save_fonds_euro_pilotage,
    save_structured_event_validation,
    save_structured_product_rule,
)
from portfolio_tracker.v2.storage import connect
from portfolio_tracker.web.app import STATIC_DIR


def test_v2_static_assets_exist():
    assert (STATIC_DIR / "v2.html").exists()
    assert (STATIC_DIR / "v2.css").exists()
    assert (STATIC_DIR / "v2.js").exists()
    assert (STATIC_DIR / "v2-contract.html").exists()
    assert (STATIC_DIR / "v2-contract.js").exists()
    assert (STATIC_DIR / "v2-support.html").exists()
    assert (STATIC_DIR / "v2-support.js").exists()
    assert (STATIC_DIR / "v2-ged.html").exists()
    assert (STATIC_DIR / "v2-ged.js").exists()
    assert (STATIC_DIR / "v2-document.html").exists()
    assert (STATIC_DIR / "v2-document.js").exists()

    dashboard_html = (STATIC_DIR / "v2.html").read_text(encoding="utf-8")
    dashboard_js = (STATIC_DIR / "v2.js").read_text(encoding="utf-8")
    dashboard_css = (STATIC_DIR / "v2.css").read_text(encoding="utf-8")

    assert 'id="v2-documents-contract"' in dashboard_html
    assert 'id="v2-documents-type"' in dashboard_html
    assert 'id="v2-structured-summary"' in dashboard_html
    assert "function renderExternalFlowsCell" in dashboard_js
    assert "function renderStructuredSummary" in dashboard_js
    assert "annual_flow_summary" in dashboard_js
    assert "official_structured_value" in dashboard_js
    assert "documentFilters" in dashboard_js
    assert ".scroll-panel" in dashboard_css


def test_v2_bootstrap_imports_real_documents_and_snapshots(tmp_path):
    root = Path(__file__).resolve().parents[1]
    data_dir = root / "portfolio_tracker" / "data"
    db_path = tmp_path / "portfolio_v2.sqlite"

    result = bootstrap_v2_data(data_dir, db_path=db_path)

    assert result["ok"] is True
    assert result["totals"]["contracts"] == 2
    assert result["totals"]["snapshots"] == 7
    assert result["totals"]["documents"] >= 38
    assert result["totals"]["ledger_entries"] > 0
    assert result["imported"]["brochures_indexed"] >= 8
    assert result["imported"]["ledger_entries_imported"] == result["totals"]["ledger_entries"]
    with connect(db_path) as conn:
        transfer_kinds = {
            row["entry_kind"]
            for row in conn.execute(
                """
                SELECT DISTINCT entry_kind
                FROM contract_ledger_entries
                WHERE contract_name = 'SwissLife Capi Stratégic Premium'
                  AND fiscal_year = 2025
                """
            ).fetchall()
        }
    assert "internal_transfer_in" in transfer_kinds
    assert "internal_transfer_out" in transfer_kinds


def test_v2_dashboard_builds_expected_contracts_and_snapshots(tmp_path):
    root = Path(__file__).resolve().parents[1]
    data_dir = root / "portfolio_tracker" / "data"
    db_path = tmp_path / "portfolio_v2.sqlite"

    payload = build_v2_dashboard_data(data_dir, db_path=db_path)

    assert [card["contract_name"] for card in payload["contracts"]] == [
        "HIMALIA",
        "SwissLife Capi Stratégic Premium",
    ]
    assert payload["overview"]["contracts_count"] == 2
    assert payload["overview"]["current_value"] > 1_000_000
    assert len(payload["documents"]) >= 38
    assert len(payload["structured_summary"]) >= 1
    assert payload["structured_summary"][0]["redeem_if_today"] in {"OUI", "non", "n/a"}
    assert "perf_if_strike_annualized" in payload["structured_summary"][0]

    himalia_latest = payload["snapshots_by_contract"]["HIMALIA"][-1]
    assert himalia_latest["reference_date"] == "2025-12-31"
    assert himalia_latest["official_total_value"] == 196792.53
    assert himalia_latest["official_fonds_euro_value"] == 72124.42
    assert himalia_latest["official_structured_value"] > 0
    assert himalia_latest["official_uc_value"] < himalia_latest["official_uc_gross_value"]
    assert himalia_latest["annual_flow_summary"]["external_contributions_total"] == 50000.0
    assert himalia_latest["annual_flow_summary"]["fees_total"] > 0

    swisslife_latest = payload["snapshots_by_contract"]["SwissLife Capi Stratégic Premium"][-1]
    assert swisslife_latest["reference_date"] == "2025-12-31"
    assert swisslife_latest["official_total_value"] == 1076684.08
    assert swisslife_latest["official_uc_value"] < swisslife_latest["official_uc_gross_value"]
    assert swisslife_latest["official_structured_value"] > 0
    assert swisslife_latest["official_fonds_euro_value"] == 223380.21
    assert swisslife_latest["annual_flow_summary"]["external_contributions_total"] == 0.0
    assert swisslife_latest["annual_flow_summary"]["credited_income_total"] > 0
    assert swisslife_latest["internal_transfer_adjustments"]["fonds_euro"]["out"] > 0
    assert swisslife_latest["internal_transfer_adjustments"]["fonds_euro"]["in"] == 0
    assert swisslife_latest["internal_transfer_adjustments"]["uc"]["in"] > 0

    swisslife_card = next(card for card in payload["contracts"] if card["contract_name"] == "SwissLife Capi Stratégic Premium")
    assert swisslife_card["year_progress_reference_date"] == "2025-12-31"
    assert swisslife_card["year_progress_amount"] is not None
    assert swisslife_card["year_progress_pct"] is not None


def test_v2_dashboard_snapshot_deltas_are_normalized_for_internal_transfers():
    dashboard_js = (STATIC_DIR / "v2.js").read_text(encoding="utf-8")

    assert "Flux externes année" in dashboard_js
    assert "Crédits constatés" in dashboard_js
    assert "Remb. structurés" in dashboard_js
    assert "Frais / taxes" in dashboard_js


def test_v2_runtime_code_no_longer_depends_on_v1_dashboard_or_cli():
    root = Path(__file__).resolve().parents[1]
    runtime_source = (root / "portfolio_tracker" / "v2" / "runtime.py").read_text(encoding="utf-8")
    dashboard_source = (root / "portfolio_tracker" / "v2" / "dashboard.py").read_text(encoding="utf-8")
    details_source = (root / "portfolio_tracker" / "v2" / "details.py").read_text(encoding="utf-8")
    manual_source = (root / "portfolio_tracker" / "v2" / "manual.py").read_text(encoding="utf-8")
    market_source = (root / "portfolio_tracker" / "v2" / "market.py").read_text(encoding="utf-8")
    market_actions_source = (root / "portfolio_tracker" / "v2" / "market_actions.py").read_text(encoding="utf-8")
    bootstrap_source = (root / "portfolio_tracker" / "v2" / "bootstrap.py").read_text(encoding="utf-8")
    main_source = (root / "portfolio_tracker" / "main.py").read_text(encoding="utf-8")
    setup_source = (root / "setup.py").read_text(encoding="utf-8")

    assert "build_dashboard_data" not in dashboard_source
    assert "build_dashboard_data" not in details_source
    assert "PortfolioCLI" not in runtime_source
    assert "PortfolioCLI" not in dashboard_source
    assert "PortfolioCLI" not in details_source
    assert "PortfolioCLI" not in manual_source
    assert "PortfolioCLI" not in market_source
    assert "PortfolioCLI" not in market_actions_source
    assert "PortfolioCLI" not in bootstrap_source
    assert "portfolio_tracker.cli:main" not in setup_source
    assert "from .cli import PortfolioCLI" not in main_source
    assert "from ..application" not in runtime_source
    assert "from ..core" not in runtime_source
    assert "from ..core" not in dashboard_source
    assert "from ..market.nav_fetch" not in market_source
    assert "from ..market" not in market_actions_source


def test_v2_structured_coverage_blocks_when_brochure_is_missing(tmp_path):
    root = Path(__file__).resolve().parents[1]
    data_dir = root / "portfolio_tracker" / "data"
    db_path = tmp_path / "portfolio_v2.sqlite"

    payload = build_v2_dashboard_data(data_dir, db_path=db_path)
    coverage_by_asset = {row["asset_id"]: row for row in payload["structured_coverage"]}

    assert coverage_by_asset["struct_d_rendement_ca_div_forf_090_fev_2025"]["rule_status"] == "complete"
    assert coverage_by_asset["struct_d_rendement_bouygues_div_fix_170_fev_2025"]["has_brochure"] is True
    assert coverage_by_asset["struct_d_coupon_kg_eni_091_oct_2023"]["rule_status"] == "complete"
    assert "struct_callable_note_taux_fixe_dec_2023" not in coverage_by_asset
    assert "struct_d_coupon_kg_credit_agricole_decrement_mai_2023" not in coverage_by_asset
    assert "struct_d_rendement_bouygues_div_fix_170_oct_2024" not in coverage_by_asset


def test_v2_contract_detail_returns_positions_documents_and_snapshots(tmp_path):
    root = Path(__file__).resolve().parents[1]
    data_dir = root / "portfolio_tracker" / "data"
    db_path = tmp_path / "portfolio_v2.sqlite"

    payload = build_v2_contract_detail(data_dir, "0010645288001", db_path=db_path)

    assert payload["contract"]["contract_name"] == "SwissLife Capi Stratégic Premium"
    assert payload["summary"]["current_value"] > 1_000_000
    assert payload["summary"]["official_total_value"] == 1076684.08
    assert len(payload["snapshots"]) == 4
    assert len(payload["positions_by_type"]["structured"]) >= 1
    assert len(payload["documents"]) >= 10


def test_v2_support_detail_returns_asset_position_and_related_docs(tmp_path):
    root = Path(__file__).resolve().parents[1]
    data_dir = root / "portfolio_tracker" / "data"
    db_path = tmp_path / "portfolio_v2.sqlite"

    payload = build_v2_support_detail(data_dir, "pos_swiss_005", db_path=db_path)

    assert payload["asset"]["asset_id"] == "struct_d_coupon_kg_eni_091_oct_2023"
    assert payload["position"]["contract_name"] == "SwissLife Capi Stratégic Premium"
    assert payload["current"]["position_id"] == "pos_swiss_005"
    assert payload["structured_rule"]["rule_status"] == "complete"
    assert len(payload["documents"]) >= 1
    assert payload["structured_summary"]["has_events_file"] is True
    assert payload["structured_summary"]["has_brochure"] is True
    assert payload["structured_summary"]["expected_events_count"] > 0


def test_v2_ged_supports_filters(tmp_path):
    root = Path(__file__).resolve().parents[1]
    data_dir = root / "portfolio_tracker" / "data"
    db_path = tmp_path / "portfolio_v2.sqlite"

    payload = build_v2_ged_data(
        data_dir,
        db_path=db_path,
        contract_name="HIMALIA",
        document_type="insurer_statement",
        year=2025,
        status="archived",
    )

    assert len(payload["documents"]) == 1
    document = payload["documents"][0]
    assert document["contract_name"] == "HIMALIA"
    assert document["document_type"] == "insurer_statement"
    assert document["status"] == "archived"


def test_v2_manual_structured_rule_and_event_validation_persist(tmp_path):
    root = Path(__file__).resolve().parents[1]
    data_dir = root / "portfolio_tracker" / "data"
    db_path = tmp_path / "portfolio_v2.sqlite"
    bootstrap_v2_data(data_dir, db_path=db_path)
    initial_payload = build_v2_support_detail(data_dir, "pos_swiss_005", db_path=db_path)
    event_key = initial_payload["structured_summary"]["expected_events"][0]["event_key"]
    event_date = initial_payload["structured_summary"]["expected_events"][0]["date"]
    event_type = initial_payload["structured_summary"]["expected_events"][0]["type"]

    save_structured_product_rule(
        data_dir,
        "struct_d_coupon_kg_eni_091_oct_2023",
        {
            "display_name_override": "ENI Test",
            "rule_source_mode": "manual",
            "coupon_payment_mode": "in_fine",
            "coupon_frequency": "Semestriel",
            "coupon_rule_summary": "Coupon test",
        },
        db_path=db_path,
    )
    save_structured_event_validation(
        data_dir,
        "struct_d_coupon_kg_eni_091_oct_2023",
        {
            "event_key": event_key,
            "event_type": event_type,
            "event_date": event_date,
            "validation_status": "triggered",
            "notes": "Test",
        },
        db_path=db_path,
    )

    payload = build_v2_support_detail(data_dir, "pos_swiss_005", db_path=db_path)

    assert payload["structured_rule_form"]["display_name_override"] == "ENI Test"
    assert payload["structured_rule_form"]["coupon_payment_mode"] == "in_fine"
    assert any(event["validation_status"] == "triggered" for event in payload["structured_summary"]["expected_events"])


def test_v2_manual_document_validation_and_fonds_euro_pilotage_persist(tmp_path):
    root = Path(__file__).resolve().parents[1]
    data_dir = root / "portfolio_tracker" / "data"
    db_path = tmp_path / "portfolio_v2.sqlite"
    bootstrap_v2_data(data_dir, db_path=db_path)

    save_document_validation(
        data_dir,
        "himalia_courrier_avenant_2026_02_28",
        {"validation_status": "confirmed", "notes": "Arbitrage validé"},
        db_path=db_path,
    )
    save_fonds_euro_pilotage(
        data_dir,
        "83914927",
        {"annual_rate": 0.025, "reference_date": "2026-03-15", "notes": "Pilotage test"},
        db_path=db_path,
    )

    payload = build_v2_contract_detail(data_dir, "83914927", db_path=db_path)

    doc = next(row for row in payload["documents"] if row["document_id"] == "himalia_courrier_avenant_2026_02_28")
    assert doc["validation_status"] == "confirmed"
    assert payload["fonds_euro_pilotage"]["annual_rate"] == 0.025
    assert payload["fonds_euro_pilotage_summary"]["pilotage_value"] > 0
