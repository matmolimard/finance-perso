from pathlib import Path

import pytest

from portfolio_tracker.bootstrap import (
    _parse_himalia_snapshot_visible_operations,
    _parse_statement_snapshot,
    _parse_statement_snapshot_positions,
    bootstrap_v2_data,
)
from portfolio_tracker.dashboard import build_v2_dashboard_data
from portfolio_tracker.details import build_v2_contract_detail, build_v2_support_detail
from portfolio_tracker.document_extractors import extract_structured_brochure_suggestions, run_post_ingest_hooks
from portfolio_tracker.document_ingest import _infer_coverage_year, classify_document, DOCUMENT_TYPE_LABELS
from portfolio_tracker.ged import build_v2_ged_data
from portfolio_tracker.manual import (
    delete_manual_movement,
    list_manual_movements,
    save_document_validation,
    save_fonds_euro_pilotage,
    save_manual_movement,
    save_snapshot_validation,
    save_structured_event_validation,
    save_structured_product_rule,
)
from portfolio_tracker.pdf_audit import build_contract_pdf_audit
from portfolio_tracker.storage import connect
from portfolio_tracker.web.app import STATIC_DIR


def _position_id_for_asset(db_path: Path, *, contract_name: str, asset_id: str) -> str:
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT position_id
            FROM positions
            WHERE contract_name = ?
              AND asset_id = ?
            LIMIT 1
            """,
            (contract_name, asset_id),
        ).fetchone()
    assert row is not None, f"Position introuvable pour {contract_name} / {asset_id}"
    return str(row["position_id"])


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
    assert "model_structured_value" in dashboard_js
    assert "structured_model_gap_value" in dashboard_js
    assert "documentFilters" in dashboard_js
    assert ".scroll-panel" in dashboard_css


def test_contract_page_no_longer_exposes_text_movement_import():
    contract_html = (STATIC_DIR / "v2-contract.html").read_text(encoding="utf-8")
    contract_js = (STATIC_DIR / "v2-contract.js").read_text(encoding="utf-8")

    assert "Flux PDF uniquement" in contract_html
    assert "movements-form" not in contract_html
    assert "/api/movements/preview" not in contract_js
    assert "/api/movements/apply" not in contract_js


def test_cli_and_webapp_no_longer_expose_text_movement_import():
    root = Path(__file__).resolve().parents[1]
    cli_source = (root / "portfolio_tracker" / "cli.py").read_text(encoding="utf-8")
    web_source = (root / "portfolio_tracker" / "web" / "app.py").read_text(encoding="utf-8")

    assert "import-movements" not in cli_source
    assert "/api/movements/preview" not in web_source
    assert "/api/movements/apply" not in web_source


def test_v2_bootstrap_imports_real_documents_and_snapshots(tmp_path):
    root = Path(__file__).resolve().parents[1]
    data_dir = root / "portfolio_tracker" / "data"
    db_path = tmp_path / "portfolio_v2.sqlite"

    result = bootstrap_v2_data(data_dir, db_path=db_path)

    assert result["ok"] is True
    assert result["totals"]["contracts"] == 2
    assert result["totals"]["snapshots"] == 7
    assert result["totals"]["snapshot_positions"] > 0
    assert result["totals"]["snapshot_operations"] > 0
    assert result["totals"]["snapshot_operation_legs"] > 0
    assert result["totals"]["documents"] >= 38
    assert result["totals"]["ledger_entries"] > 0
    assert result["imported"]["brochures_indexed"] >= 8
    assert result["imported"]["snapshot_positions_imported"] == result["totals"]["snapshot_positions"]
    assert result["imported"]["snapshot_operations_imported"] == result["totals"]["snapshot_operations"]
    assert result["imported"]["snapshot_operation_legs_imported"] == result["totals"]["snapshot_operation_legs"]
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
        swisslife_positions = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM snapshot_positions sp
            JOIN annual_snapshots s ON s.snapshot_id = sp.snapshot_id
            WHERE sp.contract_name = 'SwissLife Capi Stratégic Premium'
              AND s.reference_date = '2025-12-31'
            """
        ).fetchone()["c"]
        himalia_positions = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM snapshot_positions sp
            JOIN annual_snapshots s ON s.snapshot_id = sp.snapshot_id
            WHERE sp.contract_name = 'HIMALIA'
              AND s.reference_date = '2025-12-31'
            """
        ).fetchone()["c"]
        himalia_operations = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM snapshot_operations_visible so
            JOIN annual_snapshots s ON s.snapshot_id = so.snapshot_id
            WHERE so.contract_name = 'HIMALIA'
              AND s.reference_date = '2025-12-31'
            """
        ).fetchone()["c"]
        himalia_operation_legs = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM snapshot_operation_legs_visible sol
            JOIN annual_snapshots s ON s.snapshot_id = sol.snapshot_id
            WHERE sol.contract_name = 'HIMALIA'
              AND s.reference_date = '2025-12-31'
            """
        ).fetchone()["c"]
        himalia_unmapped_operation_legs = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM snapshot_operation_legs_visible sol
            JOIN annual_snapshots s ON s.snapshot_id = sol.snapshot_id
            WHERE sol.contract_name = 'HIMALIA'
              AND s.reference_date = '2025-12-31'
              AND sol.asset_id IS NULL
            """
        ).fetchone()["c"]
    assert "internal_transfer_in" in transfer_kinds
    assert "internal_transfer_out" in transfer_kinds
    assert swisslife_positions >= 12
    assert himalia_positions >= 9
    assert himalia_operations >= 6
    assert himalia_operation_legs >= 15
    assert himalia_unmapped_operation_legs == 0


def test_v2_bootstrap_can_reconstruct_portfolio_without_assets_or_positions_yaml(tmp_path):
    root = Path(__file__).resolve().parents[1]
    source_data_dir = root / "portfolio_tracker" / "data"
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    for dirname in ("documents", "market_data", "product_brochure"):
        source = source_data_dir / dirname
        if source.exists():
            (data_dir / dirname).symlink_to(source, target_is_directory=True)

    db_path = tmp_path / "portfolio_v2.sqlite"
    result = bootstrap_v2_data(data_dir, db_path=db_path)

    assert result["ok"] is True
    assert result["imported"]["portfolio_assets_seeded"] > 0
    assert result["imported"]["portfolio_positions_seeded"] == 0
    assert result["imported"]["portfolio_lots_seeded"] == 0
    assert result["imported"]["portfolio_positions_reconstructed_from_pdf"] > 0
    assert result["imported"]["portfolio_lots_reconstructed_from_pdf"] > 0
    assert result["imported"]["ledger_entries_imported"] > 0
    assert result["totals"]["assets"] > 0
    assert result["totals"]["positions"] > 0
    assert result["totals"]["position_lots"] > 0
    assert result["totals"]["ledger_entries"] > 0

    with connect(db_path) as conn:
        missing_snapshot_position_links = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM snapshot_positions
            WHERE asset_id IS NOT NULL
              AND position_id IS NULL
            """
        ).fetchone()["c"]
        missing_snapshot_operation_links = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM snapshot_operation_legs_visible
            WHERE asset_id IS NOT NULL
              AND position_id IS NULL
            """
        ).fetchone()["c"]
    assert missing_snapshot_position_links == 0
    assert missing_snapshot_operation_links == 0

    payload = build_v2_contract_detail(data_dir, "83914927", db_path=db_path)
    assert payload["contract"]["contract_name"] == "HIMALIA"
    assert payload["summary"]["current_value"] > 100000
    assert len(payload["positions"]) >= 9


def test_v2_dashboard_builds_expected_contracts_and_snapshots(real_data_dir, copied_real_db_path):
    payload = build_v2_dashboard_data(real_data_dir, db_path=copied_real_db_path)

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
    assert himalia_latest["official_structured_value"] is None or himalia_latest["official_structured_value"] >= 0
    assert himalia_latest["model_structured_value"] > 0
    assert himalia_latest["official_uc_value"] > 0
    assert himalia_latest["annual_flow_summary"]["external_contributions_total"] == 50000.0
    assert himalia_latest["annual_flow_summary"]["fees_total"] > 0

    swisslife_latest = payload["snapshots_by_contract"]["SwissLife Capi Stratégic Premium"][-1]
    assert swisslife_latest["reference_date"] == "2025-12-31"
    assert swisslife_latest["official_total_value"] == 1076684.08
    assert swisslife_latest["official_uc_value"] > 0
    assert swisslife_latest["official_structured_value"] is None or swisslife_latest["official_structured_value"] >= 0
    assert swisslife_latest["model_structured_value"] > 0
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


def test_v2_bootstrap_parses_swisslife_snapshot_with_normalized_fallback(monkeypatch, tmp_path):
    pdf_path = tmp_path / "swisslife.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%stub")

    monkeypatch.setattr(
        "portfolio_tracker.bootstrap._pdf_text",
        lambda _: (
            "Situationdevotreepargneau31/12/2025\n"
            "Montantdelepargne(*)1076684,08\n"
            "Epargneinvestieenunitesdecompte853303,87\n"
            "Epargneinvestiesurlefondseneuros223380,21\n"
            "Dontinteretsnetsdeprelevementssociauxpercusautitredelannee2025 10472,17\n"
        ),
    )

    snapshot = _parse_statement_snapshot(
        {
            "document_id": "swisslife_releve_situation_2026_02_20",
            "insurer": "SwissLife",
            "contract_name": "SwissLife Capi Stratégic Premium",
            "coverage_year": 2025,
            "filepath": str(pdf_path),
            "statement_date": "2026-02-20",
        },
        Path("."),
    )

    assert snapshot["official_total_value"] == 1076684.08
    assert snapshot["official_uc_value"] == 853303.87
    assert snapshot["official_fonds_euro_value"] == 223380.21
    assert snapshot["official_euro_interest_net"] == 10472.17


def test_parse_statement_snapshot_positions_swisslife(monkeypatch, tmp_path):
    pdf_path = tmp_path / "swisslife_positions.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%stub")

    monkeypatch.setattr(
        "portfolio_tracker.bootstrap._pdf_text",
        lambda _: (
            "Information relative à votre épargne investie en unités de compte\n"
            "Support\n"
            "ISIN\n"
            "Date de valorisation Montant net (€) Nombredeparts Valeur de la part (€)\n"
            "DRENDEMENTENGIE\n"
            "DIVIDENDEFIXE0.95€MARS\n"
            "2025\n"
            "FR001400UQP1\n"
            "31/12/2025 112761,86 108,10263 1043,100\n"
            "nondisponible nonapplicable\n"
            "HeliumFundSelection B-EUR\n"
            "LU1112771503\n"
            "31/12/2025 77608,23 43,58916 1780,448 1555,294 2.17%\n"
            "TOTAL 190370,09€\n"
            "Situationdevotre épargneau 31/12/2025\n"
            "Epargneinvestiesurlefondseneuros 223380,21€\n"
            "Dont intérêts nets de prélèvements sociaux perçus au titre de l'année2025 12253,67€\n"
        ),
    )

    rows = _parse_statement_snapshot_positions(
        {
            "document_id": "swisslife_releve_positions_2026",
            "insurer": "SwissLife",
            "contract_name": "SwissLife Capi Stratégic Premium",
            "coverage_year": 2025,
            "filepath": str(pdf_path),
        },
        tmp_path,
    )

    assert len(rows) == 3
    assert rows[0]["isin"] == "FR001400UQP1"
    assert rows[0]["official_value"] == 112761.86
    assert rows[1]["isin"] == "LU1112771503"
    assert rows[1]["official_average_purchase_price"] == 1555.294
    assert rows[2]["asset_name_raw"] == "Fonds Euros Swisslife"
    assert rows[2]["official_profit_sharing_amount"] == 12253.67


def test_parse_statement_snapshot_positions_himalia(monkeypatch, tmp_path):
    pdf_path = tmp_path / "himalia_positions.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%stub")

    monkeypatch.setattr(
        "portfolio_tracker.bootstrap._pdf_text",
        lambda _: (
            "Supports Fonds en euros A la date du Participation aux bénéfices (*) Epargne atteinte\n"
            "Actif Général Generali Vie 31/12/2025 2 738,65 € 72 124,42 €\n"
            "Supports Unités de Compte A la date du Valeur de la part Nombre de parts PAM (1) Epargne atteinte\n"
            "BDL Rempart C 31/12/2025 250,94 € 25,8454 250,24 € 6 485,64 €\n"
            "Helium Fund Selection B 31/12/2025 1 780,45 € 3,7300 1 733,95 € 6 641,07 €\n"
            "(1) PAM : Prix d'Achat Moyen\n"
        ),
    )

    rows = _parse_statement_snapshot_positions(
        {
            "document_id": "himalia_releve_positions_2026",
            "insurer": "Generali",
            "contract_name": "HIMALIA",
            "coverage_year": 2025,
            "filepath": str(pdf_path),
        },
        tmp_path,
    )

    assert len(rows) == 3
    assert rows[0]["asset_name_raw"] == "Actif Général Generali Vie"
    assert rows[0]["official_profit_sharing_amount"] == 2738.65
    assert rows[1]["quantity"] == 25.8454
    assert rows[1]["official_average_purchase_price"] == 250.24
    assert rows[2]["official_value"] == 6641.07


def test_parse_himalia_snapshot_visible_operations():
    text = (
        "OPERATIONS REALISEES DU 01/01/2025 AU 31/12/2025\n"
        "Opérations / Supports Montant net A la date du Valeur de la part Nombre de parts\n"
        "Opération sur titre (Remboursement) du 03/01/2025 (Frais : 0,00%)\n"
        "Callable Note Taux Fixe Dec 23 -94 598,28 € 03/01/2025 1 050,00 € -90,0936\n"
        "GENERALI Trésorerie ISR Act B 94 598,28 € 03/01/2025 3 748,90 € 25,2336\n"
        "Arbitrage du 02/02/2025 (Frais : 0,00%)\n"
        "GENERALI Trésorerie ISR Act B -94 854,11 € 03/02/2025 3 759,04 € -25,2336\n"
        "D Rd Bouygues Div Forf 1.70 Fev25 23 713,53 € 03/02/2025 1 000,00 € 23,7135\n"
        "D Rendt CA Div Forf 0,9e 0225 23 713,53 € 03/02/2025 1 000,00 € 23,7135\n"
        "D Rend Distri Fev 25 47 427,06 € 03/02/2025 1 000,00 € 47,4270\n"
        "Distribution de dividendes de 1 179,76 € du 28/08/2025 provenant du support D Rend Distri Fev 25 (Frais : 0,00%)\n"
        "Actif Général Generali Vie (Fonds en euros) 1 179,76 € 28/08/2025\n"
    )

    operations = _parse_himalia_snapshot_visible_operations(text)

    assert len(operations) == 3
    assert operations[0]["operation_type"] == "structured_redemption"
    assert operations[0]["legs"][0]["cash_amount"] == -94598.28
    assert operations[1]["operation_type"] == "arbitration"
    assert len(operations[1]["legs"]) == 4
    assert operations[2]["operation_type"] == "dividend_distribution"
    assert operations[2]["headline_amount"] == 1179.76
    assert operations[2]["legs"][0]["asset_name_raw"] == "Actif Général Generali Vie (Fonds en euros)"


def test_v2_dashboard_snapshot_deltas_are_normalized_for_internal_transfers():
    dashboard_js = (STATIC_DIR / "v2.js").read_text(encoding="utf-8")

    assert "Flux externes année" in dashboard_js
    assert "Crédits constatés" in dashboard_js
    assert "Remb. structurés" in dashboard_js
    assert "Frais / taxes" in dashboard_js


def test_v2_runtime_code_no_longer_depends_on_v1_dashboard_or_cli():
    root = Path(__file__).resolve().parents[1]
    runtime_source = (root / "portfolio_tracker" / "runtime.py").read_text(encoding="utf-8")
    dashboard_source = (root / "portfolio_tracker" / "dashboard.py").read_text(encoding="utf-8")
    details_source = (root / "portfolio_tracker" / "details.py").read_text(encoding="utf-8")
    manual_source = (root / "portfolio_tracker" / "manual.py").read_text(encoding="utf-8")
    market_source = (root / "portfolio_tracker" / "market.py").read_text(encoding="utf-8")
    market_actions_source = (root / "portfolio_tracker" / "market_actions.py").read_text(encoding="utf-8")
    bootstrap_source = (root / "portfolio_tracker" / "bootstrap.py").read_text(encoding="utf-8")
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
    assert "portfolio_tracker.v2.cli:main" not in setup_source
    assert "from .cli import PortfolioCLI" not in main_source
    assert "from ..application" not in runtime_source
    assert "from ..core" not in runtime_source
    assert "from ..core" not in dashboard_source
    assert "from ..market.nav_fetch" not in market_source
    assert "from ..market" not in market_actions_source


def test_v2_structured_coverage_blocks_when_brochure_is_missing(real_data_dir, copied_real_db_path):
    payload = build_v2_dashboard_data(real_data_dir, db_path=copied_real_db_path)
    coverage_by_asset = {row["asset_id"]: row for row in payload["structured_coverage"]}

    assert coverage_by_asset["struct_d_rendement_ca_div_forf_090_fev_2025"]["rule_status"] == "complete"
    assert coverage_by_asset["struct_d_rendement_bouygues_div_fix_170_fev_2025"]["has_brochure"] is True
    assert coverage_by_asset["struct_d_coupon_kg_eni_091_oct_2023"]["rule_status"] == "complete"
    assert "struct_callable_note_taux_fixe_dec_2023" not in coverage_by_asset
    assert "struct_d_coupon_kg_credit_agricole_decrement_mai_2023" not in coverage_by_asset
    assert "struct_d_rendement_bouygues_div_fix_170_oct_2024" not in coverage_by_asset


def test_v2_contract_detail_returns_positions_documents_and_snapshots(real_data_dir, copied_real_db_path):
    payload = build_v2_contract_detail(real_data_dir, "0010645288001", db_path=copied_real_db_path)

    assert payload["contract"]["contract_name"] == "SwissLife Capi Stratégic Premium"
    assert payload["summary"]["current_value"] > 1_000_000
    assert payload["summary"]["official_total_value"] == 1076684.08
    assert len(payload["snapshots"]) == 4
    assert len(payload["positions_by_type"]["structured"]) >= 1
    assert len(payload["documents"]) >= 10


def test_pdf_contract_audit_reports_himalia_pdf_state(real_data_dir, copied_real_db_path):
    payload = build_contract_pdf_audit(real_data_dir, "83914927", year=2025, db_path=copied_real_db_path)

    assert payload["contract"]["contract_name"] == "HIMALIA"
    assert payload["meta"]["year"] == 2025
    assert payload["summary"]["snapshots_count"] == 1
    assert payload["summary"]["snapshot_positions_count"] >= 9
    assert payload["summary"]["visible_operations_count"] >= 8
    assert payload["summary"]["visible_operation_legs_count"] >= 15
    assert payload["summary"]["visible_operation_legs_unmapped_count"] == 0
    assert payload["manual_movements"] == []
    assert payload["snapshots"][0]["reference_date"] == "2025-12-31"
    assert payload["snapshots"][0]["visible_operation_legs_unmapped_count"] == 0
    assert any(row["operation_type"] == "arbitration" for row in payload["visible_operations"])
    assert any(
        leg["asset_id"] == "struct_d_rendement_distribution_fev_2025"
        for row in payload["visible_operations"]
        for leg in row["legs"]
    )


def test_v2_support_detail_returns_asset_position_and_related_docs(real_data_dir, copied_real_db_path):
    position_id = _position_id_for_asset(
        copied_real_db_path,
        contract_name="SwissLife Capi Stratégic Premium",
        asset_id="struct_d_coupon_kg_eni_091_oct_2023",
    )
    payload = build_v2_support_detail(real_data_dir, position_id, db_path=copied_real_db_path)

    assert payload["asset"]["asset_id"] == "struct_d_coupon_kg_eni_091_oct_2023"
    assert payload["position"]["contract_name"] == "SwissLife Capi Stratégic Premium"
    assert payload["current"]["position_id"] == position_id
    assert payload["structured_rule"]["rule_status"] == "complete"
    assert len(payload["documents"]) >= 1
    assert payload["structured_summary"]["has_events_file"] is True
    assert payload["structured_summary"]["has_brochure"] is True
    assert payload["structured_summary"]["expected_events_count"] > 0


def test_v2_ged_supports_filters(real_data_dir, copied_real_db_path):
    payload = build_v2_ged_data(
        real_data_dir,
        db_path=copied_real_db_path,
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


def test_v2_manual_structured_rule_and_event_validation_persist(real_data_dir, copied_real_db_path):
    position_id = _position_id_for_asset(
        copied_real_db_path,
        contract_name="SwissLife Capi Stratégic Premium",
        asset_id="struct_d_coupon_kg_eni_091_oct_2023",
    )
    initial_payload = build_v2_support_detail(real_data_dir, position_id, db_path=copied_real_db_path)
    event_key = initial_payload["structured_summary"]["expected_events"][0]["event_key"]
    event_date = initial_payload["structured_summary"]["expected_events"][0]["date"]
    event_type = initial_payload["structured_summary"]["expected_events"][0]["type"]

    save_structured_product_rule(
        real_data_dir,
        "struct_d_coupon_kg_eni_091_oct_2023",
        {
            "display_name_override": "ENI Test",
            "rule_source_mode": "manual",
            "coupon_payment_mode": "in_fine",
            "coupon_frequency": "Semestriel",
            "coupon_rule_summary": "Coupon test",
        },
        db_path=copied_real_db_path,
    )
    save_structured_event_validation(
        real_data_dir,
        "struct_d_coupon_kg_eni_091_oct_2023",
        {
            "event_key": event_key,
            "event_type": event_type,
            "event_date": event_date,
            "validation_status": "triggered",
            "notes": "Test",
        },
        db_path=copied_real_db_path,
    )

    payload = build_v2_support_detail(real_data_dir, position_id, db_path=copied_real_db_path)

    assert payload["structured_rule_form"]["display_name_override"] == "ENI Test"
    assert payload["structured_rule_form"]["coupon_payment_mode"] == "in_fine"
    assert any(event["validation_status"] == "triggered" for event in payload["structured_summary"]["expected_events"])


def test_v2_manual_document_validation_and_fonds_euro_pilotage_persist(real_data_dir, copied_real_db_path):
    save_document_validation(
        real_data_dir,
        "himalia_courrier_avenant_2026_02_28",
        {"validation_status": "confirmed", "notes": "Arbitrage validé"},
        db_path=copied_real_db_path,
    )
    save_fonds_euro_pilotage(
        real_data_dir,
        "83914927",
        {"annual_rate": 0.025, "reference_date": "2026-03-15", "notes": "Pilotage test"},
        db_path=copied_real_db_path,
    )

    payload = build_v2_contract_detail(real_data_dir, "83914927", db_path=copied_real_db_path)

    doc = next(row for row in payload["documents"] if row["document_id"] == "himalia_courrier_avenant_2026_02_28")
    assert doc["validation_status"] == "confirmed"
    assert payload["fonds_euro_pilotage"]["annual_rate"] == 0.025
    assert payload["fonds_euro_pilotage_summary"]["pilotage_value"] > 0


def test_manual_movement_persists_in_db_and_rebuilds_ledger(tmp_path):
    root = Path(__file__).resolve().parents[1]
    data_dir = root / "portfolio_tracker" / "data"
    db_path = tmp_path / "portfolio_v2.sqlite"
    bootstrap_v2_data(data_dir, db_path=db_path)
    position_id = _position_id_for_asset(
        db_path,
        contract_name="SwissLife Capi Stratégic Premium",
        asset_id="struct_d_coupon_kg_eni_091_oct_2023",
    )

    result = save_manual_movement(
        data_dir,
        {
            "contract": "0010645288001",
            "asset_id": "struct_d_coupon_kg_eni_091_oct_2023",
            "position_id": position_id,
            "effective_date": "2023-10-23",
            "raw_lot_type": "buy",
            "movement_kind": "external_contribution",
            "cash_amount": 100000.0,
            "units_delta": 100.0,
            "unit_price": 1000.0,
            "external": True,
            "reason": "buy manquant dans les PDF disponibles",
            "notes": "Cas exceptionnel validé manuellement",
        },
        db_path=db_path,
    )

    manual_id = result["manual_movement"]["manual_movement_id"]
    listing = list_manual_movements(data_dir, contract_ref="0010645288001", db_path=db_path)
    assert any(row["manual_movement_id"] == manual_id for row in listing["manual_movements"])

    with connect(db_path) as conn:
        ledger_row = conn.execute(
            """
            SELECT entry_kind, direction, amount, source_movement_id
            FROM contract_ledger_entries
            WHERE source_movement_id = ?
            """,
            (f"manual:{manual_id}",),
        ).fetchone()
    assert ledger_row is not None
    assert ledger_row["entry_kind"] == "external_contribution"
    assert ledger_row["direction"] == "credit"
    assert ledger_row["amount"] == 100000.0

    delete_result = delete_manual_movement(data_dir, manual_id, db_path=db_path)
    assert delete_result["deleted"] is True

    with connect(db_path) as conn:
        deleted = conn.execute(
            "SELECT 1 FROM contract_ledger_entries WHERE source_movement_id = ?",
            (f"manual:{manual_id}",),
        ).fetchone()
    assert deleted is None


# ── Phase 1 tests ──────────────────────────────────────────────────────────


def test_infer_coverage_year_from_text():
    assert _infer_coverage_year("releve.pdf", "Situation au 31/12/2024\nTotal: 100 000 €") == 2024
    assert _infer_coverage_year("releve_2025.pdf", "Rien de spécial") is None
    assert _infer_coverage_year("releve.pdf", "Relevé décembre 2023 bla bla") == 2023
    assert _infer_coverage_year("", "") is None


def test_classify_document_detects_movement_list():
    result = classify_document(filename="export.pdf", text="Historique des mouvements du contrat")
    assert result["document_type"] == "insurer_movement_list"
    assert result["confidence"] >= 0.8


def test_classify_document_returns_coverage_year_for_statement():
    result = classify_document(filename="releve.pdf", text="Relevé de situation au 31/12/2024")
    assert result["document_type"] == "insurer_statement"
    assert result["coverage_year"] == 2024


def test_document_type_labels_include_movement_list():
    assert "insurer_movement_list" in DOCUMENT_TYPE_LABELS
    assert DOCUMENT_TYPE_LABELS["insurer_movement_list"] == "mouvements"


# ── Phase 2 tests ──────────────────────────────────────────────────────────


def test_bootstrap_snapshots_are_proposed_by_default(monkeypatch, tmp_path):
    pdf_path = tmp_path / "generali.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%stub")

    monkeypatch.setattr(
        "portfolio_tracker.bootstrap._pdf_text",
        lambda _: "EPARGNE ATTEINTE DE VOTRE CONTRAT AU 31/12/2025 196792,53 €\n"
                  "Actif Général Generali Vie 31/12/2025 3123,45 € 72124,42 €\n",
    )

    snapshot = _parse_statement_snapshot(
        {
            "document_id": "himalia_releve_2025",
            "insurer": "Generali",
            "contract_name": "HIMALIA",
            "coverage_year": 2025,
            "filepath": str(pdf_path),
            "statement_date": "2026-02-15",
        },
        Path("."),
    )
    assert snapshot["status"] == "proposed"


def test_snapshot_validation_workflow(real_data_dir, copied_real_db_path):
    with connect(copied_real_db_path) as conn:
        row = conn.execute(
            "SELECT snapshot_id, status FROM annual_snapshots WHERE contract_name = 'HIMALIA' ORDER BY reference_date DESC LIMIT 1"
        ).fetchone()

    snapshot_id = row["snapshot_id"]
    assert row["status"] == "proposed"

    save_snapshot_validation(
        real_data_dir, snapshot_id,
        {"status": "validated", "notes": "Vérifié manuellement"},
        db_path=copied_real_db_path,
    )

    with connect(copied_real_db_path) as conn:
        row = conn.execute(
            "SELECT status FROM annual_snapshots WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchone()
    assert row["status"] == "validated"

    bootstrap_v2_data(real_data_dir, db_path=copied_real_db_path)

    with connect(copied_real_db_path) as conn:
        row = conn.execute(
            "SELECT status FROM annual_snapshots WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchone()
    assert row["status"] == "validated"


def test_dashboard_snapshots_include_status(real_data_dir, copied_real_db_path):
    payload = build_v2_dashboard_data(real_data_dir, db_path=copied_real_db_path)

    himalia_snaps = payload["snapshots_by_contract"]["HIMALIA"]
    assert all("status" in snap for snap in himalia_snaps)
    assert himalia_snaps[-1]["status"] == "proposed"


def test_dashboard_contract_reconciliation_uses_latest_validated_snapshot(real_data_dir, copied_real_db_path):
    with connect(copied_real_db_path) as conn:
        row = conn.execute(
            """
            SELECT snapshot_id
            FROM annual_snapshots
            WHERE contract_name = 'HIMALIA'
            ORDER BY reference_date DESC
            LIMIT 1
            """
        ).fetchone()
    save_snapshot_validation(real_data_dir, row["snapshot_id"], {"status": "validated"}, db_path=copied_real_db_path)

    payload = build_v2_dashboard_data(real_data_dir, db_path=copied_real_db_path)
    card = next(c for c in payload["contracts"] if c["contract_name"] == "HIMALIA")
    assert card["latest_validated_snapshot"] is not None
    assert card["latest_validated_snapshot"]["reference_date"] == "2025-12-31"
    assert card["reconciliation"]["reference_date"] == "2025-12-31"
    assert card["reconciliation"]["status"] in {"ok", "warning"}


# ── Phase 4 tests ──────────────────────────────────────────────────────────


def test_extract_structured_brochure_suggestions():
    text = (
        "Brochure produit structuré\n"
        "ISIN: FR0014007YA0\n"
        "Coupon conditionnel de 9,50% par an\n"
        "Fréquence semestrielle\n"
        "Mécanisme de remboursement anticipé (autocall)\n"
        "Barrière de protection à 60%\n"
        "Échéance: 15/03/2033\n"
        "Coupon à mémoire\n"
    )
    result = extract_structured_brochure_suggestions(text, filename="brochure_FR0014007YA0.pdf")

    suggestions = result["suggestions"]
    assert suggestions["isin_override"] == "FR0014007YA0"
    assert "9.50" in suggestions["coupon_rule_summary"] or "9,50" in suggestions["coupon_rule_summary"]
    assert suggestions["coupon_frequency"] == "semestriel"
    assert suggestions["coupon_payment_mode"] == "memory"
    assert "anticip" in suggestions["autocall_rule_summary"].lower()
    assert "60" in suggestions["capital_rule_summary"]

    extracted = result["extracted"]
    assert extracted["isin"] == "FR0014007YA0"
    assert extracted["has_autocall"] is True
    assert extracted["has_memory"] is True
    assert extracted["has_barrier"] is True


def test_run_post_ingest_hooks_for_brochure(tmp_path):
    result = run_post_ingest_hooks(
        tmp_path,
        {"document_type": "structured_brochure", "original_filename": "brochure_FR0014007YA0.pdf"},
        "Brochure test coupon 8% annuel autocall barrière 70%",
    )
    assert result["document_type"] == "structured_brochure"
    assert "structured_suggestions" in result
    assert result["structured_suggestions"]["extracted"]["has_autocall"] is True


def test_run_post_ingest_hooks_for_non_brochure(tmp_path):
    result = run_post_ingest_hooks(
        tmp_path,
        {"document_type": "insurer_statement"},
        "Relevé de situation au 31/12/2024",
    )
    assert result["document_type"] == "insurer_statement"
    assert "structured_suggestions" not in result
