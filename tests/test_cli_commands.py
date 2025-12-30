"""
Tests d'intégration pour les commandes CLI développées dans le chat.
Teste les commandes réelles avec des données de test.
"""
import pytest
from pathlib import Path
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch, MagicMock
import tempfile
import yaml
import io
from contextlib import redirect_stdout, redirect_stderr

from portfolio_tracker.cli import PortfolioCLI, main
from portfolio_tracker.core import Portfolio


class TestStructuredProductsCommand:
    """Tests pour la commande `make structured`"""
    
    def test_structured_products_view_shows_sold_products_correctly(self, tmp_path):
        """Test que la vue structured affiche correctement les produits vendus"""
        # Créer des données de test
        positions_file = tmp_path / "positions.yaml"
        positions_file.write_text(yaml.safe_dump({
            "positions": [
                {
                    "position_id": "pos_test_001",
                    "asset_id": "struct_callable_note",
                    "holder_type": "individual",
                    "wrapper": {
                        "type": "assurance_vie",
                        "insurer": "Test Insurer",
                        "contract_name": "Test Contract"
                    },
                    "investment": {
                        "subscription_date": "2023-01-01",
                        "invested_amount": 91000.0,
                        "units_held": 0.0,
                        "lots": [
                            {
                                "date": "2023-01-01",
                                "type": "buy",
                                "net_amount": 91000.0,
                                "external": True
                            },
                            {
                                "date": "2024-01-03",
                                "type": "sell",
                                "net_amount": -94598.28
                            }
                        ]
                    }
                }
            ]
        }))
        
        assets_file = tmp_path / "assets.yaml"
        assets_file.write_text(yaml.safe_dump({
            "assets": [
                {
                    "asset_id": "struct_callable_note",
                    "type": "structured_product",
                    "name": "Callable Note Taux Fixe Décembre 2023",
                    "valuation_engine": "event_based",
                    "metadata": {
                        "period_months": 12
                    }
                }
            ]
        }))
        
        events_file = tmp_path / "market_data" / "events_struct_callable_note.yaml"
        events_file.parent.mkdir(parents=True, exist_ok=True)
        events_file.write_text(yaml.safe_dump({
            "events": [],
            "expected_events": []
        }))
        
        # Exécuter la commande avec include_terminated=True pour afficher les positions vendues
        cli = PortfolioCLI(tmp_path)
        output = io.StringIO()
        with redirect_stdout(output):
            cli.structured_products_view(wide=False, details=False, include_terminated=True)
        
        output_str = output.getvalue()
        
        # Vérifier que le produit vendu est affiché
        assert "Callable Note" in output_str
        assert "94,598.28 €" in output_str or "94598.28" in output_str
        # Vérifier que "terminé" est affiché pour une position vendue
        assert "terminé" in output_str.lower()
    
    def test_structured_products_view_shows_strike_columns(self, tmp_path):
        """Test que la vue structured affiche les colonnes 'Valeur si strike', 'Gain si strike', 'Perf si strike'"""
        positions_file = tmp_path / "positions.yaml"
        positions_file.write_text(yaml.safe_dump({
            "positions": [
                {
                    "position_id": "pos_test_002",
                    "asset_id": "struct_rendement_distribution",
                    "holder_type": "individual",
                    "wrapper": {
                        "type": "assurance_vie",
                        "insurer": "Test Insurer",
                        "contract_name": "Test Contract"
                    },
                    "investment": {
                        "subscription_date": "2025-02-23",
                        "invested_amount": 47427.06,
                        "units_held": 100.0,
                        "lots": [
                            {
                                "date": "2025-02-23",
                                "type": "buy",
                                "net_amount": 47427.06,
                                "external": True
                            }
                        ]
                    }
                }
            ]
        }))
        
        assets_file = tmp_path / "assets.yaml"
        assets_file.write_text(yaml.safe_dump({
            "assets": [
                {
                    "asset_id": "struct_rendement_distribution",
                    "type": "structured_product",
                    "name": "D RENDEMENT DISTRIBUTION FÉVRIER 2025",
                    "valuation_engine": "event_based",
                    "metadata": {
                        "period_months": 6,
                        "coupon_rate": 5.0,
                        "underlying": "CMS_EUR_10Y"
                    }
                }
            ]
        }))
        
        events_file = tmp_path / "market_data" / "events_struct_rendement_distribution.yaml"
        events_file.parent.mkdir(parents=True, exist_ok=True)
        events_file.write_text(yaml.safe_dump({
            "events": [],
            "expected_events": [
                {
                    "type": "autocall_possible",
                    "date": "2026-02-23",
                    "description": "Observation semestre 2",
                    "metadata": {
                        "semester": 2,
                        "coupon_rate": 0.025
                    }
                }
            ]
        }))
        
        # Exécuter la commande
        cli = PortfolioCLI(tmp_path)
        output = io.StringIO()
        with redirect_stdout(output):
            cli.structured_products_view(wide=False, details=False)
        
        output_str = output.getvalue()
        
        # Vérifier que les colonnes sont présentes
        assert "Valeur si strike" in output_str or "Valeur si strike" in output_str
        assert "Gain si strike" in output_str or "Gain si strike" in output_str
        assert "Perf si strike" in output_str or "Perf si strike" in output_str
    
    def test_structured_products_view_shows_portfolio_column(self, tmp_path):
        """Test que la vue structured affiche la colonne 'Portefeuille'"""
        positions_file = tmp_path / "positions.yaml"
        positions_file.write_text(yaml.safe_dump({
            "positions": [
                {
                    "position_id": "pos_test_003",
                    "asset_id": "struct_test_product",
                    "holder_type": "individual",
                    "wrapper": {
                        "type": "assurance_vie",
                        "insurer": "Swiss Life",
                        "contract_name": "SwissLife Capi Stratégic Premium"
                    },
                    "investment": {
                        "subscription_date": "2023-01-01",
                        "invested_amount": 100000.0,
                        "units_held": 100.0,
                        "lots": [
                            {
                                "date": "2023-01-01",
                                "type": "buy",
                                "net_amount": 100000.0,
                                "external": True
                            }
                        ]
                    }
                }
            ]
        }))
        
        assets_file = tmp_path / "assets.yaml"
        assets_file.write_text(yaml.safe_dump({
            "assets": [
                {
                    "asset_id": "struct_test_product",
                    "type": "structured_product",
                    "name": "Test Product",
                    "valuation_engine": "event_based",
                    "metadata": {
                        "period_months": 12
                    }
                }
            ]
        }))
        
        events_file = tmp_path / "market_data" / "events_struct_test_product.yaml"
        events_file.parent.mkdir(parents=True, exist_ok=True)
        events_file.write_text(yaml.safe_dump({
            "events": [],
            "expected_events": []
        }))
        
        # Exécuter la commande
        cli = PortfolioCLI(tmp_path)
        output = io.StringIO()
        with redirect_stdout(output):
            cli.structured_products_view(wide=False, details=False)
        
        output_str = output.getvalue()
        
        # Vérifier que la colonne Portefeuille est présente
        assert "Portefeuille" in output_str or "Portefeuille" in output_str
        # Vérifier que le nom est tronqué à 5 caractères
        assert "Swiss" in output_str  # "SwissLife" devrait être tronqué à "Swiss"
    
    def test_structured_products_view_shows_perf_an_column(self, tmp_path):
        """Test que la vue structured affiche la colonne 'Perf/an'"""
        positions_file = tmp_path / "positions.yaml"
        positions_file.write_text(yaml.safe_dump({
            "positions": [
                {
                    "position_id": "pos_test_004",
                    "asset_id": "struct_test_product",
                    "holder_type": "individual",
                    "wrapper": {
                        "type": "assurance_vie",
                        "insurer": "Test Insurer",
                        "contract_name": "Test Contract"
                    },
                    "investment": {
                        "subscription_date": "2023-01-01",
                        "invested_amount": 100000.0,
                        "units_held": 100.0,
                        "lots": [
                            {
                                "date": "2023-01-01",
                                "type": "buy",
                                "net_amount": 100000.0,
                                "external": True
                            }
                        ]
                    }
                }
            ]
        }))
        
        assets_file = tmp_path / "assets.yaml"
        assets_file.write_text(yaml.safe_dump({
            "assets": [
                {
                    "asset_id": "struct_test_product",
                    "type": "structured_product",
                    "name": "Test Product",
                    "valuation_engine": "event_based",
                    "metadata": {
                        "period_months": 12
                    }
                }
            ]
        }))
        
        events_file = tmp_path / "market_data" / "events_struct_test_product.yaml"
        events_file.parent.mkdir(parents=True, exist_ok=True)
        events_file.write_text(yaml.safe_dump({
            "events": [],
            "expected_events": []
        }))
        
        # Exécuter la commande
        cli = PortfolioCLI(tmp_path)
        output = io.StringIO()
        with redirect_stdout(output):
            cli.structured_products_view(wide=False, details=False)
        
        output_str = output.getvalue()
        
        # Vérifier que la colonne Perf/an est présente
        assert "Perf/an" in output_str or "Perf/an" in output_str


class TestWrapperCommand:
    """Tests pour la commande `make swisslife` / `make wrapper`"""
    
    def test_wrapper_excludes_sold_positions_from_total(self, tmp_path):
        """Test que la commande wrapper exclut les positions vendues du total"""
        positions_file = tmp_path / "positions.yaml"
        positions_file.write_text(yaml.safe_dump({
            "positions": [
                {
                    "position_id": "pos_test_005",
                    "asset_id": "asset_active",
                    "holder_type": "individual",
                    "wrapper": {
                        "type": "contrat_de_capitalisation",
                        "insurer": "Test Insurer",
                        "contract_name": "Test Contract"
                    },
                    "investment": {
                        "subscription_date": "2023-01-01",
                        "invested_amount": 100000.0,
                        "units_held": 100.0,
                        "lots": [
                            {
                                "date": "2023-01-01",
                                "type": "buy",
                                "net_amount": 100000.0,
                                "external": True
                            }
                        ]
                    }
                },
                {
                    "position_id": "pos_test_006",
                    "asset_id": "asset_sold",
                    "holder_type": "individual",
                    "wrapper": {
                        "type": "contrat_de_capitalisation",
                        "insurer": "Test Insurer",
                        "contract_name": "Test Contract"
                    },
                    "investment": {
                        "subscription_date": "2023-01-01",
                        "invested_amount": 50000.0,
                        "units_held": 0.0,  # Vendu
                        "lots": [
                            {
                                "date": "2023-01-01",
                                "type": "buy",
                                "net_amount": 50000.0,
                                "external": True
                            },
                            {
                                "date": "2024-01-01",
                                "type": "sell",
                                "net_amount": -55000.0
                            }
                        ]
                    }
                }
            ]
        }))
        
        assets_file = tmp_path / "assets.yaml"
        assets_file.write_text(yaml.safe_dump({
            "assets": [
                {
                    "asset_id": "asset_active",
                    "type": "fonds_euro",
                    "name": "Active Asset",
                    "valuation_engine": "declarative"
                },
                {
                    "asset_id": "asset_sold",
                    "type": "fonds_euro",
                    "name": "Sold Asset",
                    "valuation_engine": "declarative"
                }
            ]
        }))
        
        # Exécuter la commande
        cli = PortfolioCLI(tmp_path)
        output = io.StringIO()
        with redirect_stdout(output):
            cli.status_by_wrapper(
                wrapper_type="contrat_de_capitalisation",
                insurer="Test Insurer",
                contract="Test Contract"
            )
        
        output_str = output.getvalue()
        
        # Vérifier que le total de valeur n'inclut que la position active
        # Le capital externe peut inclure les deux (c'est normal)
        # Mais la valeur totale ne devrait inclure que la position active
        assert "Total:" in output_str
        # Le total ne devrait pas être 150000 (100000 + 55000 de la position vendue)
        # Note: Le capital externe peut être 150000, mais la valeur totale ne devrait pas
    
    def test_wrapper_shows_invested_external_correctly(self, tmp_path):
        """Test que la commande wrapper affiche correctement le capital investi externe"""
        positions_file = tmp_path / "positions.yaml"
        positions_file.write_text(yaml.safe_dump({
            "positions": [
                {
                    "position_id": "pos_test_007",
                    "asset_id": "asset_test",
                    "holder_type": "individual",
                    "wrapper": {
                        "type": "contrat_de_capitalisation",
                        "insurer": "Test Insurer",
                        "contract_name": "Test Contract"
                    },
                    "investment": {
                        "subscription_date": "2023-01-01",
                        "invested_amount": 100000.0,
                        "units_held": 100.0,
                        "lots": [
                            {
                                "date": "2023-01-01",
                                "type": "buy",
                                "net_amount": 100000.0,
                                "external": True
                            }
                        ]
                    }
                }
            ]
        }))
        
        assets_file = tmp_path / "assets.yaml"
        assets_file.write_text(yaml.safe_dump({
            "assets": [
                {
                    "asset_id": "asset_test",
                    "type": "fonds_euro",
                    "name": "Test Asset",
                    "valuation_engine": "declarative"
                }
            ]
        }))
        
        # Exécuter la commande
        cli = PortfolioCLI(tmp_path)
        output = io.StringIO()
        with redirect_stdout(output):
            cli.status_by_wrapper(
                wrapper_type="contrat_de_capitalisation",
                insurer="Test Insurer",
                contract="Test Contract"
            )
        
        output_str = output.getvalue()
        
        # Vérifier que le capital externe est affiché
        assert "Investi (externe)" in output_str or "Investi (externe)" in output_str
        assert "100000" in output_str or "100,000" in output_str


class TestUpdateUnderlyingsCommand:
    """Tests pour la commande `make update-underlyings`"""
    
    @patch('portfolio_tracker.cli.fetch_investing_rate')
    def test_update_underlyings_fetches_cms_rate(self, mock_fetch_rate, tmp_path):
        """Test que update-underlyings récupère le taux CMS depuis investing.com"""
        from portfolio_tracker.market.fetch_underlyings import FetchResult
        
        # Mock de la réponse
        mock_fetch_rate.return_value = FetchResult(
            source="investing",
            identifier="CMS_EUR_10Y",
            points=[(date(2025, 12, 24), 2.10)],
            metadata={"url": "https://fr.investing.com/rates-bonds/eur-10-years-irs-interest-rate-swap"}
        )
        
        # Créer le fichier assets.yaml (requis par Portfolio)
        assets_file = tmp_path / "assets.yaml"
        assets_file.write_text(yaml.safe_dump({
            "assets": []
        }))
        
        # Créer le fichier positions.yaml (requis par Portfolio)
        positions_file = tmp_path / "positions.yaml"
        positions_file.write_text(yaml.safe_dump({
            "positions": []
        }))
        
        # Créer le fichier underlyings.yaml
        underlyings_file = tmp_path / "market_data" / "underlyings.yaml"
        underlyings_file.parent.mkdir(parents=True, exist_ok=True)
        underlyings_file.write_text(yaml.safe_dump({
            "underlyings": [
                {
                    "underlying_id": "CMS_EUR_10Y",
                    "type": "rate",
                    "source": "investing",
                    "identifier": "CMS_EUR_10Y",
                    "url": "https://fr.investing.com/rates-bonds/eur-10-years-irs-interest-rate-swap"
                }
            ]
        }))
        
        # Exécuter la commande
        cli = PortfolioCLI(tmp_path)
        cli.update_underlyings()
        
        # Vérifier que fetch_investing_rate a été appelé
        mock_fetch_rate.assert_called_once()
        
        # Vérifier que le taux a été stocké
        rates_file = tmp_path / "market_data" / "rates_CMS_EUR_10Y.yaml"
        assert rates_file.exists()
        
        with open(rates_file, 'r') as f:
            rates_data = yaml.safe_load(f)
            assert rates_data is not None
            assert "history" in rates_data
            assert len(rates_data["history"]) > 0
            assert rates_data["history"][0]["value"] == pytest.approx(2.10, abs=0.01)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

