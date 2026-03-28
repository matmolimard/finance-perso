from pathlib import Path

from portfolio_tracker.v2.market import build_v2_market_data, load_market_series
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
    underlyings = {row["underlying_id"]: row for row in payload["underlyings"]}
    assert "euronext_fr:FRIX00001324-XPAR" in underlyings
    assert underlyings["euronext_fr:FRIX00001324-XPAR"]["product_names"]
    assert "redemption_levels" in underlyings["euronext_fr:FRIX00001324-XPAR"]
    assert underlyings["euronext_fr:FRIX00001324-XPAR"]["earliest_date"]


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

    capimmo = next(row for row in payload["uc_assets"] if row["asset_id"] == "uc_sci_primonial_capimmo")
    assert capimmo["source_url"] == "https://www.boursorama.com/bourse/opcvm/cours/0P0001XVJK/"


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
