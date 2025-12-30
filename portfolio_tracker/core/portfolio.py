"""
Portfolio - Gestion du portefeuille global
"""
from typing import List, Dict, Optional
from pathlib import Path
import yaml

from .asset import Asset, AssetType
from .position import Position, HolderType, WrapperType, Wrapper, Investment


class Portfolio:
    """
    Représente le portefeuille complet.
    
    Charge les assets et positions depuis les fichiers YAML,
    et fournit des méthodes pour interroger le portefeuille.
    """
    
    def __init__(self, data_dir: Path):
        """
        Initialise le portefeuille depuis un répertoire de données.
        
        Args:
            data_dir: Chemin vers le dossier contenant assets.yaml et positions.yaml
        """
        self.data_dir = Path(data_dir)
        self.assets: Dict[str, Asset] = {}
        self.positions: Dict[str, Position] = {}
        
        self._load_assets()
        self._load_positions()
    
    def _load_assets(self):
        """Charge et valide les actifs depuis assets.yaml"""
        from ..validation import validate_assets_file
        
        assets_file = self.data_dir / "assets.yaml"
        validated_assets, report = validate_assets_file(assets_file)
        
        if report.has_errors:
            error_summary = report.format_summary()
            raise ValueError(f"Validation assets.yaml échouée : {len(report.errors)} erreur(s)\n{error_summary}")
        
        if report.warnings:
            print(report.format_summary())
        
        for asset_schema in validated_assets:
            asset = Asset(
                asset_id=asset_schema.asset_id,
                asset_type=asset_schema.asset_type,
                name=asset_schema.name,
                valuation_engine=asset_schema.valuation_engine,
                isin=asset_schema.isin,
                metadata=asset_schema.metadata
            )
            self.assets[asset.asset_id] = asset
    
    def _load_positions(self):
        """Charge et valide les positions depuis positions.yaml"""
        from ..validation import validate_positions_file
        
        positions_file = self.data_dir / "positions.yaml"
        valid_asset_ids = set(self.assets.keys())
        
        validated_positions, report = validate_positions_file(positions_file, valid_asset_ids)
        
        if report.has_errors:
            error_summary = report.format_summary()
            raise ValueError(f"Validation positions.yaml échouée : {len(report.errors)} erreur(s)\n{error_summary}")
        
        if report.warnings:
            print(report.format_summary())
        
        for pos_schema in validated_positions:
            position = Position(
                position_id=pos_schema.position_id,
                asset_id=pos_schema.asset_id,
                holder_type=pos_schema.holder_type,
                wrapper=Wrapper(
                    wrapper_type=pos_schema.wrapper.wrapper_type,
                    insurer=pos_schema.wrapper.insurer,
                    contract_name=pos_schema.wrapper.contract_name
                ),
                investment=Investment(
                    subscription_date=pos_schema.investment.subscription_date,
                    invested_amount=float(pos_schema.investment.invested_amount) if pos_schema.investment.invested_amount is not None else None,
                    units_held=float(pos_schema.investment.units_held) if pos_schema.investment.units_held is not None else None,
                    purchase_nav=float(pos_schema.investment.purchase_nav) if pos_schema.investment.purchase_nav is not None else None,
                    purchase_nav_currency=pos_schema.investment.purchase_nav_currency,
                    purchase_nav_source=pos_schema.investment.purchase_nav_source,
                    lots=[
                        {
                            'date': lot.date,
                            'type': lot.type,
                            'gross_amount': float(lot.gross_amount) if lot.gross_amount is not None else None,
                            'fees_amount': float(lot.fees_amount) if lot.fees_amount is not None else None,
                            'net_amount': float(lot.net_amount) if lot.net_amount is not None else None,
                            'units': float(lot.units) if lot.units is not None else None,
                        }
                        for lot in pos_schema.investment.lots
                    ]
                )
            )
            self.positions[position.position_id] = position
    
    def get_asset(self, asset_id: str) -> Optional[Asset]:
        """Récupère un actif par son ID"""
        return self.assets.get(asset_id)
    
    def get_position(self, position_id: str) -> Optional[Position]:
        """Récupère une position par son ID"""
        return self.positions.get(position_id)
    
    def get_positions_by_asset(self, asset_id: str) -> List[Position]:
        """Récupère toutes les positions d'un actif donné"""
        return [p for p in self.positions.values() if p.asset_id == asset_id]
    
    def get_positions_by_holder(self, holder_type: HolderType) -> List[Position]:
        """Récupère toutes les positions d'un type de détenteur"""
        return [p for p in self.positions.values() if p.holder_type == holder_type]
    
    def get_positions_by_wrapper(self, wrapper_type: WrapperType) -> List[Position]:
        """Récupère toutes les positions dans un type d'enveloppe"""
        return [
            p for p in self.positions.values() 
            if p.wrapper.wrapper_type == wrapper_type
        ]
    
    def get_assets_by_type(self, asset_type: AssetType) -> List[Asset]:
        """Récupère tous les actifs d'un type donné"""
        return [a for a in self.assets.values() if a.asset_type == asset_type]
    
    def list_all_assets(self) -> List[Asset]:
        """Liste tous les actifs"""
        return list(self.assets.values())
    
    def list_all_positions(self) -> List[Position]:
        """Liste toutes les positions"""
        return list(self.positions.values())
    
    def __repr__(self) -> str:
        return f"Portfolio({len(self.assets)} assets, {len(self.positions)} positions)"

    def save_positions(self) -> Path:
        """
        Sauvegarde les positions courantes dans data/positions.yaml
        AVEC validation préalable (arrêt si erreur)
        """
        from ..validation import validate_positions_file
        import tempfile
        
        positions_file = self.data_dir / "positions.yaml"
        
        payload = {
            "positions": [p.to_dict() for p in self.positions.values()],
        }
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False, encoding='utf-8') as tmp:
            tmp_path = Path(tmp.name)
            yaml.safe_dump(payload, tmp, sort_keys=False, allow_unicode=True)
        
        try:
            valid_asset_ids = set(self.assets.keys())
            _, report = validate_positions_file(tmp_path, valid_asset_ids)
            
            if report.has_errors:
                tmp_path.unlink()
                error_summary = report.format_summary()
                raise ValueError(f"Validation pré-sauvegarde échouée : {len(report.errors)} erreur(s)\n{error_summary}")
            
            tmp_path.replace(positions_file)
            
        except Exception as e:
            if tmp_path.exists():
                tmp_path.unlink()
            raise
        
        return positions_file


