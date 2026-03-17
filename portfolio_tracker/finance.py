"""
Calculs financiers : XIRR, montants investis, métriques de performance.

Fonctions pures extraites de cli.py — source de vérité unique.
"""
from datetime import datetime, date
from typing import Optional

from .lot_classifier import LotCategory, LotClassifier, ClassifiedLot, parse_lot_date
from .constants import BENEFIT_DATE


def calculate_xirr(cashflows: list, guess: float = 0.1, max_iter: int = 100, precision: float = 1e-6) -> Optional[float]:
    """
    Calcule le XIRR (taux de rendement interne annualisé) pour une série de flux de trésorerie.
    Utilise la méthode de Newton-Raphson (pas de dépendance externe).

    Args:
        cashflows: Liste de tuples (date, montant) où les montants négatifs sont des sorties
        guess: Estimation initiale du taux
        max_iter: Nombre maximum d'itérations
        precision: Précision souhaitée

    Returns:
        Le taux annualisé en décimal (ex: 0.05 pour 5%) ou None si pas de convergence
    """
    if not cashflows or len(cashflows) < 2:
        return None

    # Trier par date
    cashflows = sorted(cashflows, key=lambda x: x[0])

    # Date de référence (première date)
    ref_date = cashflows[0][0]

    # Convertir en (jours depuis ref_date, montant)
    cf_data = []
    for dt, amt in cashflows:
        days = (dt - ref_date).days
        cf_data.append((days / 365.25, amt))  # Convertir en années

    # Méthode de Newton-Raphson pour trouver le taux
    rate = guess
    for _ in range(max_iter):
        # Calculer la valeur actuelle nette (NPV) et sa dérivée
        npv = 0.0
        npv_deriv = 0.0

        for t, amt in cf_data:
            npv += amt / ((1 + rate) ** t)
            npv_deriv -= t * amt / ((1 + rate) ** (t + 1))

        # Vérifier la convergence
        if abs(npv) < precision:
            return rate

        # Mise à jour du taux
        if npv_deriv != 0:
            rate = rate - npv / npv_deriv
        else:
            return None

    # Pas de convergence
    return None


def is_external_contribution(lot: dict, has_previous_external: bool = False) -> bool:
    """
    Détermine si un mouvement 'buy' est un versement externe ou une participation aux bénéfices.

    Args:
        lot: Le lot à vérifier
        has_previous_external: True si on a déjà vu des versements externes avant

    Returns:
        True si c'est un versement externe, False si c'est une participation aux bénéfices
    """
    if not isinstance(lot, dict):
        return False

    lot_type = str(lot.get('type', 'buy')).lower()
    if lot_type != 'buy':
        return False

    # Si external est explicitement défini, l'utiliser
    external = lot.get('external')
    if external is not None:
        return bool(external)

    # Heuristique : si c'est le 31/12 et qu'il y a déjà eu des versements externes,
    # c'est probablement une participation aux bénéfices
    lot_date_obj = parse_lot_date(lot.get('date'))
    if lot_date_obj and has_previous_external:
        if (lot_date_obj.month, lot_date_obj.day) == BENEFIT_DATE:
            return False

    # Par défaut, considérer comme versement externe (pour compatibilité)
    return True


def calculate_invested_amounts(lots: list, position_id: str, lot_classifier: LotClassifier, ref_date: Optional[date] = None) -> dict:
    """
    Calcule les montants investis à partir des lots.
    MÉTHODE CENTRALISÉE utilisant LotClassifier (source de vérité unique).

    Args:
        lots: Liste des lots (mouvements)
        position_id: ID de la position (pour la classification)
        lot_classifier: Instance de LotClassifier
        ref_date: Date de référence (optionnelle). Si fournie, calcule aussi invested_until_ref

    Returns:
        Dict avec:
        - invested_total: Capital investi total (achats externes - rachats - frais)
        - invested_external: Capital externe uniquement (versements externes)
        - invested_until_ref: Capital investi jusqu'à ref_date (si ref_date fournie)
        - invested_external_until_ref: Capital externe jusqu'à ref_date (si ref_date fournie)
    """
    result = {
        'invested_total': 0.0,
        'invested_external': 0.0,
        'invested_until_ref': 0.0,
        'invested_external_until_ref': 0.0,
    }

    if not lots:
        return result

    # Classifier tous les lots (source de vérité unique)
    classified_lots = lot_classifier.classify_all_lots(lots, position_id)

    deposits = 0.0
    withdrawals = 0.0
    fees_taxes = 0.0

    deposits_until_ref = 0.0
    withdrawals_until_ref = 0.0
    fees_taxes_until_ref = 0.0

    for cl in classified_lots:
        is_before_ref = (cl.date <= ref_date) if ref_date else True

        if cl.category == LotCategory.EXTERNAL_DEPOSIT:
            deposits += cl.amount
            if is_before_ref:
                deposits_until_ref += cl.amount

        elif cl.category == LotCategory.WITHDRAWAL:
            withdrawals += cl.amount
            if is_before_ref:
                withdrawals_until_ref += cl.amount

        elif cl.category in (LotCategory.FEE, LotCategory.TAX):
            fees_taxes += cl.amount
            if is_before_ref:
                fees_taxes_until_ref += cl.amount

    result['invested_total'] = max(0.0, deposits - withdrawals - fees_taxes)
    result['invested_external'] = deposits
    result['invested_until_ref'] = max(0.0, deposits_until_ref - withdrawals_until_ref - fees_taxes_until_ref)
    result['invested_external_until_ref'] = deposits_until_ref

    return result


def build_cashflows_for_xirr(lots: list, position_id: str, lot_classifier: LotClassifier, value_at_end: float, end_date: date) -> list:
    """
    Construit la liste des flux de trésorerie pour le calcul XIRR.
    MÉTHODE CENTRALISÉE utilisant LotClassifier (source de vérité unique).

    Args:
        lots: Liste des lots (mouvements)
        position_id: ID de la position (pour la classification)
        lot_classifier: Instance de LotClassifier
        value_at_end: Valeur finale (positive)
        end_date: Date de la valeur finale

    Returns:
        Liste de tuples (date, montant) pour XIRR
    """
    cashflows = []

    # Classifier tous les lots (source de vérité unique)
    classified_lots = lot_classifier.classify_all_lots(lots, position_id)

    # Filtrer jusqu'à end_date et construire les flux XIRR
    for cl in classified_lots:
        if cl.date > end_date:
            continue

        xirr_amount = cl.for_xirr()
        if xirr_amount is not None:
            cashflows.append((cl.date, xirr_amount))

    # Ajouter la valeur finale
    if cashflows:
        cashflows.append((end_date, value_at_end))

    return cashflows


def calculate_performance_metrics(
    current_value: float,
    invested_amount: float,
    subscription_date: date,
    position_id: str,
    lot_classifier: LotClassifier,
    end_date: Optional[date] = None,
    lots: Optional[list] = None,
    value_for_perf: Optional[float] = None,
    invested_for_perf: Optional[float] = None,
) -> dict:
    """
    Calcule les métriques de performance (gain, perf%, perf annualisée).
    Utilise XIRR si des lots sont fournis, sinon calcul simple.
    MÉTHODE CENTRALISÉE utilisant LotClassifier (source de vérité unique).

    Args:
        current_value: Valeur actuelle (pour le gain affiché)
        invested_amount: Capital investi total (pour le gain affiché)
        subscription_date: Date de souscription
        position_id: ID de la position (pour la classification)
        lot_classifier: Instance de LotClassifier
        end_date: Date de fin pour le calcul (défaut: aujourd'hui)
        lots: Liste des lots pour calcul XIRR (optionnel)
        value_for_perf: Valeur ajustée pour le calcul de performance (optionnel, pour fonds euros)
        invested_for_perf: Capital investi ajusté pour le calcul de performance (optionnel, pour fonds euros)

    Returns:
        Dict avec:
        - gain: Gain absolu (sur current_value - invested_amount)
        - perf: Performance totale en %
        - perf_annualized: Performance annualisée en %
    """
    result = {
        'gain': 0.0,
        'perf': None,
        'perf_annualized': None,
    }

    if not current_value or not invested_amount or invested_amount <= 0:
        return result

    if end_date is None:
        end_date = datetime.now().date()

    # Calcul du gain (toujours sur les valeurs actuelles pour l'affichage)
    gain = float(current_value) - float(invested_amount)
    result['gain'] = gain

    # Si on a des lots ET des valeurs ajustées (value_for_perf/invested_for_perf),
    # utiliser XIRR (cas des fonds euros avec calcul jusqu'à N-1)
    # Pour les autres cas (produits structurés, UC), on utilisera le calcul simple
    if lots and value_for_perf is not None and invested_for_perf is not None:
        # Utiliser les valeurs ajustées (pour fonds euros avec calcul jusqu'à N-1)
        value_for_xirr = float(value_for_perf)
        invested_for_xirr = float(invested_for_perf)

        # Vérifier que les valeurs pour XIRR sont valides
        if value_for_xirr and invested_for_xirr > 0:
            try:
                cashflows = build_cashflows_for_xirr(lots, position_id, lot_classifier, value_for_xirr, end_date)
                if cashflows:
                    xirr_result = calculate_xirr(cashflows)
                    if xirr_result is not None:
                        # XIRR retourne déjà un taux annualisé en décimal
                        result['perf_annualized'] = xirr_result * 100.0

                        # Calculer la performance totale à partir du taux annualisé
                        days_elapsed = (end_date - subscription_date).days
                        if days_elapsed > 0:
                            years_real = days_elapsed / 365.25
                            if years_real > 0:
                                result['perf'] = ((1.0 + xirr_result) ** years_real - 1.0) * 100.0

                        return result
            except (OverflowError, ValueError, ZeroDivisionError):
                # Si XIRR échoue, fallback sur calcul simple
                pass

    # Sinon, calcul simple
    perf = (gain / float(invested_amount)) * 100.0
    result['perf'] = perf

    # Annualiser
    days_elapsed = (end_date - subscription_date).days
    if days_elapsed > 0:
        years_real = days_elapsed / 365.25
        if years_real > 0:
            result['perf_annualized'] = ((1.0 + perf / 100.0) ** (1.0 / years_real) - 1.0) * 100.0

    return result
