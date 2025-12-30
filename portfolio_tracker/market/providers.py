"""
Providers - Interface pour les fournisseurs de données de marché
"""
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
from datetime import date
from pathlib import Path


class MarketDataProvider(ABC):
    """
    Interface abstraite pour les fournisseurs de données de marché.
    
    Les implémentations concrètes peuvent :
    - Lire depuis des fichiers YAML locaux
    - Interroger des APIs (si network disponible)
    - Utiliser des bases de données
    """
    
    def __init__(self, data_dir: Path):
        """
        Initialise le provider avec un répertoire de données.
        
        Args:
            data_dir: Chemin vers le dossier market_data/
        """
        self.data_dir = Path(data_dir)
    
    @abstractmethod
    def get_data(
        self, 
        identifier: str, 
        data_type: str,
        target_date: Optional[date] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Récupère une donnée de marché.
        
        Args:
            identifier: Identifiant de l'instrument (ISIN, ticker, etc.)
            data_type: Type de donnée ("nav", "rate", "price", etc.)
            target_date: Date cible (None = aujourd'hui)
        
        Returns:
            Dictionnaire avec les données, ou None si non disponible
        """
        pass
    
    @abstractmethod
    def is_data_available(
        self, 
        identifier: str, 
        data_type: str
    ) -> bool:
        """
        Vérifie si des données sont disponibles pour un instrument.
        
        Args:
            identifier: Identifiant de l'instrument
            data_type: Type de donnée
        
        Returns:
            True si des données existent
        """
        pass
    
    @abstractmethod
    def get_latest_date(
        self, 
        identifier: str, 
        data_type: str
    ) -> Optional[date]:
        """
        Retourne la date de la dernière donnée disponible.
        
        Args:
            identifier: Identifiant de l'instrument
            data_type: Type de donnée
        
        Returns:
            Date de la dernière donnée, ou None
        """
        pass


