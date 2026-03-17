"""
Tests pour LotClassifier et LotCategory.

Couvre :
- Classification de chaque type de lot
- Distinction versement externe vs capitalisation interne
- Heuristique 31/12 pour les participations aux bénéfices
- Gestion de l'état mutable (_external_deposits_seen)
- Lots invalides / sans date / sans montant
- Ordre chronologique
"""
import pytest
from datetime import date

from portfolio_tracker.cli import LotClassifier, LotCategory, ClassifiedLot


def make_lot(
    lot_date: str,
    lot_type: str = "buy",
    net_amount: float = 1000.0,
    units: float = 10.0,
    external: bool = None,
) -> dict:
    lot = {
        "date": lot_date,
        "type": lot_type,
        "net_amount": net_amount,
        "units": units,
    }
    if external is not None:
        lot["external"] = external
    return lot


class TestClassifyLotBasicTypes:
    """Classification des types de base."""

    def setup_method(self):
        self.clf = LotClassifier()

    def test_buy_positive_is_external_deposit_by_default(self):
        lot = make_lot("2022-11-09", "buy", 10000.0, 100.0)
        result = self.clf.classify_lot(lot, "pos_001")
        assert result.category == LotCategory.EXTERNAL_DEPOSIT

    def test_fee_negative_is_fee(self):
        lot = make_lot("2025-12-31", "fee", -168.63, -0.09471)
        result = self.clf.classify_lot(lot, "pos_001")
        assert result.category == LotCategory.FEE

    def test_tax_negative_is_tax(self):
        lot = make_lot("2025-12-31", "tax", -334.05, -334.05)
        result = self.clf.classify_lot(lot, "pos_001")
        assert result.category == LotCategory.TAX

    def test_sell_negative_is_withdrawal(self):
        lot = make_lot("2026-02-16", "sell", -27233.9, -23.4775)
        result = self.clf.classify_lot(lot, "pos_001")
        assert result.category == LotCategory.WITHDRAWAL

    def test_other_negative_is_withdrawal(self):
        lot = make_lot("2025-12-12", "other", -173962.34, -174330.93)
        result = self.clf.classify_lot(lot, "pos_001")
        assert result.category == LotCategory.WITHDRAWAL

    def test_amount_always_positive(self):
        """Le champ 'amount' du ClassifiedLot est toujours positif."""
        lot = make_lot("2025-12-31", "fee", -168.63, -0.09471)
        result = self.clf.classify_lot(lot, "pos_001")
        assert result.amount > 0
        assert result.amount == pytest.approx(168.63)


class TestExternalVsInternal:
    """Distinction versement externe vs capitalisation interne."""

    def setup_method(self):
        self.clf = LotClassifier()

    def test_explicit_external_true(self):
        lot = make_lot("2022-11-09", "buy", 360000.0, 360000.0, external=True)
        result = self.clf.classify_lot(lot, "pos_001")
        assert result.category == LotCategory.EXTERNAL_DEPOSIT

    def test_explicit_external_false(self):
        lot = make_lot("2024-12-31", "buy", 14671.02, 14671.02, external=False)
        result = self.clf.classify_lot(lot, "pos_001")
        assert result.category == LotCategory.INTERNAL_CAPITALIZATION

    def test_31_dec_after_external_is_capitalization(self):
        """Un lot buy au 31/12 sur une position ayant déjà un versement externe
        doit être classifié comme participation aux bénéfices (capitalisation interne)."""
        clf = LotClassifier()
        # Premier lot : versement externe
        external = make_lot("2022-11-09", "buy", 360000.0, 360000.0, external=True)
        clf.classify_lot(external, "pos_fe")
        # Deuxième lot : participation aux bénéfices au 31/12
        participation = make_lot("2024-12-31", "buy", 14671.02, 14671.02)
        result = clf.classify_lot(participation, "pos_fe")
        assert result.category == LotCategory.INTERNAL_CAPITALIZATION

    def test_31_dec_without_prior_external_is_external(self):
        """Un lot buy au 31/12 sans versement externe préalable → externe par défaut."""
        clf = LotClassifier()
        lot = make_lot("2024-12-31", "buy", 14671.02, 14671.02)
        result = clf.classify_lot(lot, "pos_new")
        assert result.category == LotCategory.EXTERNAL_DEPOSIT

    def test_non_31dec_buy_after_external_is_external(self):
        """Un versement le 04/09 après un versement initial reste externe."""
        clf = LotClassifier()
        initial = make_lot("2022-11-09", "buy", 360000.0, 360000.0, external=True)
        clf.classify_lot(initial, "pos_001")
        second = make_lot("2025-09-04", "buy", 17500.0, 17500.0)
        result = clf.classify_lot(second, "pos_001")
        assert result.category == LotCategory.EXTERNAL_DEPOSIT

    def test_different_positions_independent_state(self):
        """L'état des versements externes est isolé par position."""
        clf = LotClassifier()
        # pos_001 : a un versement externe
        external = make_lot("2022-11-09", "buy", 360000.0, 360000.0, external=True)
        clf.classify_lot(external, "pos_001")
        # pos_002 : pas de versement externe préalable
        participation_other_pos = make_lot("2024-12-31", "buy", 5000.0, 5000.0)
        result = clf.classify_lot(participation_other_pos, "pos_002")
        assert result.category == LotCategory.EXTERNAL_DEPOSIT


class TestClassifyAllLots:
    """Tests de classify_all_lots (traitement complet d'une position)."""

    def test_sorted_by_date(self):
        """Les lots doivent être triés chronologiquement en sortie."""
        clf = LotClassifier()
        lots = [
            make_lot("2025-12-31", "fee", -168.63, -0.09471),
            make_lot("2022-11-09", "buy", 360000.0, 360000.0, external=True),
            make_lot("2024-12-31", "buy", 14671.02, 14671.02),
        ]
        classified = clf.classify_all_lots(lots, "pos_001")
        dates = [cl.date for cl in classified]
        assert dates == sorted(dates)

    def test_external_before_31dec_participation(self):
        """L'ordre chronologique garantit que le versement externe est vu avant la participation."""
        clf = LotClassifier()
        lots = [
            make_lot("2024-12-31", "buy", 14671.02, 14671.02),   # participation
            make_lot("2022-11-09", "buy", 360000.0, 360000.0, external=True),  # externe
        ]
        classified = clf.classify_all_lots(lots, "pos_001")
        categories = {cl.date: cl.category for cl in classified}
        assert categories[date(2022, 11, 9)] == LotCategory.EXTERNAL_DEPOSIT
        assert categories[date(2024, 12, 31)] == LotCategory.INTERNAL_CAPITALIZATION

    def test_invalid_lot_skipped(self):
        """Un lot sans date est ignoré sans planter."""
        clf = LotClassifier()
        lots = [
            {"type": "buy", "net_amount": 1000.0},   # sans date
            make_lot("2022-11-09", "buy", 360000.0, 360000.0, external=True),
        ]
        classified = clf.classify_all_lots(lots, "pos_001")
        assert len(classified) == 1

    def test_non_dict_lot_skipped(self):
        """Un lot non-dict est ignoré sans planter."""
        clf = LotClassifier()
        lots = [
            "not a dict",
            make_lot("2022-11-09", "buy", 360000.0, 360000.0, external=True),
        ]
        classified = clf.classify_all_lots(lots, "pos_001")
        assert len(classified) == 1

    def test_empty_lots(self):
        clf = LotClassifier()
        assert clf.classify_all_lots([], "pos_001") == []


class TestClassifiedLotHelpers:
    """Tests des méthodes utilitaires de ClassifiedLot."""

    def test_is_cash_inflow_external_deposit(self):
        cl = ClassifiedLot(LotCategory.EXTERNAL_DEPOSIT, date(2022, 11, 9), 360000.0, {})
        assert cl.is_cash_inflow() is True
        assert cl.is_cash_outflow() is False
        assert cl.is_performance() is False

    def test_is_cash_outflow_fee(self):
        cl = ClassifiedLot(LotCategory.FEE, date(2025, 12, 31), 168.63, {})
        assert cl.is_cash_outflow() is True
        assert cl.is_cash_inflow() is False

    def test_is_cash_outflow_tax(self):
        cl = ClassifiedLot(LotCategory.TAX, date(2025, 12, 31), 334.05, {})
        assert cl.is_cash_outflow() is True

    def test_is_cash_outflow_withdrawal(self):
        cl = ClassifiedLot(LotCategory.WITHDRAWAL, date(2026, 2, 16), 27233.9, {})
        assert cl.is_cash_outflow() is True

    def test_is_performance_internal_cap(self):
        cl = ClassifiedLot(LotCategory.INTERNAL_CAPITALIZATION, date(2024, 12, 31), 14671.02, {})
        assert cl.is_performance() is True

    def test_for_xirr_external_deposit(self):
        cl = ClassifiedLot(LotCategory.EXTERNAL_DEPOSIT, date(2022, 11, 9), 360000.0, {})
        assert cl.for_xirr() == pytest.approx(-360000.0)

    def test_for_xirr_withdrawal(self):
        cl = ClassifiedLot(LotCategory.WITHDRAWAL, date(2026, 2, 16), 27233.9, {})
        assert cl.for_xirr() == pytest.approx(27233.9)

    def test_for_xirr_fee_returns_none(self):
        """Les frais ne sont pas des flux XIRR (déjà dans la valeur finale)."""
        cl = ClassifiedLot(LotCategory.FEE, date(2025, 12, 31), 168.63, {})
        assert cl.for_xirr() is None

    def test_for_xirr_tax_returns_none(self):
        cl = ClassifiedLot(LotCategory.TAX, date(2025, 12, 31), 334.05, {})
        assert cl.for_xirr() is None

    def test_for_xirr_internal_cap_returns_none(self):
        cl = ClassifiedLot(LotCategory.INTERNAL_CAPITALIZATION, date(2024, 12, 31), 14671.02, {})
        assert cl.for_xirr() is None
