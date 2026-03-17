"""
Event-based Engine - Valorisation par événements (produits structurés)
"""
from datetime import date, datetime
from typing import Optional, List, Tuple, Dict, Any
from pathlib import Path
import yaml

from .base import BaseValuationEngine, ValuationResult, ValuationEvent
from ..core.asset import Asset
from ..core.position import Position


class EventBasedEngine(BaseValuationEngine):
    """
    Moteur de valorisation pour produits structurés.
    
    Gère les événements temporels :
    - Coupons semestriels/annuels
    - Autocalls
    - Échéance
    
    Ne fait PAS de mark-to-market théorique.
    """
    
    def valuate(
        self, 
        asset: Asset, 
        position: Position, 
        valuation_date: Optional[date] = None
    ) -> ValuationResult:
        """
        Valorise un produit structuré en identifiant les événements.
        
        La valorisation se base sur :
        1. Les événements passés enregistrés
        2. Les événements futurs probables selon le calendrier
        3. Les observations du sous-jacent
        """
        val_date = self._get_valuation_date(valuation_date)
        
        # Récupérer les métadonnées du produit structuré
        metadata = asset.metadata
        if not metadata:
            return ValuationResult(
                position_id=position.position_id,
                asset_id=asset.asset_id,
                valuation_date=val_date,
                status="error",
                message="Métadonnées du produit structuré manquantes"
            )
        
        # Charger les événements (réels) et les événements attendus (brochure)
        events, expected_events = self._load_event_file(asset.asset_id)
        
        # Identifier le semestre/période courant(e)
        current_period = self._identify_current_period(
            metadata, 
            position.investment.subscription_date, 
            val_date
        )
        
        # Si units_held=0 (position historique vendue/réinvestie), utiliser la valeur de vente
        # Priorité au units_held du YAML, qui est la source de vérité
        units_held_yaml = position.investment.units_held
        lots = position.investment.lots or []
        
        def _extract_sell_value(lots_list):
            """Extrait la valeur de vente depuis les lots de type 'sell' ou 'tax' (liquidation)."""
            sell_value = None
            # Chercher d'abord un lot "sell" explicite
            for lot in lots_list:
                if not isinstance(lot, dict):
                    continue
                lt = str(lot.get("type") or "").lower()
                if lt != "sell":
                    continue
                amt = lot.get("net_amount")
                if amt is None:
                    gross = lot.get("gross_amount")
                    fees = lot.get("fees_amount") or 0.0
                    if gross is not None:
                        try:
                            amt = float(gross) - float(fees or 0.0)
                        except (ValueError, TypeError):
                            amt = None
                if amt is not None:
                    try:
                        # La valeur de vente est la valeur absolue du montant (négatif car sortie)
                        sell_value = abs(float(amt))
                        break  # Prendre le premier lot de vente trouvé
                    except (ValueError, TypeError):
                        continue
            
            # Si pas de lot "sell", chercher un lot "tax" qui retire toutes les units (liquidation)
            if sell_value is None:
                for lot in lots_list:
                    if not isinstance(lot, dict):
                        continue
                    lt = str(lot.get("type") or "").lower()
                    if lt != "tax":
                        continue
                    # Vérifier si ce lot retire toutes les units (liquidation)
                    units = lot.get("units")
                    if units is not None:
                        try:
                            units_f = float(units)
                            # Si le lot retire une grande quantité d'units (liquidation), utiliser sa valeur
                            if units_f < -10:  # Seuil arbitraire pour détecter une liquidation
                                amt = lot.get("net_amount")
                                if amt is None:
                                    gross = lot.get("gross_amount")
                                    fees = lot.get("fees_amount") or 0.0
                                    if gross is not None:
                                        try:
                                            amt = float(gross) - float(fees or 0.0)
                                        except (ValueError, TypeError):
                                            amt = None
                                if amt is not None:
                                    try:
                                        # La valeur de liquidation est la valeur absolue du montant
                                        sell_value = abs(float(amt))
                                        break
                                    except (ValueError, TypeError):
                                        continue
                        except (ValueError, TypeError):
                            continue
            
            return sell_value
        
        # Vérifier d'abord si units_held du YAML indique que la position est vendue
        if units_held_yaml is not None:
            try:
                units_held_float = float(units_held_yaml)
            except (ValueError, TypeError) as e:
                from ..errors import PortfolioDataError
                raise PortfolioDataError(
                    f"Position {position.position_id} : units_held invalide ({units_held_yaml!r}): {e}"
                )
            
            if abs(units_held_float) < 0.01:
                # units_held=0 = position fermée dans l'état courant.
                # Mais pour une valorisation historique (val_date < date de clôture),
                # la position était peut-être encore ouverte → calculer les units à val_date.
                units_at_val_date = 0.0
                for lot in lots:
                    if not isinstance(lot, dict):
                        continue
                    d = lot.get("date")
                    if d is None:
                        continue
                    try:
                        lot_date = d if hasattr(d, "year") else datetime.fromisoformat(str(d)).date()
                    except Exception:
                        continue
                    if lot_date > val_date:
                        continue  # lot postérieur à la date de valorisation
                    u = lot.get("units")
                    if u is not None:
                        try:
                            units_at_val_date += float(u)
                        except (ValueError, TypeError):
                            pass
                
                if abs(units_at_val_date) > 0.01:
                    # Position encore ouverte à val_date : laisser le moteur calculer normalement.
                    # On continue l'exécution (pas de return ici).
                    pass
                else:
                    # Position fermée à val_date → current_value = 0.
                    buy_amount_total = 0.0
                    for lot in lots:
                        if not isinstance(lot, dict):
                            continue
                        lt = str(lot.get("type") or "buy").lower()
                        if lt != "buy":
                            continue
                        amt = lot.get("net_amount")
                        if amt is None:
                            gross = lot.get("gross_amount")
                            fees = lot.get("fees_amount") or 0.0
                            if gross is not None:
                                try:
                                    amt = float(gross) - float(fees or 0.0)
                                except (ValueError, TypeError):
                                    amt = None
                        if amt is not None:
                            try:
                                if float(amt) > 0:
                                    buy_amount_total += float(amt)
                            except (ValueError, TypeError):
                                continue
                    
                    invested = position.investment.invested_amount
                    if invested is None:
                        invested = 0.0
                    invested_for_valuation = buy_amount_total if buy_amount_total > 0 else invested
                    sell_value = _extract_sell_value(lots)
                    return ValuationResult(
                        position_id=position.position_id,
                        asset_id=asset.asset_id,
                        valuation_date=val_date,
                        current_value=0.0,
                        invested_amount=invested,
                        status="ok",
                        message="Position historique (vendue/réinvestie, units_held=0)" + (
                            f" - Valeur de vente: {sell_value:,.2f} €" if sell_value is not None else ""
                        ),
                        metadata={
                            "invested_for_valuation": invested_for_valuation if buy_amount_total > 0 else None,
                            "buy_amount_total": buy_amount_total if buy_amount_total > 0 else None,
                            "sell_value": sell_value,
                        }
                    )
        
        # Si units_held n'est pas défini dans le YAML, calculer depuis les lots
        if units_held_yaml is None:
            # Calculer les units seulement pour les lots <= val_date (historique)
            lots_units_total = 0.0
            for lot in lots:
                if not isinstance(lot, dict):
                    continue
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
                    if units is not None:
                        lots_units_total += float(units)
                except (ValueError, TypeError):
                    pass
            # Si la somme des units à val_date est 0 ou très proche de 0, la position était vendue
            if abs(lots_units_total) < 0.01:
                # Calculer le capital investi réel depuis les lots (même pour positions vendues)
                buy_amount_total = 0.0
                for lot in lots:
                    if not isinstance(lot, dict):
                        continue
                    lt = str(lot.get("type") or "buy").lower()
                    if lt != "buy":
                        continue
                    amt = lot.get("net_amount")
                    if amt is None:
                        gross = lot.get("gross_amount")
                        fees = lot.get("fees_amount") or 0.0
                        if gross is not None:
                            try:
                                amt = float(gross) - float(fees or 0.0)
                            except (ValueError, TypeError):
                                amt = None
                    if amt is not None:
                        try:
                            if float(amt) > 0:
                                buy_amount_total += float(amt)
                        except (ValueError, TypeError):
                            continue
                
                invested = position.investment.invested_amount
                if invested is None:
                    invested = 0.0
                # Utiliser la somme des lots buy si disponible, sinon invested_amount du YAML
                invested_for_valuation = buy_amount_total if buy_amount_total > 0 else invested
                
                # Position vendue : current_value = 0 (elle n'est plus dans le portefeuille).
                sell_value = _extract_sell_value(lots)
                return ValuationResult(
                    position_id=position.position_id,
                    asset_id=asset.asset_id,
                    valuation_date=val_date,
                    current_value=0.0,
                    invested_amount=invested,
                    status="ok",
                    message="Position historique (vendue/réinvestie, units_held=0 depuis lots)" + (
                        f" - Valeur de vente: {sell_value:,.2f} €" if sell_value is not None else ""
                    ),
                    metadata={
                        "invested_for_valuation": invested_for_valuation if buy_amount_total > 0 else None,
                        "buy_amount_total": buy_amount_total if buy_amount_total > 0 else None,
                        "sell_value": sell_value,
                    }
                )
        
        # Vérifier que la position a été achetée avant val_date.
        # Si aucun lot "buy" n'existe avant val_date, la position n'était pas encore ouverte.
        has_buy_before_val_date = False
        for lot in lots:
            if not isinstance(lot, dict) or str(lot.get("type") or "").lower() != "buy":
                continue
            d = lot.get("date")
            if d is None:
                continue
            try:
                lot_date = d if hasattr(d, "year") else datetime.fromisoformat(str(d)).date()
                if lot_date <= val_date:
                    has_buy_before_val_date = True
                    break
            except Exception:
                pass
        if lots and not has_buy_before_val_date:
            # Position pas encore achetée à val_date → valeur = 0
            return ValuationResult(
                position_id=position.position_id,
                asset_id=asset.asset_id,
                valuation_date=val_date,
                current_value=0.0,
                invested_amount=0.0,
                status="ok",
                message="Position non encore ouverte à cette date",
            )

        # Valorisation = capital investi (buys) + coupons reçus + ajustements (frais/taxes/other cashflows)
        # Utiliser invested_amount du YAML comme source de vérité (capital investi actuel après retraits)
        # Ne calculer à partir des lots que si invested_amount n'est pas défini (None)
        # Note: invested_amount == 0.0 est une valeur valide (position rachetée/réinvestie)
        invested = position.investment.invested_amount
        
        buy_amount_total = 0.0
        cashflow_adjustments = 0.0  # fees/tax/income/other
        lots_has_amounts = False
        for lot in lots:
            if not isinstance(lot, dict):
                continue
            lt = str(lot.get("type") or "buy").lower()
            amt = lot.get("net_amount")
            if amt is None:
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
            lots_has_amounts = True
            if lt == "buy" and amt_f > 0:
                buy_amount_total += amt_f
            elif lt != "buy":
                # fee/tax/income/other/sell -> impact direct sur la valeur
                cashflow_adjustments += amt_f

        # Ne calculer depuis les lots que si invested_amount n'est pas défini dans le YAML
        if invested is None and lots_has_amounts and buy_amount_total:
            invested = buy_amount_total
        
        # Fallback si toujours None
        if invested is None:
            invested = 0.0
        
        # Pour la valorisation, utiliser le capital investi réel (somme des lots buy)
        # même si invested_amount=0 dans le YAML (réinvestissement)
        # Le capital investi pour le P&L reste invested_amount du YAML (apports externes uniquement)
        invested_for_valuation = invested
        if lots_has_amounts and buy_amount_total > 0:
            # Utiliser la somme des lots buy pour la valorisation
            invested_for_valuation = buy_amount_total
        
        coupons_recorded = sum(
            e.amount for e in events 
            if e.event_type == "coupon" and e.event_date <= val_date and e.amount
        )

        coupons_estimated = 0.0

        # Cas CMS: coupons conditionnels. Par défaut on ne peut pas les déduire.
        # Mais si l'utilisateur confirme que les coupons passés ont été versés,
        # on crédite les coupon_expected passés (sans présumer du futur).
        if self._is_cms_product(metadata) and metadata.get("cms_past_coupons_confirmed_paid", False):
            coupons_estimated = self._estimate_cms_paid_coupons_from_expected(
                expected_events=expected_events,
                invested_amount=invested_for_valuation,
                realized_events=events,
                valuation_date=val_date,
            )

        coupons_received = coupons_recorded + coupons_estimated
        
        # Vérifier si autocall s'est produit
        autocalled = any(
            e.event_type == "autocall" and e.event_date <= val_date 
            for e in events
        )
        
        # Calculer les coupons théoriques comme si le produit avait "strike" aujourd'hui
        # Cela intègre tous les coupons des périodes complètes écoulées, même s'ils n'ont pas encore été payés
        theoretical_coupons_total = 0.0
        if not autocalled:
            theoretical_coupons_total = self._calculate_theoretical_coupons_if_strike(
                metadata=metadata,
                expected_events=expected_events,
                invested_amount=invested_for_valuation,
                subscription_date=position.investment.subscription_date,
                valuation_date=val_date
            )
        
        if autocalled:
            # Produit terminé par autocall
            current_value = 0.0  # Remboursé
            status = "ok"
            message = "Produit remboursé par autocall"
        else:
            # Produit en cours
            # Utiliser invested_for_valuation (capital investi réel) pour la valorisation
            # + coupons théoriques totaux (comme si strike aujourd'hui) + ajustements
            # Note: theoretical_coupons_total inclut tous les coupons des périodes complètes écoulées
            # comme si le produit avait "strike" aujourd'hui
            current_value = invested_for_valuation + theoretical_coupons_total + cashflow_adjustments
            status = "ok"
            message = f"Période {current_period}"

        # Exploitation des expected_events (sans impact sur la valorisation)
        next_expected = self._next_expected_payment(expected_events, val_date)
        overdue_expected = self._overdue_expected_payments(
            expected_events=expected_events,
            realized_events=events,
            valuation_date=val_date,
            grace_days=7
        )
        
        return ValuationResult(
            position_id=position.position_id,
            asset_id=asset.asset_id,
            valuation_date=val_date,
            current_value=current_value,
            # Utiliser invested_amount du YAML pour le P&L (apports externes uniquement)
            invested_amount=invested,
            # On expose tout (réel + attendu) dans result.events, en taggant les attendus
            events=events + expected_events,
            status=status,
            message=message,
            metadata={
                "current_period": current_period,
                "coupons_received": coupons_received,
                "coupons_recorded": coupons_recorded,
                "coupons_estimated": coupons_estimated,
                "theoretical_coupons_if_strike": theoretical_coupons_total if not autocalled else 0.0,
                "autocalled": autocalled,
                "buy_amount_total": buy_amount_total if lots_has_amounts else None,
                "invested_for_valuation": invested_for_valuation if lots_has_amounts else None,
                "cashflow_adjustments": cashflow_adjustments if lots_has_amounts else None,
                "expected_events_count": len(expected_events),
                "next_expected_event": next_expected,
                "expected_overdue_count": len(overdue_expected),
                "expected_overdue_events": overdue_expected[:10],
            }
        )

    def _is_cms_product(self, asset_metadata: dict) -> bool:
        u = (asset_metadata or {}).get("underlying") or (asset_metadata or {}).get("underlying_id") or ""
        u = str(u).upper()
        return "CMS" in u

    def _estimate_cms_paid_coupons_from_expected(
        self,
        *,
        expected_events: List[ValuationEvent],
        invested_amount: float,
        realized_events: List[ValuationEvent],
        valuation_date: date
    ) -> float:
        """
        Pour les produits CMS où l'utilisateur confirme que les coupons passés ont été payés,
        on additionne les coupon_expected dont la date de paiement est passée et non déjà enregistrés.

        Convention: dans les YAML, coupon_expected.amount peut être un taux (ex 0.025) ou un montant.
        - si amount <= 1.0 => traité comme taux du capital investi
        - sinon => traité comme montant (EUR)
        """
        realized_coupon_dates = [e.event_date for e in realized_events if (e.event_type or "").lower() == "coupon"]
        total = 0.0
        for e in expected_events:
            if (e.event_type or "").lower() != "coupon_expected":
                continue
            if e.event_date > valuation_date:
                continue
            if e.amount is None:
                continue
            matched = any(abs((d - e.event_date).days) <= 7 for d in realized_coupon_dates)
            if matched:
                continue
            try:
                a = float(e.amount)
                if a <= 1.0:
                    total += float(invested_amount) * a
                else:
                    total += a
            except (ValueError, TypeError):
                continue
        return total

    def _derive_periodic_coupon_schedule(
        self,
        *,
        asset_metadata: dict,
        expected_events: List[ValuationEvent],
        invested_amount: float
    ) -> List[ValuationEvent]:
        """
        Pour les produits à coupon fixe (souvent représentés par gain_per_semester),
        on génère des coupon_expected aux dates de paiement (payment_date / payment_date_estimated...).

        On n'applique PAS cette règle aux produits CMS (coupons conditionnels).
        """
        # Opt-in explicite: éviter de modéliser un gain de remboursement comme un coupon périodique.
        if not (asset_metadata or {}).get("coupon_paid_periodically", False):
            return []
        if self._is_cms_product(asset_metadata):
            return []

        out: List[ValuationEvent] = []
        for e in expected_events:
            md = e.metadata or {}
            if not md.get("expected", False):
                continue

            gain = md.get("gain_per_semester")
            if gain is None:
                continue

            # Date de paiement si fournie, sinon on ne peut pas la matérialiser.
            pay = (
                md.get("payment_date")
                or md.get("payment_date_estimated_weekends_only")
                or md.get("estimated_payment_date_weekends_only")
            )
            if not pay:
                continue
            try:
                pay_date = datetime.fromisoformat(str(pay)).date()
            except (ValueError, TypeError):
                continue

            try:
                amt = float(invested_amount) * float(gain)
            except (ValueError, TypeError):
                continue

            out.append(
                ValuationEvent(
                    event_type="coupon_expected",
                    event_date=pay_date,
                    amount=amt,
                    description=f"Coupon fixe estimé (gain_per_semester={gain})",
                    metadata={
                        **md,
                        "expected": True,
                        "derived_from": (e.event_type or ""),
                        "semester": md.get("semester"),
                    },
                )
            )

        # dédoublonnage (date)
        by_key: Dict[str, ValuationEvent] = {}
        for ce in out:
            key = f"{ce.event_date.isoformat()}::{ce.amount}"
            by_key[key] = ce
        return sorted(by_key.values(), key=lambda x: x.event_date)

    def _estimate_paid_coupons_from_expected(
        self,
        *,
        expected_coupon_events: List[ValuationEvent],
        realized_events: List[ValuationEvent],
        valuation_date: date
    ) -> float:
        """
        Estime les coupons déjà payés (non saisis) en se basant sur coupon_expected.

        Heuristique: si aucun coupon réel n'est enregistré à +/- 7 jours autour de la date attendue,
        on considère le coupon comme payé (cas coupon fixe).
        """
        realized_coupon_dates = [e.event_date for e in realized_events if (e.event_type or "").lower() == "coupon"]

        total = 0.0
        for e in expected_coupon_events:
            if e.event_date > valuation_date:
                continue
            if not e.amount:
                continue
            matched = any(abs((d - e.event_date).days) <= 7 for d in realized_coupon_dates)
            if matched:
                continue
            total += float(e.amount)
        return total
    
    def _load_events(self, asset_id: str) -> List[ValuationEvent]:
        """Charge uniquement les événements réellement enregistrés pour un produit (compat)"""
        events, _ = self._load_event_file(asset_id)
        return events

    def _load_event_file(self, asset_id: str) -> Tuple[List[ValuationEvent], List[ValuationEvent]]:
        """
        Charge le fichier d'événements d'un produit structuré.

        - events: événements réellement constatés (impactent la valo)
        - expected_events: calendrier/conditions attendues (issu des brochures)
        """
        events_file = self.market_data_dir / f"events_{asset_id}.yaml"
        if not events_file.exists():
            return [], []
        
        with open(events_file, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
        
        if not data:
            return [], []
        
        events = self._parse_events_list(data.get('events', []), expected=False)
        expected_events = self._parse_events_list(data.get('expected_events', []), expected=True)
        return events, expected_events

    def _parse_events_list(self, raw_events: list, expected: bool) -> List[ValuationEvent]:
        events: List[ValuationEvent] = []
        if not raw_events:
            return events

        for event_data in raw_events:
            if not isinstance(event_data, dict):
                continue
            if 'type' not in event_data or 'date' not in event_data:
                continue

            md = dict(event_data.get('metadata') or {})
            if expected:
                md.setdefault('expected', True)

            try:
                d = datetime.fromisoformat(str(event_data['date'])).date()
            except (ValueError, TypeError):
                continue

            events.append(
                ValuationEvent(
                    event_type=str(event_data['type']),
                    event_date=d,
                    amount=event_data.get('amount'),
                    description=event_data.get('description', ''),
                    metadata=md,
                )
            )
        return events

    def _is_expected_payment_type(self, event_type: str) -> bool:
        et = (event_type or "").lower()
        return (
            et.endswith("_expected")
            or et.endswith("_payment_expected")
            or et in {"maturity_expected", "maturity_payment_expected"}
        )

    def _next_expected_payment(self, expected_events: List[ValuationEvent], valuation_date: date) -> Optional[Dict[str, Any]]:
        upcoming = [
            e for e in expected_events
            if self._is_expected_payment_type(e.event_type) and e.event_date >= valuation_date
        ]
        if not upcoming:
            return None
        e = sorted(upcoming, key=lambda x: x.event_date)[0]
        return {
            "type": e.event_type,
            "date": e.event_date.isoformat(),
            "description": e.description,
            "amount": e.amount,
        }

    def _expected_to_real_type(self, expected_type: str) -> Optional[str]:
        mapping = {
            "coupon_expected": "coupon",
            "autocall_payment_expected": "autocall",
            "maturity_payment_expected": "maturity",
            "maturity_expected": "maturity",
        }
        return mapping.get((expected_type or "").lower())

    def _overdue_expected_payments(
        self,
        expected_events: List[ValuationEvent],
        realized_events: List[ValuationEvent],
        valuation_date: date,
        grace_days: int = 7
    ) -> List[Dict[str, Any]]:
        """
        Retourne la liste des paiements attendus en retard:
        - type attendu = coupon_expected / *_payment_expected / maturity_expected
        - date attendue < (valuation_date - grace_days)
        - aucun événement réel correspondant trouvé autour de la date attendue
        """
        cutoff = valuation_date
        if grace_days and grace_days > 0:
            from datetime import timedelta
            cutoff = valuation_date - timedelta(days=grace_days)

        realized_by_type: Dict[str, List[date]] = {}
        for e in realized_events:
            realized_by_type.setdefault((e.event_type or "").lower(), []).append(e.event_date)

        overdue = []
        for e in expected_events:
            if not self._is_expected_payment_type(e.event_type):
                continue
            if e.event_date >= cutoff:
                continue

            real_type = self._expected_to_real_type(e.event_type)
            if not real_type:
                continue

            dates = realized_by_type.get(real_type, [])
            # tolérance: +/- 7 jours autour de la date attendue
            matched = any(abs((d - e.event_date).days) <= 7 for d in dates)
            if matched:
                continue

            overdue.append(
                {
                    "type": e.event_type,
                    "date": e.event_date.isoformat(),
                    "description": e.description,
                    "amount": e.amount,
                }
            )

        overdue.sort(key=lambda x: x["date"])
        return overdue
    
    def _identify_current_period(
        self, 
        metadata: dict, 
        subscription_date: date, 
        valuation_date: date
    ) -> int:
        """Identifie la période courante du produit structuré"""
        period_months = metadata.get('period_months', 12)
        
        # Calculer le nombre de mois écoulés
        months_elapsed = (
            (valuation_date.year - subscription_date.year) * 12 +
            (valuation_date.month - subscription_date.month)
        )
        
        # Période courante (commence à 1)
        period = (months_elapsed // period_months) + 1
        return max(1, period)
    
    def _calculate_theoretical_coupons_if_strike(
        self,
        *,
        metadata: dict,
        expected_events: List[ValuationEvent],
        invested_amount: float,
        subscription_date: date,
        valuation_date: date
    ) -> float:
        """
        Calcule les coupons théoriques totaux comme si le produit avait "strike" aujourd'hui.
        
        Pour chaque période complète écoulée depuis la souscription, on calcule
        le coupon qui aurait été payé si le produit avait été remboursé aujourd'hui.
        
        Ne s'applique pas aux produits CMS (coupons conditionnels) sauf si confirmés.
        """
        if invested_amount <= 0:
            return 0.0
        
        # Ne pas appliquer aux produits CMS sauf si confirmés
        if self._is_cms_product(metadata) and not metadata.get("cms_past_coupons_confirmed_paid", False):
            return 0.0
        
        # Trouver le gain par période (gain_per_semester ou coupon_rate)
        gain_per_period = None
        
        # 1) Chercher dans les expected_events
        for e in expected_events:
            md = e.metadata or {}
            if not md.get("expected", False):
                continue
            gps = md.get("gain_per_semester")
            if gps is not None:
                try:
                    gain_per_period = float(gps)
                    break
                except (ValueError, TypeError):
                    continue
            cr = md.get("coupon_rate")
            if cr is not None:
                try:
                    cr_f = float(cr)
                    if cr_f <= 1.0:
                        gain_per_period = cr_f
                    else:
                        gain_per_period = cr_f / 100.0
                    break
                except (ValueError, TypeError):
                    continue
        
        # 2) Chercher dans les métadonnées de l'asset
        if gain_per_period is None:
            gps_meta = metadata.get("gain_per_semester")
            if gps_meta is not None:
                try:
                    gain_per_period = float(gps_meta)
                except (ValueError, TypeError):
                    pass
        
        if gain_per_period is None:
            cr_meta = metadata.get("coupon_rate")
            if cr_meta is not None:
                try:
                    cr_f = float(cr_meta)
                    if cr_f <= 1.0:
                        gain_per_period = cr_f
                    else:
                        gain_per_period = cr_f / 100.0
                except (ValueError, TypeError):
                    pass
        
        if gain_per_period is None:
            return 0.0
        
        # Calculer le nombre de périodes complètes écoulées
        period_months = metadata.get('period_months', 6)
        months_elapsed = (
            (valuation_date.year - subscription_date.year) * 12 +
            (valuation_date.month - subscription_date.month)
        )
        # Nombre de périodes complètes (sans la période en cours)
        periods_completed = max(0, months_elapsed // period_months)
        
        # Calculer le montant total des coupons théoriques pour toutes les périodes complètes
        # C'est comme si le produit avait "strike" aujourd'hui et que tous les coupons étaient payés
        theoretical_total = invested_amount * gain_per_period * periods_completed
        
        return theoretical_total


