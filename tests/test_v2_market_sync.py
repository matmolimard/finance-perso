from datetime import date
import yaml

from portfolio_tracker.v2 import market_sync


def test_fetch_natixis_index_parses_textual_french_date_and_euro_level(monkeypatch):
    html = """
    <section>
      <script>let serieIndice = [[1774310400000, 780.12],[1774396800000, 789.84]];</script>
      <div>Niveau de l'indice</div>
      <div>789.84€</div>
      <div>au 25 Mar. 2026</div>
    </section>
    """

    monkeypatch.setattr(market_sync, "_http_get_text", lambda *args, **kwargs: html)

    result = market_sync.fetch_natixis_index(
        url="https://equityderivatives.natixis.com/fr/indice/luxgstdg/",
        identifier="LUXGSTDG",
    )

    assert result.source == "natixis"
    assert result.identifier == "LUXGSTDG"
    assert result.points == [
        (date(2026, 3, 24), 780.12),
        (date(2026, 3, 25), 789.84),
    ]


def test_parse_natixis_date_supports_slash_and_textual_formats():
    assert market_sync._parse_natixis_date("25/03/2026") == date(2026, 3, 25)
    assert market_sync._parse_natixis_date("25 Mar. 2026") == date(2026, 3, 25)
    assert market_sync._parse_natixis_date("25 mars 2026") == date(2026, 3, 25)


def test_fetch_nav_for_asset_id_parses_boursorama_tsv_latest_point(tmp_path, monkeypatch):
    market_data_dir = tmp_path / "market_data"
    market_data_dir.mkdir()
    (market_data_dir / "nav_sources.yaml").write_text(
        yaml.safe_dump(
            {
                "nav_sources": {
                    "uc_test_capimmo": {
                        "kind": "url_csv",
                        "url": "https://example.test/capimmo.tsv",
                        "delimiter": "\t",
                        "date_column": "date",
                        "value_column": "clot",
                        "date_format": "%d/%m/%Y %H:%M",
                        "currency": "EUR",
                        "source": "boursorama",
                    }
                }
            },
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    tsv = (
        "date\touv\thaut\tbas\tclot\tvol\tdevise\t\r\n"
        "03/10/2025 00:00\t225.38\t225.38\t225.38\t225.38\t0\tEUR\t\r\n"
        "20/03/2026 00:00\t224.13\t224.13\t224.13\t224.13\t0\tEUR\t\r\n"
    )
    monkeypatch.setattr(market_sync, "_http_get_bytes", lambda *args, **kwargs: tsv.encode("utf-8"))

    result = market_sync.fetch_nav_for_asset_id(
        market_data_dir=market_data_dir,
        asset_id="uc_test_capimmo",
        target_date=date(2026, 3, 28),
    )

    assert result is not None
    assert result.source == "boursorama"
    assert result.nav_date == date(2026, 3, 20)
    assert result.value == 224.13


def test_fetch_nav_history_for_asset_id_parses_boursorama_tsv_history(tmp_path, monkeypatch):
    market_data_dir = tmp_path / "market_data"
    market_data_dir.mkdir()
    (market_data_dir / "nav_sources.yaml").write_text(
        yaml.safe_dump(
            {
                "nav_sources": {
                    "uc_test_capimmo": {
                        "kind": "url_csv",
                        "url": "https://example.test/capimmo.tsv",
                        "delimiter": "\t",
                        "date_column": "date",
                        "value_column": "clot",
                        "date_format": "%d/%m/%Y %H:%M",
                        "currency": "EUR",
                        "source": "boursorama",
                    }
                }
            },
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    tsv = (
        "date\touv\thaut\tbas\tclot\tvol\tdevise\t\r\n"
        "03/10/2025 00:00\t225.38\t225.38\t225.38\t225.38\t0\tEUR\t\r\n"
        "10/10/2025 00:00\t225.59\t225.59\t225.59\t225.59\t0\tEUR\t\r\n"
        "20/03/2026 00:00\t224.13\t224.13\t224.13\t224.13\t0\tEUR\t\r\n"
    )
    monkeypatch.setattr(market_sync, "_http_get_bytes", lambda *args, **kwargs: tsv.encode("utf-8"))

    result = market_sync.fetch_nav_history_for_asset_id(
        market_data_dir=market_data_dir,
        asset_id="uc_test_capimmo",
        start_date=date(2025, 10, 1),
        end_date=date(2026, 3, 28),
    )

    assert [(row.nav_date, row.value, row.source) for row in result] == [
        (date(2025, 10, 3), 225.38, "boursorama"),
        (date(2025, 10, 10), 225.59, "boursorama"),
        (date(2026, 3, 20), 224.13, "boursorama"),
    ]
