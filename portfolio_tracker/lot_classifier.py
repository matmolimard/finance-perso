"""
Classification centralisée des mouvements (lots).

Ce module contient LA SEULE SOURCE DE VÉRITÉ pour identifier les mouvements.
Toutes les autres parties du code DOIVENT utiliser ces classes/méthodes.
"""
import logging
from datetime import datetime, date
from typing import Optional
from enum import Enum
from dataclasses import dataclass

from .constants import BENEFIT_DATE

logger = logging.getLogger(__name__)


def parse_lot_date(raw) -> Optional[date]:
    """Parse une date de lot (str ISO ou date) en date. Retourne None si invalide."""
    if raw is None:
        return None
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    if hasattr(raw, 'date') and callable(raw.date):
        return raw.date()
    try:
        return datetime.fromisoformat(str(raw)).date()
    except (ValueError, TypeError):
        return None


class LotCategory(Enum):
    """Catégories de mouvements (source de vérité unique)"""
    EXTERNAL_DEPOSIT = "external_deposit"      # Versement externe (de l'argent frais)
    INTERNAL_CAPITALIZATION = "internal_capitalization"  # Capitalisation interne (intérêts, dividendes)
    WITHDRAWAL = "withdrawal"                  # Retrait / rachat
    FEE = "fee"                               # Frais
    TAX = "tax"                               # Taxe / prélèvement
    OTHER = "other"                           # Autre mouvement


@dataclass
class ClassifiedLot:
    """Résultat de la classification d'un lot (source de vérité unique)"""
    category: LotCategory
    date: date
    amount: float  # Toujours > 0, le signe est dans la category
    raw_lot: dict

    def is_cash_inflow(self) -> bool:
        """Retourne True si c'est un apport d'argent externe"""
        return self.category == LotCategory.EXTERNAL_DEPOSIT

    def is_cash_outflow(self) -> bool:
        """Retourne True si c'est une sortie d'argent (retrait, frais, taxes)"""
        return self.category in (LotCategory.WITHDRAWAL, LotCategory.FEE, LotCategory.TAX)

    def is_performance(self) -> bool:
        """Retourne True si c'est de la performance (capitalisation interne)"""
        return self.category == LotCategory.INTERNAL_CAPITALIZATION

    def for_xirr(self) -> Optional[float]:
        """
        Retourne le montant pour XIRR, ou None si ne doit pas être inclus.
        Convention: négatif = sortie d'argent, positif = rentrée d'argent

        Note: Les frais/taxes/capitalisations ne sont PAS des flux XIRR car ils sont
        déjà inclus dans la current_value (via cashflow_adjustments pour les frais/taxes,
        et directement dans la valeur pour les capitalisations internes).
        """
        if self.category == LotCategory.EXTERNAL_DEPOSIT:
            return -self.amount  # Sortie d'argent (investissement)
        elif self.category == LotCategory.WITHDRAWAL:
            return self.amount   # Rentrée d'argent (rachat)
        # Les frais/taxes/capitalisations ne sont PAS des flux XIRR
        # (déjà inclus dans la valeur finale)
        return None


class LotClassifier:
    """
    Classificateur centralisé de lots.
    TOUTES les fonctions doivent utiliser cette classe pour identifier les mouvements.
    """

    def __init__(self):
        self._external_deposits_seen = set()  # Track des versements externes déjà vus par position

    def classify_lot(self, lot: dict, position_id: str) -> Optional[ClassifiedLot]:
        """
        Classifie un lot selon sa nature.
        C'est LA SEULE MÉTHODE qui doit être utilisée pour identifier les mouvements.

        Args:
            lot: Le lot à classifier
            position_id: L'identifiant de la position (pour le tracking des versements externes)

        Returns:
            ClassifiedLot ou None si le lot est invalide
        """
        if not isinstance(lot, dict):
            return None

        # Extraire les informations de base
        lot_type = str(lot.get('type', 'buy')).lower()
        net_amount = lot.get('net_amount', 0.0)
        lot_date = parse_lot_date(lot.get('date'))
        if lot_date is None:
            return None

        # Classification selon le type
        if lot_type == 'buy' and net_amount > 0:
            # Est-ce un versement externe ou une capitalisation interne ?
            is_external = self._is_external_deposit(lot, position_id)
            category = LotCategory.EXTERNAL_DEPOSIT if is_external else LotCategory.INTERNAL_CAPITALIZATION
            return ClassifiedLot(category, lot_date, abs(net_amount), lot)

        elif lot_type in ('sell', 'other') and net_amount < 0:
            # Retrait / rachat
            return ClassifiedLot(LotCategory.WITHDRAWAL, lot_date, abs(net_amount), lot)

        elif lot_type == 'fee' and net_amount < 0:
            # Frais
            return ClassifiedLot(LotCategory.FEE, lot_date, abs(net_amount), lot)

        elif lot_type == 'tax' and net_amount < 0:
            # Taxe
            return ClassifiedLot(LotCategory.TAX, lot_date, abs(net_amount), lot)

        else:
            # Autre (montant positif non-buy, ou type inconnu)
            return ClassifiedLot(LotCategory.OTHER, lot_date, abs(net_amount), lot)

    def _is_external_deposit(self, lot: dict, position_id: str) -> bool:
        """
        Détermine si un lot 'buy' est un versement externe.
        Logique centralisée (source de vérité unique).
        """
        # Si external est explicitement défini, l'utiliser
        external = lot.get('external')
        if external is not None:
            if external:
                self._external_deposits_seen.add(position_id)
            return bool(external)

        # Heuristique : si c'est le 31/12 et qu'on a déjà vu des versements externes,
        # c'est probablement une participation aux bénéfices
        lot_date_obj = parse_lot_date(lot.get('date'))
        if lot_date_obj and position_id in self._external_deposits_seen:
            if (lot_date_obj.month, lot_date_obj.day) == BENEFIT_DATE:
                return False  # C'est une participation aux bénéfices

        # Par défaut, considérer comme versement externe et le marquer
        self._external_deposits_seen.add(position_id)
        return True

    def classify_all_lots(self, lots: list, position_id: str) -> list[ClassifiedLot]:
        """
        Classifie tous les lots d'une position.
        Retourne une liste triée par date.
        """
        # Trier les lots par date AVANT classification pour que la logique de détection
        # des bénéfices (qui dépend de l'ordre de traitement) fonctionne correctement
        def get_lot_date(lot):
            if not isinstance(lot, dict):
                return date.min
            return parse_lot_date(lot.get('date')) or date.min

        sorted_lots = sorted(lots, key=get_lot_date)

        classified = []
        for lot in sorted_lots:
            classified_lot = self.classify_lot(lot, position_id)
            if classified_lot:
                classified.append(classified_lot)

        # Trier par date (déjà trié, mais on le fait pour être sûr)
        classified.sort(key=lambda cl: cl.date)

        return classified
