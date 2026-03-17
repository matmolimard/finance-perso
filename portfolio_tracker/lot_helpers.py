"""
Helpers de lots : détection de vente, extraction de dates/valeurs, calculs fonds euro.

Fonctions pures extraites de cli.py.
"""
from datetime import datetime, date
from typing import Optional, List, Dict, Any, Tuple

from .constants import POSITION_SOLD_THRESHOLD, LIQUIDATION_UNITS_THRESHOLD

from .lot_classifier import LotCategory, LotClassifier, ClassifiedLot, parse_lot_date
from .finance import is_external_contribution, calculate_invested_amounts


def is_position_sold(position) -> bool:
    """
    Détermine si une position est vendue (units_held ≈ 0).
    """
    units_held = position.investment.units_held
    if units_held is not None:
        try:
            if abs(float(units_held)) < POSITION_SOLD_THRESHOLD:
                return True
        except (ValueError, TypeError):
            pass
    return False


def extract_sell_date_from_lots(lots: List[Dict[str, Any]]) -> Optional[date]:
    """
    Extrait la date de vente depuis les lots de type 'sell' ou 'tax' (liquidation).
    """
    sell_dates = []
    # Chercher d'abord les lots "sell"
    for lot in lots:
        if not isinstance(lot, dict):
            continue
        lt = str(lot.get("type") or "").lower()
        if lt != "sell":
            continue
        parsed = parse_lot_date(lot.get("date"))
        if parsed:
            sell_dates.append(parsed)

    # Si pas de lot "sell", chercher un lot "tax" qui retire toutes les units (liquidation)
    if not sell_dates:
        for lot in lots:
            if not isinstance(lot, dict):
                continue
            lt = str(lot.get("type") or "").lower()
            if lt != "tax":
                continue
            units = lot.get("units")
            if units is not None:
                try:
                    units_f = float(units)
                    if units_f < LIQUIDATION_UNITS_THRESHOLD:  # Seuil pour détecter une liquidation
                        parsed = parse_lot_date(lot.get("date"))
                        if parsed:
                            sell_dates.append(parsed)
                except (ValueError, TypeError):
                    continue

    # Retourner la date de vente la plus récente
    return max(sell_dates) if sell_dates else None


def extract_sell_value_from_lots(lots: List[Dict[str, Any]]) -> Optional[float]:
    """
    Extrait la valeur de vente depuis les lots.
    """
    for lot in lots:
        if not isinstance(lot, dict):
            continue
        lt = str(lot.get("type") or "").lower()
        if lt == "sell":
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
                    return abs(float(amt))
                except (ValueError, TypeError):
                    continue
        elif lt == "tax":
            # Vérifier si c'est une liquidation (retire beaucoup d'units)
            units = lot.get("units")
            if units is not None:
                try:
                    units_f = float(units)
                    if units_f < LIQUIDATION_UNITS_THRESHOLD:  # Liquidation
                        amt = lot.get("net_amount")
                        if amt is not None:
                            try:
                                return abs(float(amt))
                            except (ValueError, TypeError):
                                continue
                except (ValueError, TypeError):
                    continue
    return None


def calculate_fees_total(lots: List[Dict[str, Any]], lot_classifier: LotClassifier, metadata: Optional[Dict[str, Any]] = None) -> float:
    """
    Calcule les frais totaux depuis les lots ou les métadonnées.
    """
    fees_total = 0.0
    if lots:
        classified_lots = lot_classifier.classify_all_lots(lots, "")
        for cl in classified_lots:
            if cl.category == LotCategory.FEE:
                fees_total += cl.amount

    # Utiliser cashflow_adjustments depuis les métadonnées si disponible (plus fiable)
    if metadata:
        cashflow_adjustments = metadata.get("cashflow_adjustments")
        if cashflow_adjustments is not None:
            fees_total = abs(float(cashflow_adjustments))

    return fees_total


def calculate_fonds_euro_invested_amount(position, lots: List[Dict[str, Any]]) -> float:
    """
    Calcule le capital investi pour un fonds euro en utilisant is_external_contribution.
    """
    invested_amount = position.investment.invested_amount
    if lots:
        buy_total = 0.0
        sell_other_total = 0.0
        fees_total = 0.0
        has_previous_external = False
        for lot in lots:
            if not isinstance(lot, dict):
                continue
            lot_type = str(lot.get('type', 'buy')).lower()
            net_amt = lot.get('net_amount', 0.0)

            if lot_type == 'buy' and net_amt > 0:
                if is_external_contribution(lot, has_previous_external):
                    buy_total += net_amt
                    has_previous_external = True
            elif lot_type in ('sell', 'other', 'tax') and net_amt < 0:
                sell_other_total += abs(net_amt)
            elif lot_type == 'fee' and net_amt < 0:
                fees_total += abs(net_amt)

        invested_real_from_lots = max(0.0, buy_total - sell_other_total - fees_total)
        if invested_real_from_lots > 0:
            invested_amount = invested_real_from_lots

    return float(invested_amount) if invested_amount else 0.0


def get_fonds_euro_reference_date(lots: list, position_id: str, today: date) -> date:
    """
    Détermine la date de référence pour le calcul de performance des fonds euros.

    Règle générique : tant qu'on n'a pas le mouvement de bénéfice pour une année,
    on ne prend pas en compte cette année ni l'année N-1 dans le calcul.
    """
    classifier = LotClassifier()
    classified_lots = classifier.classify_all_lots(lots, position_id)

    # Trouver toutes les années pour lesquelles on a une participation aux bénéfices
    benefit_years = set()
    for classified_lot in classified_lots:
        if classified_lot.category == LotCategory.INTERNAL_CAPITALIZATION:
            benefit_years.add(classified_lot.date.year)

    if not benefit_years:
        if today.month <= 2:
            ref_year = today.year - 2
        else:
            ref_year = today.year - 1
        return date(ref_year, 12, 31)

    last_benefit_year = max(benefit_years)

    if today.month <= 2:
        ref_year = last_benefit_year
    else:
        if last_benefit_year >= today.year - 1:
            ref_year = today.year - 1
        else:
            ref_year = last_benefit_year

    return date(ref_year, 12, 31)


def calculate_fonds_euro_performance_values(
    current_value: float,
    lots: List[Dict[str, Any]],
    position_id: str,
    ref_date_end: date,
    lot_classifier: LotClassifier,
) -> Tuple[Optional[float], Optional[float]]:
    """
    Calcule value_for_perf et invested_for_perf pour les fonds euros.

    Returns:
        Tuple de (value_for_perf, invested_for_perf)
    """
    invested_amounts = calculate_invested_amounts(lots, position_id, lot_classifier, ref_date_end)
    invested_for_perf = invested_amounts['invested_external_until_ref']

    # Calculer la valeur au 31/12/(N-1)
    value_for_perf = None
    if current_value is not None:
        value_for_perf = float(current_value)
        if lots:
            classified_lots = lot_classifier.classify_all_lots(lots, position_id)
            ref_year = ref_date_end.year
            for cl in classified_lots:
                if cl.date.year > ref_year:
                    if cl.category == LotCategory.EXTERNAL_DEPOSIT:
                        value_for_perf -= cl.amount
                    elif cl.category == LotCategory.INTERNAL_CAPITALIZATION:
                        value_for_perf -= cl.amount
                    elif cl.is_cash_outflow():
                        value_for_perf += cl.amount

    return value_for_perf, invested_for_perf if invested_for_perf > 0 else None
