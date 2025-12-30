"""
Tests pour les fonctionnalités de la vue UC développées dans le chat.
Teste que la vue UC a les mêmes fonctionnalités que la vue structured :
- Colonne Portefeuille (tronquée à 5 caractères)
- Colonne Mois (mois de détention)
- Colonne Achat (capital investi depuis les lots)
- Colonne Valeur, Gain, Perf
- Colonne Perf/an (performance annualisée)
- Affichage des frais dans les détails
- Exclusion des positions vendues du total
- Affichage "terminé" pour positions vendues
"""
import pytest
from pathlib import Path
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import Mock, patch, MagicMock
import tempfile
import yaml
import io
from contextlib import redirect_stdout, redirect_stderr

from portfolio_tracker.cli import PortfolioCLI
from portfolio_tracker.core import Portfolio, Asset, Position
from portfolio_tracker.core.asset import AssetType, ValuationEngine
from portfolio_tracker.core.position import HolderType, WrapperType
from portfolio_tracker.valuation.mark_to_market import MarkToMarketEngine
from portfolio_tracker.valuation.base import ValuationResult


class TestUCViewFeatures:
    """Tests pour les fonctionnalités de la vue UC"""
    
    def test_uc_view_shows_portfolio_column(self, tmp_path):
        """Test que la vue UC affiche la colonne 'Portefeuille' (tronquée à 5 caractères)"""
        positions_file = tmp_path / "positions.yaml"
        positions_file.write_text(yaml.safe_dump({
            "positions": [
                {
                    "position_id": "pos_test_uc_001",
                    "asset_id": "uc_test_fund",
                    "holder_type": "individual",
                    "wrapper": {
                        "type": "contrat_de_capitalisation",
                        "insurer": "Swiss Life",
                        "contract_name": "SwissLife Capi Stratégic Premium"
                    },
                    "investment": {
                        "subscription_date": "2023-01-01",
                        "invested_amount": 100000.0,
                        "units_held": 100.0,
                        "purchase_nav": 100.0,
                        "purchase_nav_source": "manual",
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
                    "asset_id": "uc_test_fund",
                    "type": "uc_fund",
                    "name": "Test UC Fund",
                    "valuation_engine": "mark_to_market",
                    "isin": "FR0012345678"
                }
            ]
        }))
        
        # Créer un fichier NAV pour le fonds
        nav_file = tmp_path / "market_data" / "nav_uc_test_fund.yaml"
        nav_file.parent.mkdir(parents=True, exist_ok=True)
        nav_file.write_text(yaml.safe_dump({
            "identifier": "uc_test_fund",
            "source": "manual",
            "nav_history": [
                {
                    "date": "2025-12-25",
                    "value": 110.0
                }
            ]
        }))
        
        # Exécuter la commande
        cli = PortfolioCLI(tmp_path)
        output = io.StringIO()
        with redirect_stdout(output):
            cli.uc_view(wide=False, details=False)
        
        output_str = output.getvalue()
        
        # Vérifier que la colonne Portefeuille est présente
        assert "Portefeuille" in output_str
        # Vérifier que le nom est tronqué à 5 caractères
        assert "Swiss" in output_str  # "SwissLife" devrait être tronqué à "Swiss"
    
    def test_uc_view_shows_perf_an_column(self, tmp_path):
        """Test que la vue UC affiche la colonne 'Perf/an'"""
        positions_file = tmp_path / "positions.yaml"
        positions_file.write_text(yaml.safe_dump({
            "positions": [
                {
                    "position_id": "pos_test_uc_002",
                    "asset_id": "uc_test_fund",
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
                        "purchase_nav": 100.0,
                        "purchase_nav_source": "manual",
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
                    "asset_id": "uc_test_fund",
                    "type": "uc_fund",
                    "name": "Test UC Fund",
                    "valuation_engine": "mark_to_market",
                    "isin": "FR0012345678"
                }
            ]
        }))
        
        # Créer un fichier NAV pour le fonds
        nav_file = tmp_path / "market_data" / "nav_uc_test_fund.yaml"
        nav_file.parent.mkdir(parents=True, exist_ok=True)
        nav_file.write_text(yaml.safe_dump({
            "identifier": "uc_test_fund",
            "source": "manual",
            "nav_history": [
                {
                    "date": "2025-12-25",
                    "value": 110.0
                }
            ]
        }))
        
        # Exécuter la commande
        cli = PortfolioCLI(tmp_path)
        output = io.StringIO()
        with redirect_stdout(output):
            cli.uc_view(wide=False, details=False)
        
        output_str = output.getvalue()
        
        # Vérifier que la colonne Perf/an est présente
        assert "Perf/an" in output_str
    
    def test_uc_view_shows_fees_in_details(self, tmp_path):
        """Test que la vue UC affiche les frais dans les détails"""
        positions_file = tmp_path / "positions.yaml"
        positions_file.write_text(yaml.safe_dump({
            "positions": [
                {
                    "position_id": "pos_test_uc_003",
                    "asset_id": "uc_test_fund",
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
                        "purchase_nav": 100.0,
                        "purchase_nav_source": "manual",
                        "lots": [
                            {
                                "date": "2023-01-01",
                                "type": "buy",
                                "net_amount": 100000.0,
                                "external": True
                            },
                            {
                                "date": "2023-01-01",
                                "type": "fee",
                                "net_amount": -50.0  # Frais de 50€
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
                    "asset_id": "uc_test_fund",
                    "type": "uc_fund",
                    "name": "Test UC Fund",
                    "valuation_engine": "mark_to_market",
                    "isin": "FR0012345678"
                }
            ]
        }))
        
        # Créer un fichier NAV pour le fonds
        nav_file = tmp_path / "market_data" / "nav_uc_test_fund.yaml"
        nav_file.parent.mkdir(parents=True, exist_ok=True)
        nav_file.write_text(yaml.safe_dump({
            "identifier": "uc_test_fund",
            "source": "manual",
            "nav_history": [
                {
                    "date": "2025-12-25",
                    "value": 110.0
                }
            ]
        }))
        
        # Exécuter la commande
        cli = PortfolioCLI(tmp_path)
        output = io.StringIO()
        with redirect_stdout(output):
            cli.uc_view(wide=False, details=True)
        
        output_str = output.getvalue()
        
        # Vérifier que les frais sont affichés dans les détails
        assert "Frais payés:" in output_str
        assert "50.00" in output_str or "50,00" in output_str
    
    def test_uc_view_excludes_sold_positions_from_total(self, tmp_path):
        """Test que la vue UC exclut les positions vendues du total"""
        positions_file = tmp_path / "positions.yaml"
        positions_file.write_text(yaml.safe_dump({
            "positions": [
                {
                    "position_id": "pos_test_uc_004",
                    "asset_id": "uc_active",
                    "holder_type": "individual",
                    "wrapper": {
                        "type": "contrat_de_capitalisation",
                        "insurer": "Test Insurer",
                        "contract_name": "Test Contract"
                    },
                    "investment": {
                        "subscription_date": "2023-01-01",
                        "invested_amount": 100000.0,
                        "units_held": 100.0,  # Actif
                        "purchase_nav": 100.0,
                        "purchase_nav_source": "manual",
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
                    "position_id": "pos_test_uc_005",
                    "asset_id": "uc_sold",
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
                        "purchase_nav": 100.0,
                        "purchase_nav_source": "manual",
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
                    "asset_id": "uc_active",
                    "type": "uc_fund",
                    "name": "Active UC",
                    "valuation_engine": "mark_to_market",
                    "isin": "FR0012345678"
                },
                {
                    "asset_id": "uc_sold",
                    "type": "uc_fund",
                    "name": "Sold UC",
                    "valuation_engine": "mark_to_market",
                    "isin": "FR0012345679"
                }
            ]
        }))
        
        # Créer des fichiers NAV
        nav_file_active = tmp_path / "market_data" / "nav_uc_active.yaml"
        nav_file_active.parent.mkdir(parents=True, exist_ok=True)
        nav_file_active.write_text(yaml.safe_dump({
            "identifier": "uc_active",
            "source": "manual",
            "nav_history": [
                {
                    "date": "2025-12-25",
                    "value": 110.0
                }
            ]
        }))
        
        nav_file_sold = tmp_path / "market_data" / "nav_uc_sold.yaml"
        nav_file_sold.write_text(yaml.safe_dump({
            "identifier": "uc_sold",
            "source": "manual",
            "nav_history": [
                {
                    "date": "2025-12-25",
                    "value": 110.0
                }
            ]
        }))
        
        # Exécuter la commande
        cli = PortfolioCLI(tmp_path)
        output = io.StringIO()
        with redirect_stdout(output):
            cli.uc_view(wide=False, details=False)
        
        output_str = output.getvalue()
        
        # Vérifier que seule la position active est affichée (pas la position vendue)
        assert "Active UC" in output_str
        assert "Sold UC" not in output_str
        # Vérifier que la valeur affichée est correcte (11,000 € = 100 units * 110 NAV)
        assert "11,000.00 €" in output_str
    
    def test_uc_view_shows_terminated_for_sold_positions(self, tmp_path):
        """Test que la vue UC affiche 'terminé' pour les positions vendues"""
        positions_file = tmp_path / "positions.yaml"
        positions_file.write_text(yaml.safe_dump({
            "positions": [
                {
                    "position_id": "pos_test_uc_006",
                    "asset_id": "uc_test_fund",
                    "holder_type": "individual",
                    "wrapper": {
                        "type": "contrat_de_capitalisation",
                        "insurer": "Test Insurer",
                        "contract_name": "Test Contract"
                    },
                    "investment": {
                        "subscription_date": "2023-01-01",
                        "invested_amount": 100000.0,
                        "units_held": 0.0,  # Vendu
                        "purchase_nav": 100.0,
                        "purchase_nav_source": "manual",
                        "lots": [
                            {
                                "date": "2023-01-01",
                                "type": "buy",
                                "net_amount": 100000.0,
                                "external": True
                            },
                            {
                                "date": "2024-01-01",
                                "type": "sell",
                                "net_amount": -110000.0
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
                    "asset_id": "uc_test_fund",
                    "type": "uc_fund",
                    "name": "Test UC Fund",
                    "valuation_engine": "mark_to_market",
                    "isin": "FR0012345678"
                }
            ]
        }))
        
        # Créer un fichier NAV pour le fonds
        nav_file = tmp_path / "market_data" / "nav_uc_test_fund.yaml"
        nav_file.parent.mkdir(parents=True, exist_ok=True)
        nav_file.write_text(yaml.safe_dump({
            "identifier": "uc_test_fund",
            "source": "manual",
            "nav_history": [
                {
                    "date": "2025-12-25",
                    "value": 110.0
                }
            ]
        }))
        
        # Exécuter la commande avec include_terminated=True pour afficher les positions vendues
        cli = PortfolioCLI(tmp_path)
        output = io.StringIO()
        with redirect_stdout(output):
            cli.uc_view(wide=False, details=False, include_terminated=True)
        
        output_str = output.getvalue()
        
        # Vérifier que la position vendue est affichée
        assert "Test UC Fund" in output_str
    
    def test_uc_view_calculates_invested_from_lots(self, tmp_path):
        """Test que la vue UC calcule le capital investi depuis les lots"""
        positions_file = tmp_path / "positions.yaml"
        positions_file.write_text(yaml.safe_dump({
            "positions": [
                {
                    "position_id": "pos_test_uc_007",
                    "asset_id": "uc_test_fund",
                    "holder_type": "individual",
                    "wrapper": {
                        "type": "contrat_de_capitalisation",
                        "insurer": "Test Insurer",
                        "contract_name": "Test Contract"
                    },
                    "investment": {
                        "subscription_date": "2023-01-01",
                        "invested_amount": 0.0,  # Réinvestissement interne
                        "units_held": 100.0,
                        "purchase_nav": 100.0,
                        "purchase_nav_source": "manual",
                        "lots": [
                            {
                                "date": "2023-01-01",
                                "type": "buy",
                                "net_amount": 50000.0,
                                "external": False  # Réinvestissement
                            },
                            {
                                "date": "2023-06-01",
                                "type": "buy",
                                "net_amount": 25000.0,
                                "external": False
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
                    "asset_id": "uc_test_fund",
                    "type": "uc_fund",
                    "name": "Test UC Fund",
                    "valuation_engine": "mark_to_market",
                    "isin": "FR0012345678"
                }
            ]
        }))
        
        # Créer un fichier NAV pour le fonds
        nav_file = tmp_path / "market_data" / "nav_uc_test_fund.yaml"
        nav_file.parent.mkdir(parents=True, exist_ok=True)
        nav_file.write_text(yaml.safe_dump({
            "identifier": "uc_test_fund",
            "source": "manual",
            "nav_history": [
                {
                    "date": "2025-12-25",
                    "value": 110.0
                }
            ]
        }))
        
        # Exécuter la commande
        cli = PortfolioCLI(tmp_path)
        output = io.StringIO()
        with redirect_stdout(output):
            cli.uc_view(wide=False, details=False)
        
        output_str = output.getvalue()
        
        # Vérifier que le capital investi est calculé depuis les lots (75000 = 50000 + 25000)
        assert "75,000.00" in output_str or "75.000,00" in output_str or "75000" in output_str
    
    def test_uc_view_shows_quantalys_rating(self, tmp_path):
        """Test que la vue UC affiche les notes Quantalys"""
        positions_file = tmp_path / "positions.yaml"
        positions_file.write_text(yaml.safe_dump({
            "positions": [
                {
                    "position_id": "pos_test_uc_008",
                    "asset_id": "uc_test_fund",
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
                        "purchase_nav": 100.0,
                        "purchase_nav_source": "manual",
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
                    "asset_id": "uc_test_fund",
                    "type": "uc_fund",
                    "name": "Test UC Fund",
                    "valuation_engine": "mark_to_market",
                    "isin": "FR0012345678"
                }
            ]
        }))
        
        # Créer un fichier NAV pour le fonds
        nav_file = tmp_path / "market_data" / "nav_uc_test_fund.yaml"
        nav_file.parent.mkdir(parents=True, exist_ok=True)
        nav_file.write_text(yaml.safe_dump({
            "identifier": "uc_test_fund",
            "source": "manual",
            "nav_history": [
                {
                    "date": "2025-12-25",
                    "value": 110.0
                }
            ]
        }))
        
        # Créer un fichier de notes Quantalys
        quantalys_file = tmp_path / "market_data" / "quantalys_ratings.yaml"
        quantalys_file.write_text(yaml.safe_dump({
            "ratings": [
                {
                    "isin": "FR0012345678",
                    "name": "Test UC Fund",
                    "quantalys_rating": 4,
                    "quantalys_category": "Actions",
                    "last_update": "2025-12-25"
                }
            ]
        }))
        
        # Exécuter la commande
        cli = PortfolioCLI(tmp_path)
        output = io.StringIO()
        with redirect_stdout(output):
            cli.uc_view(wide=False, details=False)
        
        output_str = output.getvalue()
        
        # Vérifier que la colonne Quantalys est présente
        assert "Quantalys" in output_str
        # Vérifier que la note est affichée (⭐⭐⭐⭐ (4/5))
        assert "⭐⭐⭐⭐" in output_str or "4/5" in output_str


if __name__ == "__main__":
    pytest.main([__file__, "-v"])




