"""
Tests d'intégration pour le chargement et la sauvegarde du Portfolio avec validation.
"""
import pytest
from pathlib import Path
import tempfile
import shutil
import yaml

from portfolio_tracker.core.portfolio import Portfolio
from portfolio_tracker.errors import PortfolioDataError


def create_test_data_dir(tmp_path):
    """Crée un répertoire de test avec assets et positions valides"""
    assets = {
        "assets": [
            {
                "asset_id": "test_struct",
                "type": "structured_product",
                "name": "Test Structured",
                "valuation_engine": "event_based"
            },
            {
                "asset_id": "test_euro",
                "type": "fonds_euro",
                "name": "Test Euro",
                "valuation_engine": "declarative"
            }
        ]
    }
    
    positions = {
        "positions": [
            {
                "position_id": "pos_001",
                "asset_id": "test_struct",
                "holder_type": "individual",
                "wrapper": {
                    "type": "assurance_vie",
                    "insurer": "Test",
                    "contract_name": "Test"
                },
                "investment": {
                    "subscription_date": "2024-01-01",
                    "invested_amount": 10000.0
                }
            }
        ]
    }
    
    (tmp_path / "assets.yaml").write_text(yaml.safe_dump(assets))
    (tmp_path / "positions.yaml").write_text(yaml.safe_dump(positions))
    (tmp_path / "market_data").mkdir()
    
    return tmp_path


class TestPortfolioLoadingValidation:
    """Tests chargement Portfolio avec validation"""
    
    def test_load_valid_portfolio(self, tmp_path):
        """Chargement portfolio valide"""
        data_dir = create_test_data_dir(tmp_path)
        
        portfolio = Portfolio(data_dir)
        
        assert len(portfolio.assets) == 2
        assert len(portfolio.positions) == 1
    
    def test_load_invalid_assets_raises(self, tmp_path):
        """Assets invalides => raise"""
        (tmp_path / "assets.yaml").write_text("{ invalid: [ yaml")
        (tmp_path / "positions.yaml").write_text(yaml.safe_dump({"positions": []}))
        
        with pytest.raises(ValueError, match="Validation assets.yaml échouée"):
            Portfolio(tmp_path)
    
    def test_load_invalid_position_reference_raises(self, tmp_path):
        """Position avec asset_id invalide => raise"""
        assets = {
            "assets": [{
                "asset_id": "valid",
                "type": "fonds_euro",
                "name": "Test",
                "valuation_engine": "declarative"
            }]
        }
        positions = {
            "positions": [{
                "position_id": "pos_001",
                "asset_id": "nonexistent",
                "holder_type": "individual",
                "wrapper": {
                    "type": "assurance_vie",
                    "insurer": "T",
                    "contract_name": "T"
                },
                "investment": {
                    "subscription_date": "2024-01-01"
                }
            }]
        }
        
        (tmp_path / "assets.yaml").write_text(yaml.safe_dump(assets))
        (tmp_path / "positions.yaml").write_text(yaml.safe_dump(positions))
        
        with pytest.raises(ValueError, match="Validation positions.yaml échouée"):
            Portfolio(tmp_path)


class TestPortfolioSaveValidation:
    """Tests sauvegarde avec validation pré-sauvegarde"""
    
    def test_save_valid_positions(self, tmp_path):
        """Sauvegarde positions valides"""
        data_dir = create_test_data_dir(tmp_path)
        portfolio = Portfolio(data_dir)
        
        saved_path = portfolio.save_positions()
        
        assert saved_path.exists()
        portfolio2 = Portfolio(data_dir)
        assert len(portfolio2.positions) == len(portfolio.positions)
    
    def test_save_corrupted_positions_raises(self, tmp_path):
        """Corruption manuelle => sauvegarde échoue"""
        data_dir = create_test_data_dir(tmp_path)
        portfolio = Portfolio(data_dir)
        
        pos = list(portfolio.positions.values())[0]
        pos.asset_id = "nonexistent_asset"
        
        with pytest.raises(ValueError, match="Validation pré-sauvegarde échouée"):
            portfolio.save_positions()


class TestNavValidation:
    """Tests validation fichiers NAV"""
    
    def test_load_valid_nav(self, tmp_path):
        """Chargement NAV valide"""
        from portfolio_tracker.market.nav_store import load_nav_history
        
        nav_file = tmp_path / "nav_test.yaml"
        nav_file.write_text(yaml.safe_dump({
            "nav_history": [
                {
                    "date": "2024-01-01",
                    "value": 100.50,
                    "currency": "EUR"
                }
            ]
        }))
        
        history = load_nav_history(nav_file)
        
        assert len(history) == 1
        assert history[0].value == 100.50
    
    def test_load_corrupted_nav_raises(self, tmp_path):
        """NAV corrompu => raise"""
        from portfolio_tracker.market.nav_store import load_nav_history
        
        nav_file = tmp_path / "nav_test.yaml"
        nav_file.write_text("{ invalid: [ yaml")
        
        with pytest.raises(PortfolioDataError, match="Fichier NAV corrompu"):
            load_nav_history(nav_file)
    
    def test_load_nav_with_invalid_entries(self, tmp_path, capsys):
        """NAV avec entrées invalides => warnings"""
        from portfolio_tracker.market.nav_store import load_nav_history
        
        nav_file = tmp_path / "nav_test.yaml"
        nav_file.write_text(yaml.safe_dump({
            "nav_history": [
                {
                    "date": "2024-01-01",
                    "value": 100.50,
                    "currency": "EUR"
                },
                {
                    "date": "2024-01-02",
                    "value": -50.0,
                    "currency": "EUR"
                }
            ]
        }))
        
        history = load_nav_history(nav_file)
        
        assert len(history) == 1
        captured = capsys.readouterr()
        assert "WARNING" in captured.out
        assert "ignorée" in captured.out

