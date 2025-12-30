"""
Rates Provider - Gestion des taux (CMS, etc.)
"""
from typing import Optional, Dict, Any, List, Tuple
from datetime import date, datetime
from pathlib import Path
import yaml

from .providers import MarketDataProvider


class RatesProvider(MarketDataProvider):
    """
    Fournisseur de taux d'intérêt et indices.
    
    Gère :
    - CMS (Constant Maturity Swap)
    - Taux sans risque
    - Indices de référence pour produits structurés
    """
    
    def get_data(
        self, 
        identifier: str, 
        data_type: str = "rate",
        target_date: Optional[date] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Récupère un taux à une date donnée.
        
        Args:
            identifier: Nom du taux (ex: "CMS10", "EURIBOR3M")
            data_type: Toujours "rate" pour ce provider
            target_date: Date cible
        
        Returns:
            {'value': float, 'date': date} ou None
        """
        if target_date is None:
            target_date = datetime.now().date()
        
        rates_file = self.data_dir / f"rates_{identifier}.yaml"
        if not rates_file.exists():
            return None
        
        with open(rates_file, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
        
        if not data or 'history' not in data:
            return None
        
        # Trouver le taux le plus proche de la date cible
        suitable_rates = []
        for rate_entry in data['history']:
            rate_date = datetime.fromisoformat(rate_entry['date']).date()
            if rate_date <= target_date:
                suitable_rates.append({
                    'value': rate_entry['value'],
                    'date': rate_date
                })
        
        if not suitable_rates:
            return None
        
        return max(suitable_rates, key=lambda x: x['date'])
    
    def is_data_available(self, identifier: str, data_type: str = "rate") -> bool:
        """Vérifie si des taux sont disponibles"""
        rates_file = self.data_dir / f"rates_{identifier}.yaml"
        return rates_file.exists()
    
    def get_latest_date(
        self, 
        identifier: str, 
        data_type: str = "rate"
    ) -> Optional[date]:
        """Retourne la date du dernier taux disponible"""
        data = self.get_data(identifier, data_type, date.max)
        return data['date'] if data else None
    
    def get_rate_history(
        self, 
        identifier: str,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None
    ) -> List[Dict[str, Any]]:
        """
        Récupère l'historique des taux sur une période.
        
        Args:
            identifier: Nom du taux
            start_date: Date de début (None = tout l'historique)
            end_date: Date de fin (None = aujourd'hui)
        
        Returns:
            Liste de {'value': float, 'date': date}
        """
        rates_file = self.data_dir / f"rates_{identifier}.yaml"
        if not rates_file.exists():
            return []
        
        with open(rates_file, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
        
        if not data or 'history' not in data:
            return []
        
        result = []
        for rate_entry in data['history']:
            rate_date = datetime.fromisoformat(rate_entry['date']).date()
            
            if start_date and rate_date < start_date:
                continue
            if end_date and rate_date > end_date:
                continue
            
            result.append({
                'value': rate_entry['value'],
                'date': rate_date
            })
        
        return sorted(result, key=lambda x: x['date'])
    
    def upsert_history(
        self,
        identifier: str,
        *,
        source: str,
        points: List[Tuple[date, float]],
        extra: Optional[Dict[str, Any]] = None
    ) -> int:
        """
        Upsert des points (date, value) dans le fichier rates_<id>.yaml.
        Retourne le nombre de points ajoutés/modifiés.
        """
        rates_file = self.data_dir / f"rates_{identifier}.yaml"
        data = {}
        if rates_file.exists():
            with open(rates_file, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f) or {}
        
        existing = {}
        for row in (data.get("history") or []):
            if not isinstance(row, dict):
                continue
            ds = row.get("date")
            if not ds:
                continue
            existing[str(ds)] = row
        
        changed = 0
        for d, v in points:
            key = d.isoformat()
            row = {"date": key, "value": float(v)}
            if key not in existing or float(existing[key].get("value")) != float(v):
                existing[key] = row
                changed += 1
        
        merged_history = sorted(existing.values(), key=lambda r: r["date"])
        
        out = {
            "identifier": identifier,
            "source": source,
            "units": data.get("units", "pct"),
            "last_updated": datetime.now().isoformat(timespec="seconds"),
            "history": merged_history,
        }
        if extra:
            out.update(extra)
        
        with open(rates_file, 'w', encoding='utf-8') as f:
            yaml.safe_dump(out, f, sort_keys=False, allow_unicode=True, width=100)
        
        return changed


