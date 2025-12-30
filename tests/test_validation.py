"""
Tests unitaires pour les schémas et la validation.
"""
import pytest
from pathlib import Path
from decimal import Decimal
from datetime import date
import tempfile
import yaml

from portfolio_tracker.schemas import (
    AssetSchema, PositionSchema, InvestmentSchema,
    LotSchema, AssetType, ValuationEngine, HolderType,
    WrapperType, WrapperSchema
)
from portfolio_tracker.validation import (
    validate_assets_file, validate_positions_file,
    ValidationSeverity
)
from pydantic import ValidationError


class TestAssetSchema:
    """Tests schéma Asset"""
    
    def test_asset_valid(self):
        """Asset valide"""
        asset = AssetSchema(
            asset_id="test_001",
            asset_type=AssetType.STRUCTURED_PRODUCT,
            name="Test Asset",
            valuation_engine=ValuationEngine.EVENT_BASED
        )
        assert asset.asset_id == "test_001"
    
    def test_asset_empty_id(self):
        """asset_id vide interdit"""
        with pytest.raises(ValidationError):
            AssetSchema(
                asset_id="   ",
                asset_type=AssetType.STRUCTURED_PRODUCT,
                name="Test",
                valuation_engine=ValuationEngine.EVENT_BASED
            )
    
    def test_asset_invalid_engine_for_type(self):
        """Moteur incompatible avec type"""
        with pytest.raises(ValidationError):
            AssetSchema(
                asset_id="test_001",
                asset_type=AssetType.STRUCTURED_PRODUCT,
                name="Test",
                valuation_engine=ValuationEngine.DECLARATIVE
            )
    
    def test_asset_isin_format(self):
        """Format ISIN validé"""
        with pytest.raises(ValidationError):
            AssetSchema(
                asset_id="test_001",
                asset_type=AssetType.UC_FUND,
                name="Test",
                valuation_engine=ValuationEngine.MARK_TO_MARKET,
                isin="INVALID"
            )


class TestLotSchema:
    """Tests schéma Lot"""
    
    def test_lot_valid(self):
        """Lot valide"""
        lot = LotSchema(
            date=date(2024, 1, 15),
            type="buy",
            gross_amount=Decimal("1000.00"),
            fees_amount=Decimal("50.00"),
            net_amount=Decimal("950.00")
        )
        assert lot.net_amount == Decimal("950.00")
    
    def test_lot_incoherent_amounts(self):
        """net != gross - fees"""
        with pytest.raises(ValidationError):
            LotSchema(
                date=date(2024, 1, 15),
                type="buy",
                gross_amount=Decimal("1000.00"),
                fees_amount=Decimal("50.00"),
                net_amount=Decimal("900.00")
            )
    
    def test_lot_missing_amounts(self):
        """Aucun montant fourni"""
        with pytest.raises(ValidationError):
            LotSchema(
                date=date(2024, 1, 15),
                type="buy"
            )


class TestInvestmentSchema:
    """Tests schéma Investment"""
    
    def test_investment_purchase_nav_without_source(self):
        """purchase_nav sans source est accepté (la source sera dérivée si nécessaire)"""
        # Le schéma accepte purchase_nav sans source explicite
        # La logique de validation de la source est dans les moteurs de valorisation
        investment = InvestmentSchema(
            subscription_date=date(2024, 1, 1),
            purchase_nav=Decimal("100.50")
        )
        assert investment.purchase_nav == Decimal("100.50")
    
    def test_investment_allows_invested_amount_with_lots(self):
        """invested_amount peut être différent de la somme des lots (calculé par LotClassifier)"""
        # Le schéma accepte invested_amount même si différent des lots
        # La logique de calcul est maintenant dans LotClassifier, pas dans le schéma
        investment = InvestmentSchema(
            subscription_date=date(2024, 1, 1),
            invested_amount=Decimal("10000.00"),
            lots=[
                LotSchema(
                    date=date(2024, 1, 15),
                    type="buy",
                    net_amount=Decimal("5000.00"),
                    external=True
                )
            ]
        )
        assert investment.invested_amount == Decimal("10000.00")


class TestValidateAssetsFile:
    """Tests validation fichier assets.yaml"""
    
    def test_valid_assets_file(self, tmp_path):
        """Fichier valide"""
        assets_file = tmp_path / "assets.yaml"
        assets_file.write_text(yaml.safe_dump({
            "assets": [
                {
                    "asset_id": "test_001",
                    "type": "structured_product",
                    "name": "Test Struct",
                    "valuation_engine": "event_based"
                }
            ]
        }))
        
        assets, report = validate_assets_file(assets_file)
        
        assert len(assets) == 1
        assert not report.has_errors
        assert assets[0].asset_id == "test_001"
    
    def test_missing_file(self, tmp_path):
        """Fichier manquant"""
        assets_file = tmp_path / "nonexistent.yaml"
        
        assets, report = validate_assets_file(assets_file)
        
        assert len(assets) == 0
        assert report.has_errors
        assert any("introuvable" in i.message for i in report.errors)
    
    def test_duplicate_asset_id(self, tmp_path):
        """asset_id dupliqué"""
        assets_file = tmp_path / "assets.yaml"
        assets_file.write_text(yaml.safe_dump({
            "assets": [
                {
                    "asset_id": "duplicate",
                    "type": "fonds_euro",
                    "name": "Asset 1",
                    "valuation_engine": "declarative"
                },
                {
                    "asset_id": "duplicate",
                    "type": "fonds_euro",
                    "name": "Asset 2",
                    "valuation_engine": "declarative"
                }
            ]
        }))
        
        assets, report = validate_assets_file(assets_file)
        
        assert len(assets) == 1
        assert report.has_errors
        assert any("dupliqué" in i.message for i in report.errors)
    
    def test_invalid_yaml(self, tmp_path):
        """YAML malformé"""
        assets_file = tmp_path / "assets.yaml"
        assets_file.write_text("{ invalid yaml [[[")
        
        assets, report = validate_assets_file(assets_file)
        
        assert len(assets) == 0
        assert report.has_errors
        assert any("parsing YAML" in i.message for i in report.errors)


class TestValidatePositionsFile:
    """Tests validation fichier positions.yaml"""
    
    def test_valid_positions_file(self, tmp_path):
        """Fichier valide"""
        positions_file = tmp_path / "positions.yaml"
        positions_file.write_text(yaml.safe_dump({
            "positions": [
                {
                    "position_id": "pos_001",
                    "asset_id": "asset_001",
                    "holder_type": "individual",
                    "wrapper": {
                        "type": "assurance_vie",
                        "insurer": "Test Insurer",
                        "contract_name": "Test Contract"
                    },
                    "investment": {
                        "subscription_date": "2024-01-01",
                        "invested_amount": 10000.0
                    }
                }
            ]
        }))
        
        valid_asset_ids = {"asset_001"}
        positions, report = validate_positions_file(positions_file, valid_asset_ids)
        
        assert len(positions) == 1
        assert not report.has_errors
    
    def test_invalid_asset_reference(self, tmp_path):
        """asset_id inexistant"""
        positions_file = tmp_path / "positions.yaml"
        positions_file.write_text(yaml.safe_dump({
            "positions": [
                {
                    "position_id": "pos_001",
                    "asset_id": "nonexistent",
                    "holder_type": "individual",
                    "wrapper": {
                        "type": "assurance_vie",
                        "insurer": "Test",
                        "contract_name": "Test"
                    },
                    "investment": {
                        "subscription_date": "2024-01-01"
                    }
                }
            ]
        }))
        
        valid_asset_ids = {"asset_001"}
        positions, report = validate_positions_file(positions_file, valid_asset_ids)
        
        assert len(positions) == 0
        assert report.has_errors
        assert any("asset_id invalide" in i.message for i in report.errors)


class TestEdgeCases:
    """Tests cas limites"""
    
    def test_invested_amount_zero_valid(self):
        """invested_amount=0 valide (réinvestissement)"""
        inv = InvestmentSchema(
            subscription_date=date(2024, 1, 1),
            invested_amount=Decimal("0.0")
        )
        assert inv.invested_amount == Decimal("0.0")
    
    def test_invested_amount_none_with_lots(self):
        """invested_amount=None avec lots (calculé dynamiquement)"""
        inv = InvestmentSchema(
            subscription_date=date(2024, 1, 1),
            invested_amount=None,
            lots=[
                LotSchema(
                    date=date(2024, 1, 15),
                    type="buy",
                    net_amount=Decimal("5000.00")
                )
            ]
        )
        assert inv.invested_amount is None
    
    def test_units_held_zero_valid(self):
        """units_held=0 valide (position historique)"""
        inv = InvestmentSchema(
            subscription_date=date(2024, 1, 1),
            units_held=Decimal("0.0")
        )
        assert inv.units_held == Decimal("0.0")
    
    def test_lot_net_only(self):
        """Lot avec net_amount seul"""
        lot = LotSchema(
            date=date(2024, 1, 15),
            type="buy",
            net_amount=Decimal("1000.00")
        )
        assert lot.net_amount == Decimal("1000.00")
    
    def test_lot_gross_only(self):
        """Lot avec gross_amount seul"""
        lot = LotSchema(
            date=date(2024, 1, 15),
            type="buy",
            gross_amount=Decimal("1000.00")
        )
        assert lot.gross_amount == Decimal("1000.00")
    
    def test_empty_lots_list(self):
        """lots vide"""
        inv = InvestmentSchema(
            subscription_date=date(2024, 1, 1),
            lots=[]
        )
        assert inv.lots == []
    
    def test_negative_fee(self):
        """fees_amount négatif invalide"""
        with pytest.raises(ValidationError):
            LotSchema(
                date=date(2024, 1, 15),
                type="buy",
                net_amount=Decimal("1000.00"),
                fees_amount=Decimal("-50.00")
            )

