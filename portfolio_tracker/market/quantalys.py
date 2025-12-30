"""
Quantalys Provider - Gestion des notes Quantalys des fonds
"""
from typing import Optional, Dict, Any, List
from pathlib import Path
from datetime import date
import yaml

from .providers import MarketDataProvider


class QuantalysProvider(MarketDataProvider):
    """
    Fournisseur de notes Quantalys pour les fonds.
    
    Gère :
    - Notes globales Quantalys (1 à 5 étoiles)
    - Catégories de fonds
    - Dates de mise à jour
    """
    
    def __init__(self, data_dir: Path):
        super().__init__(data_dir)
        self._ratings_cache = None
    
    def _load_ratings(self) -> Dict[str, Dict[str, Any]]:
        """
        Charge le fichier des notes Quantalys.
        
        Returns:
            Dict {isin: {rating, category, ...}}
        """
        if self._ratings_cache is not None:
            return self._ratings_cache
        
        ratings_file = self.data_dir / "quantalys_ratings.yaml"
        if not ratings_file.exists():
            self._ratings_cache = {}
            return self._ratings_cache
        
        with open(ratings_file, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
        
        if not data or 'ratings' not in data:
            self._ratings_cache = {}
            return self._ratings_cache
        
        # Indexer par ISIN
        ratings = {}
        for rating_entry in data['ratings']:
            isin = rating_entry.get('isin')
            if isin:
                ratings[isin] = {
                    'name': rating_entry.get('name'),
                    'rating': rating_entry.get('quantalys_rating'),
                    'category': rating_entry.get('quantalys_category'),
                    'last_update': rating_entry.get('last_update'),
                    'notes': rating_entry.get('notes', '')
                }
        
        self._ratings_cache = ratings
        return self._ratings_cache
    
    def get_rating(self, isin: str) -> Optional[Dict[str, Any]]:
        """
        Récupère la note Quantalys pour un ISIN donné.
        
        Args:
            isin: Code ISIN du fonds
        
        Returns:
            {'rating': int, 'category': str, ...} ou None si non trouvé
        """
        ratings = self._load_ratings()
        return ratings.get(isin)
    
    def get_rating_display(self, isin: str) -> str:
        """
        Retourne une représentation visuelle de la note Quantalys.
        
        Args:
            isin: Code ISIN du fonds
        
        Returns:
            Chaîne formatée (ex: "⭐⭐⭐⭐ (4/5)" ou "N/A")
        """
        rating_info = self.get_rating(isin)
        if not rating_info:
            return "N/A"
        
        rating = rating_info.get('rating')
        if rating is None:
            return "Non noté"
        
        # Convertir la note en étoiles
        stars = "⭐" * rating
        return f"{stars} ({rating}/5)"
    
    def get_data(
        self, 
        identifier: str, 
        data_type: str = "rating",
        **kwargs
    ) -> Optional[Dict[str, Any]]:
        """
        Interface générique MarketDataProvider.
        
        Args:
            identifier: Code ISIN
            data_type: Type de données (toujours "rating")
        
        Returns:
            Dict avec les informations de notation
        """
        return self.get_rating(identifier)
    
    def is_data_available(self, identifier: str, data_type: str = "rating") -> bool:
        """
        Vérifie si une note Quantalys est disponible pour un ISIN.
        
        Args:
            identifier: Code ISIN
            data_type: Type de données (toujours "rating")
        
        Returns:
            True si la note existe
        """
        ratings = self._load_ratings()
        return identifier in ratings
    
    def get_latest_date(self, identifier: str, data_type: str = "rating") -> Optional[date]:
        """
        Retourne la date de la dernière mise à jour de la note Quantalys.
        
        Args:
            identifier: Code ISIN
            data_type: Type de données (toujours "rating")
        
        Returns:
            Date de la dernière mise à jour, ou None
        """
        from datetime import datetime
        
        rating_info = self.get_rating(identifier)
        if not rating_info:
            return None
        
        last_update_str = rating_info.get('last_update')
        if not last_update_str:
            return None
        
        try:
            return datetime.fromisoformat(last_update_str).date()
        except (ValueError, AttributeError):
            return None
    
    def upsert_rating(
        self,
        isin: str,
        name: str,
        rating: Optional[int],
        category: Optional[str] = None,
        update_date: Optional[date] = None
    ) -> bool:
        """
        Ajoute ou met à jour une note Quantalys dans le fichier.
        
        Args:
            isin: Code ISIN du fonds
            name: Nom du fonds
            rating: Note Quantalys (1-5) ou None si non noté
            category: Catégorie Quantalys
            update_date: Date de mise à jour (défaut: aujourd'hui)
        
        Returns:
            True si le fichier a été modifié
        """
        if update_date is None:
            from datetime import datetime
            update_date = datetime.now().date()
        
        ratings_file = self.data_dir / "quantalys_ratings.yaml"
        
        # Charger le fichier existant
        if ratings_file.exists():
            with open(ratings_file, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f) or {}
        else:
            data = {}
        
        if 'ratings' not in data:
            data['ratings'] = []
        
        # Chercher si l'ISIN existe déjà
        existing_entry = None
        for entry in data['ratings']:
            if entry.get('isin') == isin:
                existing_entry = entry
                break
        
        # Préparer la nouvelle entrée
        new_entry = {
            'isin': isin,
            'name': name,
            'quantalys_rating': rating,
            'quantalys_category': category,
            'last_update': update_date.isoformat(),
            'notes': 'Note globale Quantalys' if rating else 'Non noté par Quantalys'
        }
        
        # Vérifier si des changements sont nécessaires
        if existing_entry:
            # Comparer les valeurs
            changed = (
                existing_entry.get('quantalys_rating') != rating or
                existing_entry.get('quantalys_category') != category or
                existing_entry.get('name') != name
            )
            if changed:
                # Mettre à jour l'entrée existante
                existing_entry.update(new_entry)
            else:
                return False
        else:
            # Ajouter la nouvelle entrée
            data['ratings'].append(new_entry)
            changed = True
        
        # Sauvegarder le fichier
        if changed:
            # Trier par ISIN pour garder un ordre cohérent
            data['ratings'].sort(key=lambda x: x.get('isin', ''))
            
            with open(ratings_file, 'w', encoding='utf-8') as f:
                # Écrire l'en-tête
                f.write("# Notes Quantalys des fonds\n")
                f.write("# Source: https://www.quantalys.com\n")
                f.write(f"# Dernière mise à jour: {update_date.isoformat()}\n\n")
                # Écrire les données
                yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
            
            # Invalider le cache
            self._ratings_cache = None
        
        return changed

