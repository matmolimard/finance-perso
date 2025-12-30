"""
Declarative Engine - Valorisation déclarative (fonds euros)
"""
from datetime import date, datetime
from typing import Optional, Dict, List, Tuple
from pathlib import Path
import yaml

from .base import BaseValuationEngine, ValuationResult
from ..core.asset import Asset
from ..core.position import Position


class DeclarativeEngine(BaseValuationEngine):
    """
    Moteur de valorisation pour fonds euros.
    
    Accepte explicitement l'opacité :
    - Pas de calcul théorique
    - Stockage de taux déclarés avec source
    - Rendement inconnu marqué explicitement
    - Aucune extrapolation
    """
    
    def valuate(
        self, 
        asset: Asset, 
        position: Position, 
        valuation_date: Optional[date] = None
    ) -> ValuationResult:
        """
        Valorise un fonds euro en se basant sur les taux déclarés.
        
        La valorisation utilise uniquement les informations déclarées
        par l'assureur, sans aucune estimation.
        """
        val_date = self._get_valuation_date(valuation_date)
        
        # Charger les taux déclarés
        rates = self._load_declared_rates(asset.asset_id)
        lots = position.investment.lots or []
        # Utiliser invested_amount du YAML comme source de vérité (capital investi actuel après retraits)
        # Ne calculer à partir des lots que si invested_amount n'est pas défini (None)
        # Note: invested_amount == 0.0 est une valeur valide (position rachetée/réinvestie)
        invested = position.investment.invested_amount

        # Si lots présents: on valorise via cashflows (buys + fees/taxes/income)
        cashflows = self._extract_cashflows(lots)
        use_cashflows = bool(cashflows)
        
        # Ne calculer depuis les lots que si invested_amount n'est pas défini dans le YAML
        if invested is None and use_cashflows:
            invested = sum(amt for (_, kind, amt) in cashflows if kind == "buy" and amt is not None)
        
        # Fallback si toujours None
        if invested is None:
            invested = 0.0
        # Valeur "sans rendement" (= somme des cashflows) : reflète au moins taxes/frais même si taux manquants
        raw_value = None
        if use_cashflows:
            raw_value = sum(amt for (_, _, amt) in cashflows)
        
        if not rates:
            # Dataset partiel : pas de taux déclarés.
            # Pour un fonds euro, units_held représente la valeur en euros (1 unit = 1 €)
            # Utiliser units_held tel quel comme valeur actuelle (il est généralement déjà à jour)
            units_held = position.investment.units_held
            if units_held is not None:
                try:
                    current_value = float(units_held)
                except (ValueError, TypeError) as e:
                    from ..errors import PortfolioDataError
                    raise PortfolioDataError(
                        f"Position {position.position_id} : units_held invalide ({units_held!r}): {e}"
                    )
                status = "warning"
                message = f"Valeur déclarée (units_held): {current_value:,.2f} € - Aucun taux déclaré disponible"
            else:
                current_value = raw_value if raw_value is not None else invested
                status = "warning"
                message = "Aucun taux déclaré disponible (valeur = cashflows / capital investi)"
            
            return ValuationResult(
                position_id=position.position_id,
                asset_id=asset.asset_id,
                valuation_date=val_date,
                current_value=current_value,
                invested_amount=invested,
                status=status,
                message=message,
                metadata={"opacity_acknowledged": True, "raw_value": raw_value, "units_held_value": units_held}
            )
        
        # Calculer la valorisation basée sur les taux déclarés
        subscription_date = position.investment.subscription_date
        
        # Trouver la dernière année avec un taux disponible
        last_available_year = max(rates.keys()) if rates else None
        
        # Si on valorise après la dernière année avec un taux, utiliser units_held comme base
        # et ajouter les cashflows après la dernière année avec taux (versements sans intérêts)
        # (units_held est généralement la valeur au 31/12 de la dernière année avec taux)
        if last_available_year and val_date.year > last_available_year:
            units_held = position.investment.units_held
            if units_held is not None:
                try:
                    base_value = float(units_held)
                except (ValueError, TypeError) as e:
                    from ..errors import PortfolioDataError
                    raise PortfolioDataError(
                        f"Position {position.position_id} : units_held invalide ({units_held!r}): {e}"
                    )
                
                # Calculer la valeur théorique au 31/12 de la dernière année avec taux
                # pour vérifier si units_held est à jour ou non
                theoretical_value_end_year = None
                if use_cashflows:
                    # Calculer avec les cashflows jusqu'au 31/12 de la dernière année
                    theoretical_value_end_year = self._compute_value_from_cashflows(
                        cashflows=cashflows,
                        valuation_date=date(last_available_year, 12, 31),
                        rates=rates,
                    )
                else:
                    theoretical_value_end_year = self._compute_value_from_rates(
                        invested,
                        subscription_date,
                        date(last_available_year, 12, 31),
                        rates
                    )
                
                # Si units_held est proche de la valeur théorique au 31/12, c'est la valeur au 31/12
                # Sinon, units_held est probablement déjà à jour (inclut les cashflows récents)
                if theoretical_value_end_year and abs(base_value - theoretical_value_end_year) < abs(base_value) * 0.1:
                    # units_held = valeur au 31/12, ajouter les cashflows après
                    cashflows_after_last_year = []
                    if use_cashflows:
                        for cf_date, cf_kind, cf_amt in cashflows:
                            if cf_date.year > last_available_year:
                                cashflows_after_last_year.append((cf_date, cf_kind, cf_amt))
                    
                    additional_value = sum(amt for (_, _, amt) in cashflows_after_last_year)
                    current_value = base_value + additional_value
                    
                    if cashflows_after_last_year:
                        status = "ok"
                        message = f"Valeur {last_available_year} ({base_value:,.2f} €) + versements {last_available_year + 1}+ ({additional_value:,.2f} €) - Taux disponibles jusqu'en {last_available_year}"
                    else:
                        status = "ok"
                        message = f"Valeur déclarée (units_held): {current_value:,.2f} € - Taux disponibles jusqu'en {last_available_year}"
                else:
                    # units_held est probablement déjà à jour (inclut les cashflows récents)
                    current_value = base_value
                    status = "ok"
                    message = f"Valeur déclarée (units_held): {current_value:,.2f} € - Taux disponibles jusqu'en {last_available_year}"
            else:
                current_value = None
        else:
            # Calculer avec les taux jusqu'à la date de valorisation
            if use_cashflows:
                current_value = self._compute_value_from_cashflows(
                    cashflows=cashflows,
                    valuation_date=val_date,
                    rates=rates,
                )
                subscription_date = position.investment.subscription_date
            else:
                # fallback historique: un seul montant investi
                subscription_date = position.investment.subscription_date
                current_value = self._compute_value_from_rates(
                    invested,
                    subscription_date,
                    val_date,
                    rates
                )
        
        # Si impossible de calculer, utiliser units_held ou le montant investi
        if current_value is None:
            units_held = position.investment.units_held
            if units_held is not None:
                try:
                    current_value = float(units_held)
                except (ValueError, TypeError) as e:
                    from ..errors import PortfolioDataError
                    raise PortfolioDataError(
                        f"Position {position.position_id} : units_held invalide ({units_held!r}): {e}"
                    )
            else:
                current_value = raw_value if raw_value is not None else invested
        
        # Identifier la dernière mise à jour
        if rates:
            latest_rate = max(rates.items(), key=lambda x: x[0])
            latest_year, latest_data = latest_rate
        else:
            latest_year = None
            latest_data = {}
        
        # Vérifier si les données sont récentes
        if latest_year is None:
            status = "warning"
            message = "Aucun taux déclaré disponible"
        elif latest_year < val_date.year - 1:
            status = "warning"
            message = f"Dernière mise à jour : {latest_year}"
        else:
            status = "ok"
            message = f"Taux {latest_year} : {latest_data.get('rate', 'N/A')}%"
        
        return ValuationResult(
            position_id=position.position_id,
            asset_id=asset.asset_id,
            valuation_date=val_date,
            current_value=current_value,
            invested_amount=invested,
            status=status,
            message=message,
            metadata={
                "declared_rates": rates,
                "latest_year": latest_year,
                "opacity_acknowledged": True,
                "cashflows_count": len(cashflows) if use_cashflows else 0,
            }
        )
    
    def _load_declared_rates(self, asset_id: str) -> Dict[int, Dict]:
        """
        Charge les taux déclarés pour un fonds euro.
        
        Returns:
            Dict {année: {'rate': X.XX, 'source': '...', 'date': '...'}}
        """
        rates_file = self.market_data_dir / f"fonds_euro_{asset_id}.yaml"
        if not rates_file.exists():
            return {}
        
        with open(rates_file, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
        
        if not data or 'declared_rates' not in data:
            return {}
        
        # Convertir en dict {année: données}
        rates = {}
        for rate_entry in data['declared_rates']:
            year = rate_entry['year']
            rates[year] = {
                'rate': rate_entry.get('rate'),
                'source': rate_entry.get('source', 'unknown'),
                'date': rate_entry.get('date')
            }
        
        return rates
    
    def _compute_value_from_rates(
        self,
        invested: float,
        subscription_date: date,
        valuation_date: date,
        rates: Dict[int, Dict]
    ) -> Optional[float]:
        """
        Calcule la valeur en appliquant les taux déclarés année par année.
        
        Si un taux est manquant, retourne None (pas d'extrapolation).
        S'arrête à la dernière année avec un taux disponible.
        """
        value = invested
        start_year = subscription_date.year
        
        # Trouver la dernière année avec un taux disponible
        if not rates:
            return None
        last_available_year = max(rates.keys())
        
        # Ne capitaliser que jusqu'à la dernière année avec un taux disponible
        # (ou jusqu'à la date de valorisation si elle est avant)
        end_year = min(valuation_date.year, last_available_year)
        
        for year in range(start_year, end_year + 1):
            if year not in rates or rates[year].get('rate') is None:
                # Taux manquant : impossible de calculer
                return None
            
            rate = rates[year]['rate'] / 100.0
            
            if year == start_year:
                # Première année partielle
                days_in_year = 365
                days_invested = (date(year, 12, 31) - subscription_date).days
                partial_rate = rate * (days_invested / days_in_year)
                value *= (1 + partial_rate)
            elif year == end_year:
                # Dernière année : si c'est la dernière année avec taux, capitaliser jusqu'au 31/12
                # Sinon, capitaliser jusqu'à la date de valorisation
                if end_year == last_available_year and end_year < valuation_date.year:
                    # S'arrêter au 31/12 de la dernière année avec taux
                    value *= (1 + rate)  # Année complète jusqu'au 31/12
                else:
                    # Capitaliser jusqu'à la date de valorisation
                    days_in_year = 365
                    days_invested = (valuation_date - date(year, 1, 1)).days
                    partial_rate = rate * (days_invested / days_in_year)
                    value *= (1 + partial_rate)
            else:
                # Année complète
                value *= (1 + rate)
        
        return value

    @staticmethod
    def _extract_cashflows(lots: list) -> List[Tuple[date, str, float]]:
        """
        Convertit des lots en cashflows datés.
        Convention:
        - type=buy, net_amount>0 => contribution
        - type=fee/tax, net_amount<0 => retrait
        - type=income, net_amount>0 => versement (crédit)
        """
        out: List[Tuple[date, str, float]] = []
        for lot in lots or []:
            if not isinstance(lot, dict):
                continue
            if lot.get("date") is None:
                continue
            try:
                d = datetime.fromisoformat(str(lot.get("date"))).date()
            except (ValueError, TypeError):
                continue
            lt = str(lot.get("type") or "buy").lower()
            amt = lot.get("net_amount")
            if amt is None:
                # fallback gross - fees
                gross = lot.get("gross_amount")
                fees = lot.get("fees_amount") or 0.0
                if gross is not None:
                    try:
                        amt = float(gross) - float(fees or 0.0)
                    except (ValueError, TypeError):
                        amt = None
            if amt is None:
                continue
            try:
                amt_f = float(amt)
            except (ValueError, TypeError):
                continue
            # Normalize kind
            kind = "buy" if lt == "buy" else "fee" if lt == "fee" else "tax" if lt == "tax" else "income" if lt == "income" else "other"
            out.append((d, kind, amt_f))
        return sorted(out, key=lambda x: x[0])

    def _compute_value_from_cashflows(
        self,
        *,
        cashflows: List[Tuple[date, str, float]],
        valuation_date: date,
        rates: Dict[int, Dict],
    ) -> Optional[float]:
        """
        Valorise en appliquant les taux déclarés à une série de cashflows.
        Pas d'extrapolation: si un taux manque sur une période requise -> None.
        
        Les cashflows positifs (buy, income) sont capitalisés.
        Les cashflows négatifs (fee, tax, sell, other) sont aussi capitalisés (ils réduisent la valeur).
        """
        total = 0.0
        for d, kind, amt in cashflows:
            if d > valuation_date:
                continue
            
            # Capitaliser le montant (positif ou négatif) depuis sa date jusqu'à la date de valorisation
            v = self._compute_value_from_rates(
                invested=abs(float(amt)),  # Capitaliser la valeur absolue
                subscription_date=d,
                valuation_date=valuation_date,
                rates=rates,
            )
            if v is None:
                return None
            
            # Appliquer le signe du cashflow
            if amt < 0:
                total -= float(v)  # Retrait/frais : soustraire la valeur capitalisée
            else:
                total += float(v)  # Achat/income : ajouter la valeur capitalisée
        return total

