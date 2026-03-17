"""
Tests pour le pipeline d'import des mouvements.

Couvre :
- classify_movement (tous les cas réels dont les pièges)
- parse_himalia_text (format détaillé et compact)
- Déduplication des lots
- Protection de units_held pour les fonds euros
"""
import pytest
import tempfile
import yaml
from datetime import date
from pathlib import Path

from portfolio_tracker.importers.himalia_movements import (
    classify_movement,
    parse_himalia_text,
    movement_summary,
    MovementItem,
)


# ---------------------------------------------------------------------------
# classify_movement
# ---------------------------------------------------------------------------

class TestClassifyMovement:
    """Tests de classification des libellés de mouvements."""

    # --- fee ---
    def test_frais_de_gestion(self):
        assert classify_movement("Frais de gestion") == "fee"

    def test_frais_de_gestion_avec_date(self):
        assert classify_movement("Frais de gestion - 31/12/2025") == "fee"

    # --- buy ---
    def test_versement_initial(self):
        assert classify_movement("Versement initial") == "buy"

    def test_versement_libre(self):
        assert classify_movement("Versement libre complémentaire") == "buy"

    def test_arbitrage_volontaire(self):
        assert classify_movement("Arbitrage volontaire") == "buy"

    def test_arbitrage_automatique(self):
        assert classify_movement("Arbitrage automatique") == "buy"

    def test_ost_sans_impact_fiscal(self):
        """Bug passé : 'fiscal' dans le libellé ne doit pas classifier en 'tax'."""
        assert classify_movement("Ost sans impact fiscal") == "buy"

    def test_ost_majuscule(self):
        assert classify_movement("OST SANS IMPACT FISCAL") == "buy"

    # --- income ---
    def test_participation_aux_benefices(self):
        assert classify_movement("Participation aux bénéfices") == "income"

    def test_participation_aux_benefices_date(self):
        assert classify_movement("Participation aux bénéfices - 31/12/2025") == "income"

    def test_distribution_de_revenus(self):
        assert classify_movement("Distribution de revenus") == "income"

    # --- tax ---
    def test_taxes_et_prelevements(self):
        assert classify_movement("Taxes et prélevements sociaux") == "tax"

    def test_prelevements_sociaux(self):
        assert classify_movement("Prélèvements sociaux") == "tax"

    def test_prelevements_sans_accent(self):
        assert classify_movement("Prelevements sociaux") == "tax"

    # --- other ---
    def test_hors_nomenclature(self):
        assert classify_movement("Hors nomenclature") == "other"

    def test_libelle_inconnu(self):
        assert classify_movement("Opération diverse") == "other"

    def test_empty(self):
        assert classify_movement("") == "other"

    def test_none(self):
        assert classify_movement(None) == "other"


# ---------------------------------------------------------------------------
# parse_himalia_text
# ---------------------------------------------------------------------------

MOVEMENT_DETAILLE = """Mouvements sur le compte

31 décembre 2025


Frais de gestion - 31/12/2025
-4 109,19 €
Frais
4 109,19 €

LU1112771503
Helium Selection B-EUR
-168,63 €
Quantité
-0,09471
Cours
1 780,49156 €
Montant net
-168,63 €

FR001400OJI4
DNCA Flexibonds C EUR
-25,04 €
Quantité
-0,236132
Cours
106,04238 €
Montant net
-25,04 €

Participation aux bénéfices - 31/12/2025
14 798,32 €
Montant net
14 798,32 €

SW0000000000
Fonds Euros Swisslife
14 798,32 €
Quantité
14 798,32
Cours
1,00 €
Montant net
14 798,32 €
"""

MOVEMENT_OST = """Mouvements sur le compte

16 février 2026


Ost sans impact fiscal - 16/02/2026
0,00 €

FRIP00000YI4
D Rendement Ca Dividende Forfaitaire 0,90 EUR Février 2025
-27 233,90 €
Quantité
-23,4775
Cours
1 160,00 €
Montant net
-27 233,90 €

FR0010233726
Generali Trésorerie ISR B
27 233,90 €
Quantité
7,0672
Cours
3 853,51 €
Montant net
27 233,90 €
"""


class TestParseHimalia:
    """Tests du parser de texte HIMALIA/Swiss Life."""

    def test_parse_frais_de_gestion(self):
        movements = parse_himalia_text(MOVEMENT_DETAILLE)
        fees = [m for m in movements if m.kind == "fee"]
        assert len(fees) == 1
        assert fees[0].movement_date == date(2025, 12, 31)
        assert fees[0].label == "Frais de gestion"

    def test_parse_frais_items_count(self):
        movements = parse_himalia_text(MOVEMENT_DETAILLE)
        fees = [m for m in movements if m.kind == "fee"]
        assert len(fees[0].items) == 2

    def test_parse_frais_item_units(self):
        movements = parse_himalia_text(MOVEMENT_DETAILLE)
        fees = movements[0]
        helium = next(i for i in fees.items if i.code == "LU1112771503")
        assert helium.units == pytest.approx(-0.09471)
        assert helium.nav == pytest.approx(1780.49156)
        assert helium.net_amount == pytest.approx(-168.63)

    def test_parse_participation_classified_as_income(self):
        """Participation aux bénéfices doit être classifiée 'income'."""
        movements = parse_himalia_text(MOVEMENT_DETAILLE)
        income = [m for m in movements if m.kind == "income"]
        assert len(income) == 1
        assert income[0].movement_date == date(2025, 12, 31)

    def test_parse_participation_item(self):
        movements = parse_himalia_text(MOVEMENT_DETAILLE)
        income = [m for m in movements if m.kind == "income"][0]
        fonds = next(i for i in income.items if i.code == "SW0000000000")
        assert fonds.units == pytest.approx(14798.32)
        assert fonds.net_amount == pytest.approx(14798.32)

    def test_parse_ost_classified_as_buy(self):
        """OST sans impact fiscal doit être classifié 'buy', pas 'tax'."""
        movements = parse_himalia_text(MOVEMENT_OST)
        assert len(movements) == 1
        assert movements[0].kind == "buy"

    def test_parse_ost_items(self):
        movements = parse_himalia_text(MOVEMENT_OST)
        ost = movements[0]
        assert len(ost.items) == 2
        codes = {i.code for i in ost.items}
        assert "FRIP00000YI4" in codes
        assert "FR0010233726" in codes

    def test_parse_ost_negative_item(self):
        movements = parse_himalia_text(MOVEMENT_OST)
        ca_div = next(i for i in movements[0].items if i.code == "FRIP00000YI4")
        assert ca_div.units == pytest.approx(-23.4775)
        assert ca_div.net_amount == pytest.approx(-27233.90)

    def test_parse_empty_text(self):
        movements = parse_himalia_text("")
        assert movements == []

    def test_parse_none(self):
        movements = parse_himalia_text(None)
        assert movements == []

    def test_movement_summary(self):
        movements = parse_himalia_text(MOVEMENT_DETAILLE)
        summ = movement_summary(movements)
        assert summ["movements"] == 2
        assert "fee" in summ["by_kind"]
        assert "income" in summ["by_kind"]


# ---------------------------------------------------------------------------
# Clé de déduplication
# ---------------------------------------------------------------------------

class TestLotKey:
    """Tests de la clé de déduplication."""

    def _lot_key(self, lot: dict) -> str:
        """Réimplémentation locale de lot_key pour tester la logique."""
        lt = str(lot.get("type") or "buy").lower()
        try:
            u = round(float(lot.get("units") or 0), 4)
        except (TypeError, ValueError):
            u = lot.get("units")
        try:
            n = round(float(lot.get("net_amount") or 0), 2)
        except (TypeError, ValueError):
            n = lot.get("net_amount")
        return f"{lot.get('date')}::{lt}::{u}::{n}"

    def test_same_lot_same_key(self):
        lot = {"date": "2025-12-31", "type": "fee", "units": -0.09471, "net_amount": -168.63}
        assert self._lot_key(lot) == self._lot_key(lot)

    def test_float_precision_same_key(self):
        """Deux flottants représentant le même montant → même clé."""
        lot1 = {"date": "2025-12-31", "type": "fee", "units": -174330.93, "net_amount": -2544.65}
        lot2 = {"date": "2025-12-31", "type": "fee", "units": -174330.930000001, "net_amount": -2544.6500001}
        assert self._lot_key(lot1) == self._lot_key(lot2)

    def test_different_date_different_key(self):
        lot1 = {"date": "2025-12-31", "type": "fee", "units": -168.63, "net_amount": -168.63}
        lot2 = {"date": "2025-09-30", "type": "fee", "units": -168.63, "net_amount": -168.63}
        assert self._lot_key(lot1) != self._lot_key(lot2)

    def test_different_type_different_key(self):
        lot1 = {"date": "2025-12-31", "type": "fee", "units": -168.63, "net_amount": -168.63}
        lot2 = {"date": "2025-12-31", "type": "tax", "units": -168.63, "net_amount": -168.63}
        assert self._lot_key(lot1) != self._lot_key(lot2)

    def test_none_units_handled(self):
        """Lot sans units ne plante pas."""
        lot = {"date": "2025-12-31", "type": "fee", "net_amount": -168.63}
        key = self._lot_key(lot)
        assert "2025-12-31" in key


# ---------------------------------------------------------------------------
# Protection units_held fonds euros (test d'intégration)
# ---------------------------------------------------------------------------

MINIMAL_ASSETS_YAML = """assets:
  - asset_id: fonds_euro_test
    type: fonds_euro
    name: "Fonds Euro Test"
    valuation_engine: declarative
    metadata:
      identifier: "AGGV090"

  - asset_id: uc_test
    type: uc_fund
    name: "UC Test"
    isin: "FR0010174144"
    valuation_engine: mark_to_market
"""

MINIMAL_POSITIONS_YAML = """
positions:
  - position_id: pos_fe_001
    asset_id: fonds_euro_test
    holder_type: individual
    wrapper:
      type: assurance_vie
      insurer: Generali
      contract_name: HIMALIA
    investment:
      subscription_date: '2022-01-01'
      invested_amount: 0.0
      units_held: 69856.82
      lots:
        - date: '2022-01-01'
          type: buy
          net_amount: 50000.0
          units: 50000.0
        - date: '2024-12-31'
          type: buy
          net_amount: 1000.0
          units: 1000.0

  - position_id: pos_uc_001
    asset_id: uc_test
    holder_type: individual
    wrapper:
      type: assurance_vie
      insurer: Generali
      contract_name: HIMALIA
    investment:
      subscription_date: '2022-01-01'
      invested_amount: 0.0
      units_held: 100.0
      lots:
        - date: '2022-01-01'
          type: buy
          net_amount: 10000.0
          units: 100.0
"""

MINIMAL_NAV_SOURCES = "nav_sources: {}"


@pytest.fixture
def temp_data_dir(tmp_path):
    """Crée un répertoire data minimal pour les tests d'intégration."""
    # Créer un .git pour que PortfolioCLI détecte tmp_path comme workspace root
    # et n'essaie pas de créer /.cursor qui est hors-portée.
    (tmp_path / ".git").mkdir()

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    market_data = data_dir / "market_data"
    market_data.mkdir()

    (data_dir / "assets.yaml").write_text(MINIMAL_ASSETS_YAML)
    (data_dir / "positions.yaml").write_text(MINIMAL_POSITIONS_YAML)
    (market_data / "nav_sources.yaml").write_text(MINIMAL_NAV_SOURCES)
    (market_data / "fonds_euro_fonds_euro_test.yaml").write_text("declared_rates: []\n")

    return data_dir


MOUVEMENT_FEE_FONDS_EURO = """Mouvements sur le compte

31 décembre 2025


Taxes et prélevements sociaux - 31/12/2025
-471,05 €
Fiscalité
471,05 €

AGGV090
Fonds Euro Test
-471,05 €
Quantité
-471,05
Cours
1,00 €
Montant net
-471,05 €
"""


class TestUnitsHeldProtection:
    """Vérifie que l'import ne détruit pas units_held des fonds euros."""

    def test_fonds_euro_units_held_not_overwritten(self, temp_data_dir):
        """Après import d'un tax sur fonds euro, units_held doit rester inchangé."""
        from portfolio_tracker.cli import PortfolioCLI

        # Écrire le fichier de mouvement
        mv_file = temp_data_dir / "mv_test.txt"
        mv_file.write_text(MOUVEMENT_FEE_FONDS_EURO)

        cli = PortfolioCLI(temp_data_dir)
        original_units_held = 69856.82

        cli.import_movements(
            file_path=str(mv_file),
            insurer="Generali",
            contract_name="HIMALIA",
            dry_run=False,
            only_uc=False,
            update_units_held=True,
        )

        # Recharger et vérifier
        cli2 = PortfolioCLI(temp_data_dir)
        pos = cli2.portfolio.get_position("pos_fe_001")
        assert pos is not None
        assert pos.investment.units_held == pytest.approx(original_units_held), (
            f"units_held du fonds euro a été écrasé ! "
            f"Attendu {original_units_held}, obtenu {pos.investment.units_held}"
        )

    def test_uc_units_held_is_updated(self, temp_data_dir):
        """Après import d'un lot UC, units_held doit être recalculé."""
        from portfolio_tracker.cli import PortfolioCLI

        mv_text = """Mouvements sur le compte

30 septembre 2025


Frais de gestion - 30/09/2025
-10,00 €

FR0010174144
UC Test
-10,00 €
Quantité
-0,5
Cours
20,00 €
Montant net
-10,00 €
"""
        mv_file = temp_data_dir / "mv_uc_test.txt"
        mv_file.write_text(mv_text)

        cli = PortfolioCLI(temp_data_dir)
        cli.import_movements(
            file_path=str(mv_file),
            insurer="Generali",
            contract_name="HIMALIA",
            dry_run=False,
            only_uc=True,
            update_units_held=True,
        )

        cli2 = PortfolioCLI(temp_data_dir)
        pos = cli2.portfolio.get_position("pos_uc_001")
        assert pos is not None
        # units_held = 100.0 (initial buy) + (-0.5) (fee) = 99.5
        assert pos.investment.units_held == pytest.approx(99.5)

    def test_no_duplicate_on_double_import(self, temp_data_dir):
        """Importer deux fois le même fichier ne crée pas de doublon."""
        from portfolio_tracker.cli import PortfolioCLI

        mv_text = """Mouvements sur le compte

30 septembre 2025


Frais de gestion - 30/09/2025
-10,00 €

FR0010174144
UC Test
-10,00 €
Quantité
-0,5
Cours
20,00 €
Montant net
-10,00 €
"""
        mv_file = temp_data_dir / "mv_dedup_test.txt"
        mv_file.write_text(mv_text)

        cli1 = PortfolioCLI(temp_data_dir)
        cli1.import_movements(
            file_path=str(mv_file),
            insurer="Generali",
            contract_name="HIMALIA",
            dry_run=False,
            only_uc=True,
            update_units_held=True,
        )

        cli2 = PortfolioCLI(temp_data_dir)
        cli2.import_movements(
            file_path=str(mv_file),
            insurer="Generali",
            contract_name="HIMALIA",
            dry_run=False,
            only_uc=True,
            update_units_held=True,
        )

        cli3 = PortfolioCLI(temp_data_dir)
        pos = cli3.portfolio.get_position("pos_uc_001")
        # 1 lot initial + 1 lot fee (pas 2 lots fee)
        fee_lots = [l for l in pos.investment.lots if isinstance(l, dict) and l.get("type") == "fee"]
        assert len(fee_lots) == 1, f"Doublon détecté : {len(fee_lots)} lots fee au lieu de 1"
