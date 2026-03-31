from pathlib import Path
import yaml

from portfolio_tracker.market import build_v2_market_data, load_market_series
from portfolio_tracker.storage import default_db_path, get_market_series_points, upsert_market_series_points
from portfolio_tracker.web.app import STATIC_DIR


def test_v2_market_static_assets_exist():
    assert (STATIC_DIR / "v2-market.html").exists()
    assert (STATIC_DIR / "v2-market.js").exists()
    assert (STATIC_DIR / "v2.html").exists()
    assert (STATIC_DIR / "v2.css").exists()

    html = (STATIC_DIR / "v2-market.html").read_text(encoding="utf-8")
    js = (STATIC_DIR / "v2-market.js").read_text(encoding="utf-8")
    dashboard_html = (STATIC_DIR / "v2.html").read_text(encoding="utf-8")
    css = (STATIC_DIR / "v2.css").read_text(encoding="utf-8")

    assert 'select id="market-series-select"' in html
    assert 'id="market-include-former"' in html
    assert 'id="market-include-future"' in html
    assert '<option value="purchase" hidden>Date d\'achat</option>' in html
    assert 'id="market-series-meta"' in html
    assert "function defaultRange()" in js
    assert "function classifySeriesLifecycle(option)" in js
    assert "function prefixContracts(label, linkedContracts = [])" in js
    assert "function computeHoldingRangeGain(points, holdings)" in js
    assert "function purchaseDateForSelectedSeries()" in js
    assert 'document.querySelector("#market-series-select")' in js
    assert 'document.querySelector("#market-include-former")' in js
    assert 'document.querySelector("#market-include-future")' in js
    assert "thresholdLinesPlugin" in js
    assert "Gain/perte période estimé" in js
    assert "series-meta-grid" in css
    assert "checkbox-inline" in css
    assert "Actualiser" not in html
    assert "Actualiser" not in dashboard_html
    assert "display: inline-flex;" in css


def test_v2_market_data_lists_uc_and_underlyings():
    root = Path(__file__).resolve().parents[1]
    data_dir = root / "portfolio_tracker" / "data"

    payload = build_v2_market_data(data_dir)

    assert payload["summary"]["uc_count"] >= 1
    assert payload["summary"]["underlying_count"] >= 1
    assert any(row["asset_id"] == "uc_bdl_rempart_c" for row in payload["uc_assets"])
    assert any(row["earliest_date"] for row in payload["uc_assets"] if row["has_series"])
    assert any(row["product_names"] for row in payload["underlyings"])
    assert any("redemption_levels" in row for row in payload["underlyings"])
    assert any(row["earliest_date"] for row in payload["underlyings"] if row["has_series"])


def test_v2_market_data_exposes_morningstar_url_fallback_for_uc():
    root = Path(__file__).resolve().parents[1]
    data_dir = root / "portfolio_tracker" / "data"

    payload = build_v2_market_data(data_dir)

    bdl_rempart = next(row for row in payload["uc_assets"] if row["asset_id"] == "uc_bdl_rempart_c")
    assert bdl_rempart["source_url"] == "https://www.morningstar.fr/fr/funds/snapshot/snapshot.aspx?id=F000005GG8"


def test_v2_market_data_prefers_configured_source_url_for_boursorama_uc():
    root = Path(__file__).resolve().parents[1]
    data_dir = root / "portfolio_tracker" / "data"

    payload = build_v2_market_data(data_dir)

    echiquier = next(row for row in payload["uc_assets"] if row["asset_id"] == "uc_echiquier_allocation_flexible_b")
    assert echiquier["source_url"] == "https://www.boursorama.com/bourse/opcvm/cours/0P0001INWJ/"


def test_v2_market_series_filters_dates_for_uc_and_underlying():
    root = Path(__file__).resolve().parents[1]
    data_dir = root / "portfolio_tracker" / "data"

    uc_series = load_market_series(
        data_dir,
        kind="uc",
        identifier="uc_bdl_rempart_c",
        date_from="2026-03-01",
        date_to="2026-03-31",
    )
    assert len(uc_series["points"]) >= 1
    assert all("2026-03-01" <= point["date"] <= "2026-03-31" for point in uc_series["points"])

    underlying_series = load_market_series(
        data_dir,
        kind="underlying",
        identifier="euronext_fr:FRIX00001324-XPAR",
        date_from="2026-03-01",
        date_to="2026-03-31",
    )
    assert len(underlying_series["points"]) >= 1
    assert all("2026-03-01" <= point["date"] <= "2026-03-31" for point in underlying_series["points"])


def test_load_market_series_reads_from_sqlite_without_yaml(tmp_path):
    data_dir = tmp_path
    upsert_market_series_points(
        default_db_path(data_dir),
        kind="uc",
        identifier="uc_test_db",
        source="manual",
        currency="EUR",
        points=[
            {"date": "2026-03-01", "value": 101.2, "currency": "EUR", "source": "manual"},
            {"date": "2026-03-20", "value": 103.4, "currency": "EUR", "source": "manual"},
        ],
    )

    payload = load_market_series(
        data_dir,
        kind="uc",
        identifier="uc_test_db",
        date_from="2026-03-10",
        date_to="2026-03-31",
    )

    assert payload["points"] == [
        {"date": "2026-03-20", "value": 103.4, "currency": "EUR", "source": "manual"},
    ]


def test_load_market_series_lazy_syncs_yaml_into_sqlite(tmp_path):
    data_dir = tmp_path
    market_data_dir = data_dir / "market_data"
    market_data_dir.mkdir()
    (market_data_dir / "nav_uc_lazy.yaml").write_text(
        yaml.safe_dump(
            {
                "source": "legacy_yaml",
                "nav_history": [
                    {"date": "2026-03-01", "value": 99.1, "currency": "EUR", "source": "legacy_yaml"},
                    {"date": "2026-03-15", "value": 100.5, "currency": "EUR", "source": "legacy_yaml"},
                ],
            },
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    payload = load_market_series(data_dir, kind="uc", identifier="uc_lazy")

    assert len(payload["points"]) == 2
    stored_points = get_market_series_points(
        default_db_path(data_dir),
        kind="uc",
        identifier="uc_lazy",
    )
    assert [row["date"] for row in stored_points] == ["2026-03-01", "2026-03-15"]
