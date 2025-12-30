"""
Risk Profiles - Définition des profils de risque par contrat
"""
from dataclasses import dataclass
from typing import Optional, List
from pathlib import Path
import yaml


@dataclass
class RiskProfile:
    """Profil de risque pour un contrat/enveloppe"""
    name: str
    contract_name: str
    insurer: str
    risk_tolerance: str  # "conservative" | "moderate" | "aggressive"
    performance_priority: bool
    max_volatility: Optional[float] = None
    preferred_asset_classes: Optional[List[str]] = None
    excluded_asset_classes: Optional[List[str]] = None
    description: Optional[str] = None
    
    def __post_init__(self):
        if self.preferred_asset_classes is None:
            self.preferred_asset_classes = []
        if self.excluded_asset_classes is None:
            self.excluded_asset_classes = []
    
    @classmethod
    def from_dict(cls, data: dict) -> 'RiskProfile':
        """Crée un RiskProfile depuis un dictionnaire"""
        return cls(
            name=data['name'],
            contract_name=data['contract_name'],
            insurer=data['insurer'],
            risk_tolerance=data['risk_tolerance'],
            performance_priority=data.get('performance_priority', False),
            max_volatility=data.get('max_volatility'),
            preferred_asset_classes=data.get('preferred_asset_classes'),
            excluded_asset_classes=data.get('excluded_asset_classes'),
            description=data.get('description'),
        )
    
    def matches_position(self, contract_name: str, insurer: str) -> bool:
        """Vérifie si ce profil correspond à une position donnée"""
        return (
            self.contract_name == contract_name and
            self.insurer == insurer
        )


def load_profiles(data_dir: Path) -> List[RiskProfile]:
    """
    Charge les profils de risque depuis profiles.yaml
    
    Args:
        data_dir: Répertoire contenant profiles.yaml
        
    Returns:
        Liste des profils de risque
    """
    profiles_file = Path(data_dir) / "profiles.yaml"
    
    if not profiles_file.exists():
        return []
    
    with open(profiles_file, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)
    
    if not data or 'profiles' not in data:
        return []
    
    return [RiskProfile.from_dict(profile_data) for profile_data in data['profiles']]


def get_profile_for_position(
    profiles: List[RiskProfile],
    contract_name: str,
    insurer: str
) -> Optional[RiskProfile]:
    """
    Trouve le profil correspondant à une position
    
    Args:
        profiles: Liste des profils disponibles
        contract_name: Nom du contrat
        insurer: Nom de l'assureur
        
    Returns:
        Le profil correspondant ou None
    """
    for profile in profiles:
        if profile.matches_position(contract_name, insurer):
            return profile
    return None

