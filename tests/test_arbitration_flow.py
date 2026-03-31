from pathlib import Path

import pytest

from portfolio_tracker.arbitration import (
    apply_arbitration_proposal,
    build_arbitration_proposal_for_document,
    parse_arbitration_text,
    save_arbitration_mappings,
)
from portfolio_tracker.bootstrap import BOOTSTRAP_DB_VERSION
from portfolio_tracker.runtime import V2Runtime
from portfolio_tracker.storage import connect, init_db


ARBITRAGE_TEXT = """
OBJET : Arbitrage
Arbitrage d'un montant de : 26 616,20 Euros en date du 26/03/2026
- Désinvestissement :
- EMTN D Rend TE Div For 2,96 EU 0326 (ISIN : FR0014014T52)
Valeur au 27/03/2026 : 26,6162 Parts à 1 000,00 Euros l'unité soit 26 616,20 Euros
- Réinvestissement :
- SICAV GENERALI Trésorerie ISR Act B (ISIN : FR0010233726)
Valeur au 27/03/2026 : 6,8926 Parts à 3 861,56 Euros l'unité soit 26 616,20 Euros
"""

SWISSLIFE_ARBITRAGE_TEXT = """
SwissLife Capi Stratégic Premium
Avenant d'arbitrage
Date d'effet de l'opération : 12/12/2025 Objet : Arbitrage
Désinvestissement :
FR0013301629:SLF(F)ESGShortTermEuroP1 88120,60€ 829,01928 106,295 12/12/2025
Montantàréinvestir 261896,63€
Investissement :
LU1112771503:HeliumFundSelectionB-EUR 10475,87€ 5,90451 1774,214 15/12/2025
FR00140101H2:DRendementLuxeDécembre2025 86425,87€ 86,42589 1000,000 12/12/2025
"""


def _write_test_data(data_dir: Path) -> None:
    (data_dir / "documents" / "insurer" / "generali" / "himalia" / "courriers").mkdir(parents=True, exist_ok=True)
    (data_dir / "market_data").mkdir(parents=True, exist_ok=True)

    pdf_path = data_dir / "documents" / "insurer" / "generali" / "himalia" / "courriers" / "2026-03-29_arbitrage_generali.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%stub")


def _seed_test_db(data_dir: Path, db_path: Path) -> None:
    init_db(db_path)
    imported_at = "2026-03-30T00:00:00"
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO app_meta (meta_key, meta_value)
            VALUES ('bootstrap_version', ?)
            ON CONFLICT(meta_key) DO UPDATE SET meta_value = excluded.meta_value
            """,
            (BOOTSTRAP_DB_VERSION,),
        )
        conn.execute(
            """
            INSERT INTO contracts (
                contract_id, contract_name, insurer, holder_type, fiscal_applicability,
                status, external_contributions_total, external_withdrawals_total, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("83914927", "HIMALIA", "Generali", "individual", "applicable", "active", 0.0, 0.0, "Test"),
        )
        conn.execute(
            """
            INSERT INTO documents (
                document_id, document_type, insurer, contract_name, asset_id, document_date,
                coverage_year, status, filepath, original_filename, sha256, notes, imported_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "doc_arb_test",
                "arbitration_letter",
                "Generali",
                "HIMALIA",
                None,
                "2026-03-29",
                None,
                "active",
                "documents/insurer/generali/himalia/courriers/2026-03-29_arbitrage_generali.pdf",
                "avenant - 83914927 - 22.pdf",
                "test",
                "test",
                imported_at,
            ),
        )
        for asset_id, asset_type, name, valuation_engine, isin in (
            ("struct_1", "structured_product", "EMTN Test", "event_based", "FR0014014T52"),
            ("uc_1", "uc_fund", "SICAV Test", "mark_to_market", "FR0010233726"),
        ):
            conn.execute(
                """
                INSERT INTO assets (
                    asset_id, asset_type, name, valuation_engine, isin, metadata_json, imported_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (asset_id, asset_type, name, valuation_engine, isin, "{}", imported_at),
            )
        for position_id, asset_id, invested_amount, units_held in (
            ("pos_struct", "struct_1", 26616.2, 26.6162),
            ("pos_uc", "uc_1", 0.0, 0.0),
        ):
            conn.execute(
                """
                INSERT INTO positions (
                    position_id, asset_id, holder_type, wrapper_type, insurer, contract_name,
                    subscription_date, invested_amount, units_held, purchase_nav,
                    purchase_nav_currency, purchase_nav_source, imported_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    position_id,
                    asset_id,
                    "individual",
                    "assurance_vie",
                    "Generali",
                    "HIMALIA",
                    "2025-01-01",
                    invested_amount,
                    units_held,
                    None,
                    "EUR",
                    None,
                    imported_at,
                ),
            )


def test_parse_arbitration_text():
    parsed = parse_arbitration_text(ARBITRAGE_TEXT)
    assert parsed["effective_date"] == "2026-03-26"
    assert parsed["amount"] == 26616.2
    assert len(parsed["from_legs"]) == 1
    assert len(parsed["to_legs"]) == 1
    assert parsed["from_legs"][0]["isin"] == "FR0014014T52"
    assert parsed["to_legs"][0]["isin"] == "FR0010233726"


def test_parse_swisslife_arbitration_text():
    parsed = parse_arbitration_text(SWISSLIFE_ARBITRAGE_TEXT)
    assert parsed["effective_date"] == "2025-12-12"
    assert len(parsed["from_legs"]) == 1
    assert len(parsed["to_legs"]) == 2
    assert parsed["from_legs"][0]["isin"] == "FR0013301629"
    assert parsed["from_legs"][0]["amount"] == 88120.6
    assert parsed["to_legs"][0]["isin"] == "LU1112771503"


def test_build_and_apply_arbitration_proposal(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    _write_test_data(data_dir)
    db_path = tmp_path / "db.sqlite"
    _seed_test_db(data_dir, db_path)

    monkeypatch.setattr(
        "portfolio_tracker.arbitration.extract_pdf_text",
        lambda _path: (ARBITRAGE_TEXT, "mock"),
    )

    proposal = build_arbitration_proposal_for_document(data_dir, "doc_arb_test", db_path=db_path)
    assert proposal["ok"] is True
    assert proposal["proposal"]["from_legs"][0]["mapping_status"] == "matched"
    assert proposal["proposal"]["to_legs"][0]["mapping_status"] == "matched"

    apply_result = apply_arbitration_proposal(data_dir, "doc_arb_test", db_path=db_path)
    assert apply_result["ok"] is True
    assert apply_result["created_lots"] == 2

    with connect(db_path) as conn:
        positions_count = conn.execute("SELECT COUNT(*) AS c FROM positions").fetchone()["c"]
        lots_count = conn.execute("SELECT COUNT(*) AS c FROM position_lots").fetchone()["c"]
        persisted = conn.execute(
            """
            SELECT raw_lot_type, effective_date, document_id
            FROM document_movements
            WHERE document_id = ?
            ORDER BY raw_lot_type
            """,
            ("doc_arb_test",),
        ).fetchall()
    assert positions_count == 2
    assert lots_count == 0
    assert len(persisted) == 2
    assert {row["raw_lot_type"] for row in persisted} == {"buy", "sell"}

    runtime = V2Runtime(data_dir, db_path=db_path)
    struct_lots = runtime.portfolio.get_position("pos_struct").investment.lots
    uc_lots = runtime.portfolio.get_position("pos_uc").investment.lots

    assert any(lot.get("type") == "sell" and str(lot.get("date")) == "2026-03-26" for lot in struct_lots)
    assert any(lot.get("type") == "buy" and str(lot.get("date")) == "2026-03-26" for lot in uc_lots)

    apply_second = apply_arbitration_proposal(data_dir, "doc_arb_test", db_path=db_path)
    assert apply_second["ok"] is True
    assert apply_second["created_lots"] == 0


def test_manual_mapping_required_before_apply(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    _write_test_data(data_dir)
    db_path = tmp_path / "db.sqlite"
    _seed_test_db(data_dir, db_path)

    # Force an unmatched "from" ISIN to validate manual mapping flow.
    text_unmatched = ARBITRAGE_TEXT.replace("FR0014014T52", "FR0000000001")
    monkeypatch.setattr(
        "portfolio_tracker.arbitration.extract_pdf_text",
        lambda _path: (text_unmatched, "mock"),
    )

    proposal = build_arbitration_proposal_for_document(data_dir, "doc_arb_test", db_path=db_path)
    assert proposal["proposal"]["from_legs"][0]["mapping_status"] == "unmatched"

    with pytest.raises(ValueError, match="non mappés"):
        apply_arbitration_proposal(data_dir, "doc_arb_test", db_path=db_path)

    mapped = save_arbitration_mappings(
        data_dir,
        "doc_arb_test",
        db_path=db_path,
        mappings=[{"direction": "from", "index": 0, "position_id": "pos_struct"}],
    )
    assert mapped["ok"] is True
    assert mapped["proposal"]["from_legs"][0]["mapping_status"] == "matched"

    applied = apply_arbitration_proposal(data_dir, "doc_arb_test", db_path=db_path)
    assert applied["ok"] is True
    assert applied["created_lots"] == 2


def test_name_similarity_suggestion(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    _write_test_data(data_dir)
    db_path = tmp_path / "db.sqlite"
    _seed_test_db(data_dir, db_path)

    text_name_only = ARBITRAGE_TEXT.replace("FR0014014T52", "FR0000000001")
    monkeypatch.setattr(
        "portfolio_tracker.arbitration.extract_pdf_text",
        lambda _path: (text_name_only, "mock"),
    )

    proposal = build_arbitration_proposal_for_document(data_dir, "doc_arb_test", db_path=db_path, force_refresh=True)
    from_leg = proposal["proposal"]["from_legs"][0]
    assert from_leg["mapping_status"] in {"suggested", "unmatched"}
    if from_leg["mapping_status"] == "suggested":
        assert from_leg["position_id"] == "pos_struct"


def test_runtime_can_load_portfolio_from_sqlite_without_yaml(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    _write_test_data(data_dir)
    db_path = tmp_path / "db.sqlite"
    _seed_test_db(data_dir, db_path)

    runtime = V2Runtime(data_dir, db_path=db_path, include_db_overlay=False)
    struct_asset = runtime.portfolio.get_asset("struct_1")
    struct_position = runtime.portfolio.get_position("pos_struct")

    assert struct_asset is not None
    assert struct_asset.name == "EMTN Test"
    assert struct_position is not None
    assert struct_position.wrapper.contract_name == "HIMALIA"
    assert struct_position.investment.lots == []
