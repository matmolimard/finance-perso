"""
Mark-to-Market Engine - Valorisation au prix de marché (UC cotées)
"""
from datetime import date, datetime
from typing import Optional
from pathlib import Path
import yaml
from dataclasses import dataclass

from .base import BaseValuationEngine, ValuationResult
from ..core.asset import Asset
from ..core.position import Position


class MarkToMarketEngine(BaseValuationEngine):
    """
    Moteur de valorisation pour unités de compte cotées.
    
    Valorisation simple basée sur la VL (valeur liquidative) :
    - Récupération de la dernière VL disponible
    - Calcul : nombre de parts × VL
    - Performance cumulative
    """
    
    def valuate(
        self, 
        asset: Asset, 
        position: Position, 
        valuation_date: Optional[date] = None
    ) -> ValuationResult:
        """
        Valorise une UC cotée en utilisant la dernière VL disponible.
        """
        val_date = self._get_valuation_date(valuation_date)
        
        # Lots (achats multiples) -> unités détenues + PRU basé sur achats
        # On filtre les lots à val_date pour les valorisations historiques correctes.
        lots = position.investment.lots or []
        lots_units_total = 0.0
        buy_units_total = 0.0
        buy_amount_total = 0.0
        lots_has_data = False
        for lot in lots:
            if not isinstance(lot, dict):
                continue
            # Ignorer les lots postérieurs à val_date (valorisation historique)
            d = lot.get("date")
            if d is not None:
                try:
                    lot_date = d if hasattr(d, "year") else datetime.fromisoformat(str(d)).date()
                    if lot_date > val_date:
                        continue
                except Exception:
                    pass
            try:
                units = lot.get("units")
                if units is None:
                    continue
                units_f = float(units)
                if units_f == 0:
                    continue
                lots_units_total += units_f
                lots_has_data = True

                lot_type = str(lot.get("type") or "buy").lower()
                if lot_type == "buy" and units_f > 0:
                    net_amount = lot.get("net_amount")
                    fees = lot.get("fees_amount") or 0.0
                    gross = lot.get("gross_amount")
                    if net_amount is None:
                        # fallback gross - fees
                        if gross is not None:
                            net_amount = float(gross) - float(fees or 0.0)
                        else:
                            # fallback units * nav (si fourni)
                            if lot.get("nav") is not None:
                                net_amount = float(lot["nav"]) * units_f
                            else:
                                net_amount = None
                    if net_amount is not None:
                        buy_units_total += units_f
                        buy_amount_total += float(net_amount)
            except Exception:
                continue
        
        # Si lots disponibles (position a été gérée via lots), utiliser la somme filtrée à val_date.
        # Cela permet les valorisations historiques correctes (pas encore acheté → 0 unités).
        # Si aucun lot n'existe, utiliser units_held du YAML (position simple sans historique de lots).
        has_lots = any(isinstance(l, dict) for l in lots)
        units_held = position.investment.units_held
        if has_lots:
            units_held = lots_units_total  # somme filtrée à val_date (0 si pas encore acheté)
        elif units_held is None:
            units_held = 0.0
        # Accepter des valeurs très proches de 0 (erreurs d'arrondi)
        if units_held is not None and abs(float(units_held)) < 0.01:
            # Utiliser invested_amount du YAML comme source de vérité
            invested_amount = position.investment.invested_amount
            # Ne calculer depuis les lots que si invested_amount n'est pas défini dans le YAML
            if invested_amount is None and lots_has_data and buy_amount_total:
                invested_amount = float(buy_amount_total)
            # Fallback si toujours None
            if invested_amount is None:
                invested_amount = 0.0
            return ValuationResult(
                position_id=position.position_id,
                asset_id=asset.asset_id,
                valuation_date=val_date,
                current_value=0.0,
                invested_amount=invested_amount,
                status="ok",
                message="Position historique (vendue, units_held=0)"
            )
        
        # Charger la VL
        nav_data = self._load_nav(asset.asset_id, val_date)
        
        if not nav_data:
            return ValuationResult(
                position_id=position.position_id,
                asset_id=asset.asset_id,
                valuation_date=val_date,
                status="error",
                message="VL non disponible"
            )
        
        nav_value = nav_data['value']
        nav_date = nav_data['date']

        # Calculer la valorisation + VL d'achat (si possible)
        # Règle de priorité:
        # - purchase_nav "manual" => on respecte
        # - sinon, si lots => PRU net (source de vérité pour multi-achats)
        # - sinon, fallback derived (investi/parts)
        purchase_nav = position.investment.purchase_nav
        purchase_nav_source = position.investment.purchase_nav_source

        if purchase_nav is not None and purchase_nav_source == "manual":
            # ok
            pass
        elif lots_has_data and buy_units_total != 0:
            purchase_nav = buy_amount_total / buy_units_total
            purchase_nav_source = "lots"
        elif purchase_nav is not None and purchase_nav_source is None:
            # valeur présente mais non sourcée -> non confirmée
            purchase_nav_source = "unknown"

        if purchase_nav is None and position.investment.invested_amount and position.investment.units_held:
            try:
                if float(position.investment.units_held) != 0:
                    purchase_nav = float(position.investment.invested_amount) / float(position.investment.units_held)
                    purchase_nav_source = "derived"
            except Exception:
                purchase_nav = None

        # Calculer la valorisation (units_held reste la source de vérité si présent; sinon lots)
        units_for_valuation = None
        if position.investment.units_held is not None:
            units_for_valuation = float(position.investment.units_held)
        elif lots_has_data:
            units_for_valuation = float(lots_units_total)

        if units_for_valuation is not None:
            # Nombre de parts connu
            current_value = units_for_valuation * nav_value
        elif position.investment.invested_amount:
            # Montant investi connu, calculer les parts à partir de la VL initiale
            initial_nav_value = None
            if purchase_nav is not None:
                initial_nav_value = purchase_nav
            else:
                initial_nav = self._load_nav(
                    asset.asset_id,
                    position.investment.subscription_date
                )
                if initial_nav:
                    initial_nav_value = initial_nav["value"]

            if initial_nav_value is None:
                return ValuationResult(
                    position_id=position.position_id,
                    asset_id=asset.asset_id,
                    valuation_date=val_date,
                    status="error",
                    message="VL initiale non disponible pour calculer les parts (renseigner investment.purchase_nav ou nav_*.yaml)"
                )

            units = position.investment.invested_amount / initial_nav_value
            current_value = units * nav_value
        else:
            return ValuationResult(
                position_id=position.position_id,
                asset_id=asset.asset_id,
                valuation_date=val_date,
                status="error",
                message="Ni le nombre de parts ni le montant investi n'est spécifié"
            )
        
        # Vérifier la fraîcheur de la VL
        days_old = (val_date - nav_date).days
        if days_old > 7:
            status = "warning"
            message = f"VL datée de {days_old} jours ({nav_date})"
        else:
            status = "ok"
            message = f"VL : {nav_value} au {nav_date}"

        perf_pct = None
        if purchase_nav is not None:
            try:
                if float(purchase_nav) != 0:
                    perf_pct = ((float(nav_value) / float(purchase_nav)) - 1.0) * 100.0
            except Exception:
                perf_pct = None

        # Utiliser invested_amount du YAML comme source de vérité (capital investi actuel après retraits)
        # Ne calculer à partir des lots que si invested_amount n'est pas défini (None)
        # Note: invested_amount == 0.0 est une valeur valide (position rachetée/réinvestie)
        invested_amount = position.investment.invested_amount
        
        # Ne calculer depuis les lots que si invested_amount n'est pas défini dans le YAML
        if invested_amount is None and lots_has_data and buy_amount_total:
            invested_amount = float(buy_amount_total)
        
        # Fallback si toujours None
        if invested_amount is None:
            invested_amount = 0.0
        
        return ValuationResult(
            position_id=position.position_id,
            asset_id=asset.asset_id,
            valuation_date=val_date,
            current_value=current_value,
            invested_amount=invested_amount,
            status=status,
            message=message,
            metadata={
                "nav": nav_value,
                "nav_date": nav_date.isoformat(),
                "days_old": days_old,
                "purchase_nav": purchase_nav,
                "purchase_nav_source": purchase_nav_source,
                "perf_pct": perf_pct,
                "lots_units_total": lots_units_total if lots_has_data else None,
                "buy_units_total": buy_units_total if lots_has_data else None,
                "buy_amount_total": buy_amount_total if lots_has_data else None,
            }
        )
    
    def _load_nav(self, asset_id: str, target_date: date) -> Optional[dict]:
        """
        Charge la VL la plus récente avant ou égale à target_date.
        
        Returns:
            {'value': float, 'date': date} ou None
        """
        nav_file = self.market_data_dir / f"nav_{asset_id}.yaml"
        if not nav_file.exists():
            return None
        
        with open(nav_file, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
        
        if not data or 'nav_history' not in data:
            return None
        
        # Trouver la VL la plus récente <= target_date
        suitable_navs = []
        for nav_entry in data['nav_history']:
            nav_date = datetime.fromisoformat(nav_entry['date']).date()
            if nav_date <= target_date:
                suitable_navs.append({
                    'value': nav_entry['value'],
                    'date': nav_date
                })
        
        if not suitable_navs:
            return None
        
        # Retourner la plus récente
        return max(suitable_navs, key=lambda x: x['date'])


