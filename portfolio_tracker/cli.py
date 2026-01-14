"""
CLI - Interface en ligne de commande pour Portfolio Tracker
"""
import argparse
from pathlib import Path
from datetime import datetime, date
from typing import Optional, Literal, List, Dict, Any, Tuple
from enum import Enum
from dataclasses import dataclass
import shutil
import re

from .core import Portfolio, Asset, Position
from .core.asset import AssetType, ValuationEngine
from .core.position import HolderType, WrapperType
from .valuation import EventBasedEngine, DeclarativeEngine, MarkToMarketEngine, HybridEngine
from .valuation.base import ValuationEvent, ValuationResult
from .alerts import AlertManager, ConsoleNotifier, AlertSeverity
from .market.underlyings import UnderlyingProvider
from .market.rates import RatesProvider
from .market.quantalys import QuantalysProvider
from .market.nav_daily import update_uc_navs
from .importers.himalia_movements import parse_himalia_text, movement_summary
# Advisory imports are lazy (only when advice command is used) to avoid requiring httpx
from .market.fetch_underlyings import (
    fetch_solactive_indexhistory,
    fetch_euronext_recent_history,
    fetch_merqube_indexhistory,
    fetch_natixis_index,
    fetch_investing_rate,
)


# ==============================================================================
# CLASSIFICATION CENTRALISÉE DES MOUVEMENTS
# ==============================================================================
# Cette section contient LA SEULE SOURCE DE VÉRITÉ pour identifier les mouvements
# Toutes les autres parties du code DOIVENT utiliser ces classes/méthodes

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
        lot_date_raw = lot.get('date')
        
        # Parser la date
        if not lot_date_raw:
            return None
        try:
            if isinstance(lot_date_raw, str):
                lot_date = datetime.fromisoformat(lot_date_raw).date()
            else:
                lot_date = lot_date_raw
        except:
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
        lot_date_raw = lot.get('date')
        if lot_date_raw and position_id in self._external_deposits_seen:
            try:
                if isinstance(lot_date_raw, str):
                    lot_date_obj = datetime.fromisoformat(lot_date_raw).date()
                else:
                    lot_date_obj = lot_date_raw
                # Si c'est le 31/12 et qu'on a déjà vu des versements externes
                if lot_date_obj.month == 12 and lot_date_obj.day == 31:
                    return False  # C'est une participation aux bénéfices
            except:
                pass
        
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
            lot_date_raw = lot.get('date')
            if not lot_date_raw:
                return date.min
            try:
                if isinstance(lot_date_raw, str):
                    return datetime.fromisoformat(lot_date_raw).date()
                return lot_date_raw
            except:
                return date.min
        
        sorted_lots = sorted(lots, key=get_lot_date)
        
        classified = []
        for lot in sorted_lots:
            classified_lot = self.classify_lot(lot, position_id)
            if classified_lot:
                classified.append(classified_lot)
        
        # Trier par date (déjà trié, mais on le fait pour être sûr)
        classified.sort(key=lambda cl: cl.date)
        
        return classified


class PortfolioCLI:
    """Interface en ligne de commande pour le portfolio tracker"""
    
    def __init__(self, data_dir: Path):
        """
        Initialise le CLI.
        
        Args:
            data_dir: Chemin vers le dossier data/
        """
        self.data_dir = Path(data_dir)
        self.portfolio = Portfolio(data_dir)
        self.lot_classifier = LotClassifier()  # Classificateur centralisé
        self.market_data_dir = self.data_dir / "market_data"
        self.underlyings_provider = UnderlyingProvider(self.market_data_dir)
        self.rates_provider = RatesProvider(self.market_data_dir)
        self.quantalys_provider = QuantalysProvider(self.market_data_dir)
        # Debug log path - use cwd to find workspace root
        # Try to find workspace root by going up from data_dir until we find .git or portfolio_tracker
        workspace_root = Path(data_dir).resolve()
        while workspace_root != workspace_root.parent:
            if (workspace_root / "portfolio_tracker").exists() or (workspace_root / ".git").exists():
                break
            workspace_root = workspace_root.parent
        self.debug_log_path = workspace_root / ".cursor" / "debug.log"
        # Ensure directory exists
        self.debug_log_path.parent.mkdir(parents=True, exist_ok=True)
        # Ensure log directory exists
        try:
            self.debug_log_path.parent.mkdir(parents=True, exist_ok=True)
        except: pass
        
        # Engines de valorisation
        self.engines = {
            ValuationEngine.EVENT_BASED: EventBasedEngine(self.data_dir),
            ValuationEngine.DECLARATIVE: DeclarativeEngine(self.data_dir),
            ValuationEngine.MARK_TO_MARKET: MarkToMarketEngine(self.data_dir),
            ValuationEngine.HYBRID: HybridEngine(self.data_dir),
        }
    
    def _print_structured_product_details(self, asset: Asset, position: Position, result):
        """Affiche les détails d'un produit structuré (strike, sous-jacent, autocall, etc.)"""
        from datetime import date
        today = datetime.now().date()
        rates_provider = RatesProvider(self.market_data_dir)
        
        def find_initial_observation_date(asset_, evts):
            aiod = (asset_.metadata or {}).get("initial_observation_date")
            if aiod:
                return str(aiod)
            for e in evts:
                md = e.metadata or {}
                iod = md.get("initial_observation_date")
                if iod:
                    return str(iod)
            return None
        
        def next_observation_event(evts, today_):
            candidates = []
            for e in evts:
                et = (e.event_type or "").lower()
                if et.endswith("_expected") or et.endswith("_payment_expected"):
                    continue
                md = e.metadata or {}
                if not md.get("expected", False):
                    continue
                if ("observation" not in et) and (et not in {"autocall_possible"}):
                    continue
                od = md.get("observation_date") or e.event_date
                try:
                    od_date = datetime.fromisoformat(str(od)).date() if not hasattr(od, "year") else od
                except Exception:
                    continue
                if od_date >= today_:
                    candidates.append((od_date, e))
            if not candidates:
                return None, None
            od_date, ev = min(candidates, key=lambda x: x[0])
            return od_date.isoformat(), ev
        
        evts = result.events if result else []
        next_obs, next_obs_event = next_observation_event(evts, today)
        
        # Trouver le sous-jacent
        underlying_id = (asset.metadata or {}).get("underlying_id") or (asset.metadata or {}).get("underlying")
        if not underlying_id:
            for e in evts:
                md = e.metadata or {}
                u = md.get("underlying")
                if u:
                    underlying_id = u
                    break
        
        strike_val = None
        strike_date_used = None
        strike_note = None
        is_rate_like = isinstance(underlying_id, str) and underlying_id.upper().startswith("CMS_")
        
        if underlying_id and not is_rate_like:
            strike_date = position.investment.subscription_date
            iod = find_initial_observation_date(asset, evts)
            if iod:
                try:
                    strike_date = datetime.fromisoformat(iod).date()
                except Exception:
                    pass
            
            initial_level = (asset.metadata or {}).get("initial_level")
            if initial_level is not None:
                try:
                    strike_val = float(initial_level)
                    strike_date_used = strike_date
                    strike_note = "from_brochure_level"
                except Exception:
                    pass
            
            strike = self.underlyings_provider.get_data(underlying_id, "underlying", strike_date)
            if strike_val is None and strike:
                strike_val = strike.get("value")
                strike_date_used = strike.get("date")
                if strike_date_used and strike_date_used != strike_date:
                    strike_note = f"fallback<= {strike_date.isoformat()}"
            elif strike_val is None:
                strike_note = f"no_data_for<= {strike_date.isoformat()}"
        
        # Valeur actuelle du sous-jacent
        underlying_current = None
        underlying_current_date = None
        perf_vs_strike = None
        underlying_current_note = None
        
        if underlying_id:
            cur = self.underlyings_provider.get_data(underlying_id, "underlying", today)
            if cur:
                underlying_current = cur.get("value")
                d = cur.get("date")
                underlying_current_date = d.isoformat() if d else None
            else:
                cur_r = rates_provider.get_data(str(underlying_id), "rate", today)
                if cur_r:
                    underlying_current = cur_r.get("value")
                    d = cur_r.get("date")
                    underlying_current_date = d.isoformat() if d else None
                    underlying_current_note = "rates"
                else:
                    md_asset = asset.metadata or {}
                    manual_val = md_asset.get("underlying_current_level") or md_asset.get("current_level")
                    if manual_val is not None:
                        try:
                            underlying_current = float(manual_val)
                            manual_date = md_asset.get("underlying_current_date") or md_asset.get("current_level_date")
                            underlying_current_date = str(manual_date) if manual_date else today.isoformat()
                            underlying_current_note = "manual"
                        except Exception:
                            pass
        
        if underlying_id and (not is_rate_like) and strike_val is None:
            hist = self.underlyings_provider.get_history(underlying_id)
            if hist:
                strike_val = hist[0].value
                strike_date_used = hist[0].point_date
                strike_note = f"approx_first_available({hist[0].point_date.isoformat()})"
        
        if strike_val is not None and underlying_current is not None and strike_val != 0:
            try:
                perf_vs_strike = (float(underlying_current) / float(strike_val) - 1.0) * 100.0
            except Exception:
                pass
        
        # Seuil d'autocall
        redemption_trigger = None
        redemption_trigger_level = None
        will_strike = None
        
        if next_obs_event is not None:
            md = next_obs_event.metadata or {}
            pct = md.get("autocall_threshold_pct_of_initial") or md.get("autocall_barrier_pct_of_initial")
            if pct is not None and strike_val is not None:
                try:
                    redemption_trigger_pct = float(pct)
                    redemption_trigger_level = float(strike_val) * float(pct) / 100.0
                    redemption_trigger = f">= {redemption_trigger_level:.4g} ({redemption_trigger_pct:.2f}% du initial)"
                    
                    # Vérifier si le strike sera atteint
                    if underlying_current is not None and redemption_trigger_level is not None:
                        will_strike = float(underlying_current) >= float(redemption_trigger_level)
                except Exception:
                    pass
            else:
                cond = md.get("autocall_condition") or ""
                if isinstance(cond, str) and "initial" in cond.lower() and strike_val is not None:
                    redemption_trigger_level = float(strike_val)
                    redemption_trigger = f">= {strike_val:.4g} (Initial)"
                    if underlying_current is not None:
                        will_strike = float(underlying_current) >= float(strike_val)
                elif isinstance(cond, str) and cond:
                    redemption_trigger = cond
        
        # Afficher les informations
        details_lines = []
        
        # Prochain événement
        if next_obs:
            details_lines.append(f"     Prochain: {next_obs} ({next_obs_event.event_type if next_obs_event else 'N/A'})")
        
        # Strike
        if strike_val is not None:
            strike_str = f"{strike_val:.4g}"
            if strike_date_used:
                strike_str += f" @ {strike_date_used.isoformat() if hasattr(strike_date_used, 'isoformat') else strike_date_used}"
            if strike_note:
                strike_str += f" [{strike_note}]"
            details_lines.append(f"     Strike: {strike_str}")
        
        # Sous-jacent actuel
        if underlying_current is not None:
            und_str = f"{underlying_current:.4g}"
            if underlying_current_date:
                und_str += f" @ {underlying_current_date}"
            if underlying_current_note:
                und_str += f" [{underlying_current_note}]"
            details_lines.append(f"     Sous-jacent: {und_str}")
        
        # Performance vs strike
        if perf_vs_strike is not None:
            details_lines.append(f"     Perf vs strike: {perf_vs_strike:+.2f}%")
        
        # Seuil d'autocall
        if redemption_trigger:
            strike_status = "✅ STRIKE" if will_strike else "❌ Pas de strike"
            details_lines.append(f"     Autocall: {redemption_trigger} → {strike_status}")
        
        # Autocallé
        autocalled = (result.metadata or {}).get("autocalled")
        if autocalled:
            details_lines.append(f"     ⚠️  Autocallé")
        
        # Coupons reçus
        coupons_received = (result.metadata or {}).get("coupons_received")
        if coupons_received:
            details_lines.append(f"     Coupons reçus: {coupons_received:,.2f} €")
        
        # Frais payés
        cashflow_adjustments = (result.metadata or {}).get("cashflow_adjustments")
        if cashflow_adjustments is not None and abs(float(cashflow_adjustments)) > 0.01:
            # cashflow_adjustments est négatif (frais réduisent la valeur)
            fees_total = abs(float(cashflow_adjustments))
            details_lines.append(f"     Frais payés: {fees_total:,.2f} €")
        
        for line in details_lines:
            print(line)
    
    def status_by_asset_type(self, asset_type: Optional[str] = None):
        """Affiche l'état par type d'actif"""
        print("\n" + "="*70)
        print("PORTFOLIO TRACKER - État par Type d'Actif")
        print("="*70 + "\n")
        
        # Grouper par type
        by_type = {}
        
        for asset in self.portfolio.list_all_assets():
            if asset_type and asset.asset_type.value != asset_type:
                continue
            
            if asset.asset_type not in by_type:
                by_type[asset.asset_type] = []
            
            by_type[asset.asset_type].append(asset)
        
        # Afficher chaque type
        for atype, assets in by_type.items():
            print(f"📂 {atype.value.upper().replace('_', ' ')}")
            print("-" * 70)
            
            type_value = 0.0
            type_invested = 0.0
            
            for asset in assets:
                positions = self.portfolio.get_positions_by_asset(asset.asset_id)
                
                for position in positions:
                    engine = self.engines.get(asset.valuation_engine)
                    if not engine:
                        continue
                    
                    result = engine.valuate(asset, position)
                    
                    if result.current_value:
                        type_value += result.current_value
                    if result.invested_amount:
                        type_invested += result.invested_amount
                
                # Afficher l'actif
                positions_count = len(positions)
                print(f"  • {asset.name} ({positions_count} position(s))")
            
            pnl = type_value - type_invested
            pnl_pct = (pnl / type_invested * 100) if type_invested > 0 else 0
            
            print()
            print(f"  💰 Total: {type_value:,.2f} € | P&L: {pnl:+,.2f} € ({pnl_pct:+.2f}%)")
            print()
    
    def alerts(self, severity: Optional[str] = None):
        """Affiche les alertes"""
        alert_manager = AlertManager(self.portfolio, self.market_data_dir)
        alert_manager.add_default_rules()
        
        if severity:
            try:
                sev = AlertSeverity(severity.lower())
                triggers = alert_manager.check_by_severity(sev)
            except ValueError:
                print(f"Sévérité invalide: {severity}")
                print("Valeurs valides: info, warning, error")
                return
        else:
            triggers = alert_manager.check_all()
        
        notifier = ConsoleNotifier()
        notifier.notify(triggers)
    
    def list_assets(self):
        """Liste tous les actifs"""
        print("\n" + "="*70)
        print("PORTFOLIO TRACKER - Liste des Actifs")
        print("="*70 + "\n")
        
        for asset in self.portfolio.list_all_assets():
            print(f"🔹 {asset.asset_id}")
            print(f"   Nom: {asset.name}")
            print(f"   Type: {asset.asset_type.value}")
            print(f"   Moteur: {asset.valuation_engine.value}")
            if asset.isin:
                print(f"   ISIN: {asset.isin}")
            
            positions = self.portfolio.get_positions_by_asset(asset.asset_id)
            print(f"   Positions: {len(positions)}")
            print()
    
    def list_positions(self):
        """Liste toutes les positions"""
        print("\n" + "="*70)
        print("PORTFOLIO TRACKER - Liste des Positions")
        print("="*70 + "\n")
        
        for position in self.portfolio.list_all_positions():
            asset = self.portfolio.get_asset(position.asset_id)
            asset_name = asset.name if asset else "Inconnu"
            
            print(f"🔸 {position.position_id}")
            print(f"   Actif: {asset_name} ({position.asset_id})")
            print(f"   Détenteur: {position.holder_type.value}")
            print(f"   Enveloppe: {position.wrapper.wrapper_type.value}")
            print(f"   Assureur: {position.wrapper.insurer}")
            print(f"   Date: {position.investment.subscription_date}")
            if position.investment.invested_amount:
                print(f"   Investi: {position.investment.invested_amount:,.2f} €")
            if position.investment.units_held:
                print(f"   Parts: {position.investment.units_held}")
            print()

    def update_uc_navs(self, *, target_date: Optional[str] = None, set_values: Optional[list] = None, headless: bool = False):
        """
        Tâche quotidienne: enregistre la VL du jour pour les UC.

        Usage (manuel):
          python3 -m portfolio_tracker.cli --data-dir portfolio_tracker/data update-uc-navs \
            --set uc_eleva_absolute_return_europe=153.58 --set uc_helium_selection_b_eur=1742.12

        Usage (auto):
          - Configurer portfolio_tracker/data/market_data/nav_sources.yaml
          - Lancer update-uc-navs sans --set
        """
        from datetime import datetime, date

        if target_date:
            d = datetime.fromisoformat(str(target_date)).date()
        else:
            d = datetime.now().date()

        market_data_dir = self.data_dir / "market_data"
        results, positions_changed = update_uc_navs(
            portfolio=self.portfolio,
            market_data_dir=market_data_dir,
            target_date=d,
            set_values=set_values,
            headless=headless,
        )

        ok = [r for r in results if r.status == "ok"]
        skipped = [r for r in results if r.status == "skipped"]
        errors = [r for r in results if r.status == "error"]

        print("\n" + "=" * 70)
        print("PORTFOLIO TRACKER - Mise à jour quotidienne VL UC")
        print("=" * 70 + "\n")
        print(f"Date cible: {d.isoformat()}")
        print(f"Positions modifiées (purchase_nav): {'oui' if positions_changed else 'non'}")
        print()

        for r in results:
            sym = "✓" if r.status == "ok" else "•" if r.status == "skipped" else "✗"
            changed = " (upsert)" if r.changed else ""
            print(f"{sym} {r.asset_id}: {r.message}{changed}")

        print()
        print(f"✓ OK: {len(ok)} | • Skipped: {len(skipped)} | ✗ Erreurs: {len(errors)}")

    def set_purchase_nav(
        self,
        *,
        position_id: Optional[str] = None,
        asset_id: Optional[str] = None,
        value: Optional[float] = None,
        currency: str = "EUR",
        clear: bool = False,
    ):
        """
        Renseigne ou efface la VL d'achat (purchase_nav) sur une ou plusieurs positions UC.
        """
        if (not position_id) and (not asset_id):
            raise ValueError("Il faut fournir --position-id ou --asset-id")
        if not clear and value is None:
            raise ValueError("Il faut fournir --value (ou utiliser --clear)")

        targets = []
        for p in self.portfolio.list_all_positions():
            if position_id and p.position_id != position_id:
                continue
            if asset_id and p.asset_id != asset_id:
                continue
            targets.append(p)

        if not targets:
            raise ValueError("Aucune position trouvée pour ce filtre")

        changed = False
        for p in targets:
            if clear:
                if p.investment.purchase_nav is not None or p.investment.purchase_nav_source is not None:
                    p.investment.purchase_nav = None
                    p.investment.purchase_nav_currency = "EUR"
                    p.investment.purchase_nav_source = None
                    changed = True
            else:
                p.investment.purchase_nav = float(value)
                p.investment.purchase_nav_currency = str(currency or "EUR")
                p.investment.purchase_nav_source = "manual"
                changed = True

        if changed:
            path = self.portfolio.save_positions()
            print(f"✓ positions.yaml mis à jour: {path}")
            print(f"✓ positions affectées: {len(targets)}")
        else:
            print("• Aucun changement (déjà à jour).")

    def add_uc_lot(
        self,
        *,
        position_id: str,
        lot_date: str,
        lot_type: str = "buy",
        units: float,
        nav: Optional[float] = None,
        net_amount: Optional[float] = None,
        gross_amount: Optional[float] = None,
        fees_amount: Optional[float] = None,
        currency: str = "EUR",
        update_units_held: bool = False,
    ):
        """
        Ajoute un lot d'achat UC à une position (donc à une enveloppe précise).
        """
        from datetime import datetime, date

        pos = self.portfolio.get_position(position_id)
        if not pos:
            raise ValueError(f"Position introuvable: {position_id}")

        d = datetime.fromisoformat(str(lot_date)).date()
        lt = str(lot_type or "buy").strip().lower()
        if lt not in {"buy", "fee", "tax", "income", "sell", "other"}:
            raise ValueError("type de lot invalide (buy|fee|tax|income|sell|other)")
        lot = {
            "date": d.isoformat(),
            "type": lt,
            "units": float(units),
            "currency": str(currency or "EUR"),
        }
        if nav is not None:
            lot["nav"] = float(nav)
        if net_amount is not None:
            lot["net_amount"] = float(net_amount)
        if gross_amount is not None:
            lot["gross_amount"] = float(gross_amount)
        if fees_amount is not None:
            lot["fees_amount"] = float(fees_amount)

        # Validation minimale: il faut au moins units + (net_amount ou gross_amount ou nav)
        if lot.get("net_amount") is None and lot.get("gross_amount") is None and lot.get("nav") is None:
            raise ValueError("Il faut fournir au moins --net-amount, ou --gross-amount, ou --nav (avec units).")

        pos.investment.lots = list(pos.investment.lots or [])
        pos.investment.lots.append(lot)

        # Recalcule un PRU (coût moyen) basé sur les achats (buy) uniquement.
        try:
            total_units_buy = 0.0
            total_cost_buy = 0.0
            for x in pos.investment.lots:
                if not isinstance(x, dict):
                    continue
                if x.get("units") is None:
                    continue
                if str(x.get("type") or "buy").lower() != "buy":
                    continue
                u = float(x.get("units"))
                net = x.get("net_amount")
                fees = x.get("fees_amount") or 0.0
                gross = x.get("gross_amount")
                if net is None:
                    if gross is not None:
                        net = float(gross) - float(fees or 0.0)
                    elif x.get("nav") is not None:
                        net = float(x["nav"]) * u
                if net is not None:
                    total_units_buy += u
                    total_cost_buy += float(net)
            if total_units_buy and total_units_buy != 0:
                pos.investment.purchase_nav = float(total_cost_buy) / float(total_units_buy)
                pos.investment.purchase_nav_source = "lots"
        except Exception:
            pass

        if update_units_held:
            try:
                total_units = sum(float(x.get("units")) for x in pos.investment.lots if isinstance(x, dict) and x.get("units") is not None)
                pos.investment.units_held = float(total_units)
            except Exception:
                pass

        path = self.portfolio.save_positions()
        print(f"✓ Lot ajouté à {position_id} ({pos.asset_id}) | positions.yaml: {path}")

    def import_movements(
        self,
        *,
        file_path: str,
        insurer: str = "Generali",
        contract_name: str = "HIMALIA",
        dry_run: bool = True,
        only_uc: bool = True,
        update_units_held: bool = True,
        since_date: Optional[str] = None,
    ):
        """
        Import des mouvements depuis un export texte (format Generali/Swiss Life).
        
        Cette commande peut être utilisée régulièrement pour importer de nouveaux mouvements.
        Les doublons sont automatiquement détectés et ignorés (basé sur date, type, units, net_amount).
        
        Args:
            file_path: Chemin du fichier texte exporté/collé
            insurer: Assureur (ex: "Generali", "Swiss Life")
            contract_name: Nom du contrat (ex: "HIMALIA", "SwissLife Capi Stratégic Premium")
            dry_run: Si True, affiche ce qui sera importé sans modifier positions.yaml
            only_uc: Si True, importe uniquement les UC (sinon tous les actifs)
            update_units_held: Si True, recalcule units_held à partir de la somme des lots
            since_date: Date ISO (YYYY-MM-DD) - n'importer que les mouvements depuis cette date (utile pour imports incrémentaux)
        """
        from pathlib import Path

        txt = Path(file_path).read_text(encoding="utf-8")
        movements = parse_himalia_text(txt)
        
        # Filtrer par date si since_date est fourni
        if since_date:
            from datetime import datetime, date
            since = datetime.strptime(since_date, "%Y-%m-%d").date()
            movements = [mv for mv in movements if mv.movement_date >= since]
            if not movements:
                print(f"⚠ Aucun mouvement trouvé depuis {since_date}")
                return
        
        summ = movement_summary(movements)

        # mapping code (ISIN) -> asset_id
        code_to_asset = {}
        for a in self.portfolio.list_all_assets():
            if a.isin:
                code_to_asset[str(a.isin).strip()] = a.asset_id
            # fonds euro Generali: identifier (AGGV090)
            if (a.metadata or {}).get("identifier"):
                code_to_asset[str((a.metadata or {}).get("identifier")).strip()] = a.asset_id

        # wrapper filter (Himalia)
        def is_target_wrapper(pos):
            return (
                (pos.wrapper.insurer or "") == insurer
                and (pos.wrapper.contract_name or "") == contract_name
            )

        positions = [p for p in self.portfolio.list_all_positions() if is_target_wrapper(p)]
        pos_by_asset = {}
        for p in positions:
            pos_by_asset.setdefault(p.asset_id, []).append(p)

        planned = []
        for mv in movements:
            for it in mv.items:
                asset_id = code_to_asset.get(it.code)
                if not asset_id:
                    continue
                asset = self.portfolio.get_asset(asset_id)
                if not asset:
                    continue
                if only_uc and asset.asset_type.value not in {"uc_fund", "uc_illiquid"}:
                    continue
                target_positions = pos_by_asset.get(asset_id) or []
                if not target_positions:
                    continue

                # déterminer lot_type (buy/fee/tax/income/other)
                lot_type = mv.kind
                # heuristique: si net_amount > 0 et units > 0, on force buy
                if it.net_amount is not None and it.units is not None:
                    if it.net_amount > 0 and it.units > 0:
                        lot_type = "buy"
                    elif it.net_amount < 0 and it.units < 0 and mv.kind == "buy":
                        lot_type = "other"
                
                # Détecter si c'est un apport externe (versement depuis compte bancaire)
                # vs réinvestissement interne (arbitrage au sein du contrat)
                is_external = False
                label_lower = (mv.label or "").lower()
                if lot_type == "buy" and it.net_amount and it.net_amount > 0:
                    # Apports externes : "Versement initial", "Versement libre complémentaire", etc.
                    if any(keyword in label_lower for keyword in ["versement initial", "versement libre", "versement complémentaire"]):
                        is_external = True
                    # Arbitrages sont des réinvestissements internes (pas external)

                for p in target_positions:
                    planned.append(
                        {
                            "position_id": p.position_id,
                            "asset_id": asset_id,
                            "date": mv.movement_date.isoformat(),
                            "type": lot_type,
                            "units": it.units,
                            "nav": it.nav,
                            "net_amount": it.net_amount,
                            "external": is_external,
                        }
                    )

        print("\n" + "=" * 70)
        print("IMPORT MOUVEMENTS")
        print("=" * 70 + "\n")
        print(f"Fichier: {file_path}")
        print(f"Wrapper: {insurer} / {contract_name}")
        print(f"Parsed: {summ}")
        print(f"Planned lots: {len(planned)}")
        print(f"Mode: {'DRY-RUN' if dry_run else 'APPLY'}")
        print()

        # show a small preview
        for row in planned[:25]:
            print(
                f"• {row['date']} {row['type']} {row['asset_id']} ({row['position_id']}): "
                f"units={row['units']} nav={row['nav']} net={row['net_amount']}"
            )
        if len(planned) > 25:
            print(f"... ({len(planned) - 25} autres)")

        if dry_run:
            return

        # Apply: upsert lots by (date,type,units,net_amount)
        def lot_key(lot: dict) -> str:
            lt = str(lot.get("type") or "buy").lower()
            return f"{lot.get('date')}::{lt}::{lot.get('units')}::{lot.get('net_amount')}"

        def normalize_lot(lot: dict) -> dict:
            if not isinstance(lot, dict):
                return lot
            out = dict(lot)
            out["type"] = str(out.get("type") or "buy").lower()
            return out

        changed = 0
        for row in planned:
            p = self.portfolio.get_position(row["position_id"])
            if not p:
                continue
            # Normalize + dedupe existing lots first (handles older lots without 'type')
            p.investment.lots = [normalize_lot(x) for x in list(p.investment.lots or [])]
            dedup_map = {}
            for x in p.investment.lots:
                if not isinstance(x, dict):
                    continue
                dedup_map[lot_key(x)] = x
            p.investment.lots = list(dedup_map.values())

            # Recalc units_held after normalization/dedup (even if no new lot is added later)
            if update_units_held:
                try:
                    p.investment.units_held = float(
                        sum(float(x.get("units")) for x in p.investment.lots if isinstance(x, dict) and x.get("units") is not None)
                    )
                except Exception:
                    pass

            new_lot = {
                "date": row["date"],
                "type": row["type"],
                "units": row["units"],
                "currency": "EUR",
            }
            if row.get("nav") is not None:
                new_lot["nav"] = row["nav"]
            if row.get("net_amount") is not None:
                new_lot["net_amount"] = row["net_amount"]
            # Marquer les apports externes pour le calcul du capital investi
            if row.get("external", False):
                new_lot["external"] = True

            existing_keys = {lot_key(x) for x in p.investment.lots if isinstance(x, dict)}
            k = lot_key(new_lot)
            if k in existing_keys:
                continue
            p.investment.lots.append(new_lot)
            changed += 1

            if update_units_held:
                try:
                    p.investment.units_held = float(
                        sum(float(x.get("units")) for x in p.investment.lots if isinstance(x, dict) and x.get("units") is not None)
                    )
                except Exception:
                    pass

        if changed:
            path = self.portfolio.save_positions()
            print(f"\n✓ Lots ajoutés: {changed}")
            print(f"✓ positions.yaml mis à jour: {path}")
            
            # Recalculer automatiquement les invested_amount depuis les lots marqués external
            self.recalculate_invested_amounts()
        else:
            # Même si aucun nouveau lot, on a peut-être normalisé/dédoublonné
            path = self.portfolio.save_positions()
            print("\n• Aucun lot ajouté (déjà à jour).")
            print(f"✓ positions.yaml normalisé/dédoublonné: {path}")

    # Alias pour compatibilité
    def import_himalia_movements(self, **kwargs):
        """Alias pour import_movements (compatibilité)"""
        return self.import_movements(**kwargs)

    def calculate_fonds_euro_rates(self, position_id: str, *, dry_run: bool = False, value_at_end_year: Optional[float] = None):
        """
        Calcule les taux déclarés d'un fonds euro à partir des cashflows et de la valeur actuelle.
        
        Utilise une méthode itérative (bissection) pour trouver les taux annuels qui expliquent
        la valeur actuelle (units_held) compte tenu des cashflows historiques.
        """
        import yaml
        from datetime import date
        
        position = self.portfolio.get_position(position_id)
        if not position:
            print(f"⚠ Position {position_id} introuvable")
            return
        
        asset = self.portfolio.get_asset(position.asset_id)
        if not asset or asset.asset_type != AssetType.FONDS_EURO:
            print(f"⚠ {position_id} n'est pas un fonds euro")
            return
        
        # Récupérer les données
        lots = position.investment.lots or []
        units_held = position.investment.units_held
        subscription_date = position.investment.subscription_date
        
        if units_held is None:
            print(f"⚠ units_held non disponible pour {position_id}")
            return
        
        current_value = float(units_held)
        valuation_date = datetime.now().date()
        
        # Extraire les cashflows
        cashflows = []
        for lot in sorted(lots, key=lambda x: str(x.get('date', ''))):
            if not isinstance(lot, dict):
                continue
            lot_date_str = lot.get('date')
            if not lot_date_str:
                continue
            
            try:
                lot_date = datetime.fromisoformat(str(lot_date_str)).date()
            except:
                continue
            
            lot_type = str(lot.get('type', 'buy')).lower()
            net_amt = lot.get('net_amount', 0.0)
            
            if lot_type == 'buy' and net_amt > 0:
                cashflows.append((lot_date, 'buy', net_amt))
            elif lot_type in ('sell', 'other') and net_amt < 0:
                cashflows.append((lot_date, 'sell', abs(net_amt)))
            elif lot_type == 'fee' and net_amt < 0:
                cashflows.append((lot_date, 'fee', abs(net_amt)))
        
        if not cashflows:
            print(f"⚠ Aucun cashflow trouvé pour {position_id}")
            return
        
        print(f"📊 Calcul des taux déclarés pour {position_id}")
        print(f"   Asset: {asset.asset_id}")
        print(f"   Date souscription: {subscription_date}")
        print(f"   Valeur actuelle (units_held): {current_value:,.2f} €")
        print(f"   Date valorisation: {valuation_date}")
        print(f"   Cashflows: {len(cashflows)}")
        
        # Fonction pour calculer la valeur avec des taux donnés
        def compute_value_with_rates(rates_dict):
            """Calcule la valeur en appliquant les taux année par année"""
            value = 0.0
            
            for cf_date, cf_type, cf_amount in cashflows:
                if cf_date > valuation_date:
                    continue
                
                # Capitaliser depuis la date du cashflow jusqu'à la date de valorisation
                cf_value = cf_amount
                start_year = cf_date.year
                end_year = valuation_date.year
                
                # Capitaliser seulement jusqu'à la fin de la dernière année complète
                # (pas pour l'année en cours)
                last_complete_year = valuation_date.year - 1
                end_year_for_calc = min(end_year, last_complete_year)
                
                for year in range(start_year, end_year_for_calc + 1):
                    # Si l'année n'est pas dans rates_dict, on saute
                    if year not in rates_dict:
                        continue
                    
                    rate = rates_dict[year] / 100.0
                    
                    if year == start_year and year == end_year_for_calc:
                        # Même année (cashflow et valorisation dans la même année complète)
                        days_in_year = 365
                        days_invested = (date(year, 12, 31) - cf_date).days
                        if days_invested > 0:
                            partial_rate = rate * (days_invested / days_in_year)
                            cf_value *= (1 + partial_rate)
                    elif year == start_year:
                        # Première année partielle
                        days_in_year = 365
                        days_invested = (date(year, 12, 31) - cf_date).days
                        if days_invested > 0:
                            partial_rate = rate * (days_invested / days_in_year)
                            cf_value *= (1 + partial_rate)
                    elif year == end_year_for_calc:
                        # Dernière année complète (fin de l'année précédente)
                        # Année complète
                        cf_value *= (1 + rate)
                    else:
                        # Année complète
                        cf_value *= (1 + rate)
                
                if cf_type == 'buy':
                    value += cf_value
                elif cf_type == 'sell':
                    value -= cf_value
                # Les frais ne sont pas soustraits car ils sont déjà inclus dans units_held
                # (les frais réduisent le capital mais sont déjà reflétés dans la valeur déclarée)
            
            return value
        
        # Calculer les années complètes (sans l'année en cours)
        current_year = valuation_date.year
        years_to_calculate = [y for y in range(subscription_date.year, current_year)]
        nb_years = len(years_to_calculate)
        
        if not years_to_calculate:
            print("⚠ Aucune année complète à calculer (souscription trop récente)")
            return
        
        print(f"\n📅 Années complètes: {years_to_calculate} ({nb_years} années)")
        print(f"   (L'année en cours {current_year} n'est pas comptée)")
        
        # Vérifier qu'on a des données suffisantes
        if not cashflows:
            print("⚠ Aucun cashflow trouvé")
            return
        
        # Afficher le détail des mouvements
        print(f"\n📊 Détail des mouvements:")
        total_achats = 0.0
        total_rachats = 0.0
        for cf_date, cf_type, cf_amount in cashflows:
            if cf_type == 'buy':
                total_achats += cf_amount
                print(f"   {cf_date} | ACHAT  | +{cf_amount:,.2f} €")
            elif cf_type == 'sell':
                total_rachats += cf_amount
                print(f"   {cf_date} | RACHAT | -{cf_amount:,.2f} €")
        
        print(f"\n   Total achats: {total_achats:,.2f} €")
        print(f"   Total rachats: {total_rachats:,.2f} €")
        print(f"   Valeur actuelle: {current_value:,.2f} €")
        
        # Fonction pour calculer la valeur capitalisée avec un taux donné
        def compute_value_with_rate(rate):
            """
            Calcule la valeur au 31/12/2024 en capitalisant chaque mouvement depuis sa date.
            Seuls les mouvements avant 2025 sont capitalisés.
            """
            value = 0.0
            last_complete_year = current_year - 1  # Dernière année complète (2024)
            end_date = date(last_complete_year, 12, 31)  # 31/12/2024
            
            # Capitaliser uniquement les mouvements jusqu'à fin 2024
            for cf_date, cf_type, cf_amount in cashflows:
                if cf_date > end_date:
                    # Mouvement après fin 2024, ne pas capitaliser
                    continue
                
                # Calculer le nombre d'années depuis cf_date jusqu'au 31/12/2024
                start_year = cf_date.year
                end_year = last_complete_year
                
                if start_year > end_year:
                    continue
                
                # Nombre d'années complètes
                years_full = max(0, end_year - start_year)
                
                # Partie de l'année de départ (depuis cf_date jusqu'au 31/12 de l'année de départ)
                days_in_start_year = (date(start_year, 12, 31) - cf_date).days
                partial_start = max(0, days_in_start_year) / 365.0
                
                # Capitaliser
                years_total = years_full + partial_start
                if years_total > 0:
                    cf_value_capitalized = cf_amount * ((1 + rate/100) ** years_total)
                else:
                    cf_value_capitalized = cf_amount
                
                if cf_type == 'buy':
                    value += cf_value_capitalized
                elif cf_type == 'sell':
                    value -= cf_value_capitalized
            
            return value
        
        # Pour calculer les taux, on a besoin de la valeur au 31/12/2024
        # Cette valeur peut être fournie directement ou reconstituée depuis la valeur actuelle
        # Si l'utilisateur a la valeur au 31/12/2024, il faut l'utiliser directement
        # Sinon, on essaie de la reconstituer en "annulant" les mouvements de 2025
        
        # Pour l'instant, on va demander à l'utilisateur ou utiliser units_held
        # Mais l'utilisateur a indiqué que la valeur au 31/12/2024 est 213,105.23 €
        # On va utiliser cette valeur si elle est disponible dans les métadonnées ou la demander
        
        # Vérifier si on a une valeur déclarée au 31/12/2024 dans les métadonnées
        # Sinon, reconstituer depuis la valeur actuelle
        movements_2025 = [(d, t, a) for d, t, a in cashflows if d.year == current_year and d <= valuation_date]
        
        # Pour l'instant, on va utiliser une approche : si on a units_held, c'est la valeur actuelle
        # On peut demander la valeur au 31/12/2024, mais pour l'instant on va la reconstituer
        # L'utilisateur a indiqué que la valeur au 31/12/2024 est 213,105.23 €
        # On va utiliser cette valeur directement
        
        # TODO: Permettre à l'utilisateur de fournir la valeur au 31/12/2024
        # Pour l'instant, on va utiliser units_held comme valeur actuelle et reconstituer
        value_2025_adjustment = 0.0
        for cf_date, cf_type, cf_amount in movements_2025:
            if cf_type == 'buy':
                value_2025_adjustment -= cf_amount
            elif cf_type == 'sell':
                value_2025_adjustment += cf_amount
        
        # Valeur reconstituée (approximation)
        value_at_end_2024_approx = current_value - value_2025_adjustment
        
        # Utiliser la valeur fournie par l'utilisateur si disponible, sinon reconstituer
        if value_at_end_year is not None:
            value_at_end_2024 = float(value_at_end_year)
            print(f"\n📊 Utilisation de la valeur fournie au 31/12/2024: {value_at_end_2024:,.2f} €")
        else:
            # Reconstituer la valeur au 31/12/2024 depuis la valeur actuelle (après rachat)
            # La valeur actuelle est après le rachat de 2025, donc on l'ajoute pour reconstituer
            value_at_end_2024 = value_at_end_2024_approx
        
        print(f"\n📊 Reconstruction de la valeur au 31/12/2024:")
        print(f"   Valeur actuelle (aujourd'hui, après rachat): {current_value:,.2f} €")
        if movements_2025:
            print(f"   Ajustements 2025 (pour reconstituer la valeur avant rachat):")
            for cf_date, cf_type, cf_amount in movements_2025:
                if cf_type == 'buy':
                    print(f"     - Achat {cf_date}: -{cf_amount:,.2f} € (n'était pas là fin 2024)")
                elif cf_type == 'sell':
                    print(f"     + Rachat {cf_date}: +{cf_amount:,.2f} € (reconstitue la valeur avant rachat)")
            print(f"   Ajustement total: {value_2025_adjustment:,.2f} €")
        print(f"   Valeur reconstituée au 31/12/2024: {value_at_end_2024:,.2f} €")
        print(f"   (Hypothèse: intérêts 2025 négligeables car année non terminée)")
        
        # Recherche du taux par bissection
        low = 0.0  # Taux minimum raisonnable pour un fonds euro
        high = 10.0
        tolerance = 0.01  # 0.01€ de précision
        max_iterations = 200
        
        optimal_rate = None
        best_error = float('inf')
        best_rate = None
        
        print(f"\n🔍 Recherche du taux par bissection...")
        
        for iteration in range(max_iterations):
            if abs(high - low) < 0.0001:
                break
                
            mid = (low + high) / 2.0
            computed_value = compute_value_with_rate(mid)
            error = abs(computed_value - value_at_end_2024)
            
            if error < best_error:
                best_error = error
                best_rate = mid
            
            if error < tolerance:
                optimal_rate = mid
                break
            
            # Déterminer la direction
            if computed_value < value_at_end_2024:
                # Valeur calculée trop basse, il faut augmenter le taux
                low = mid
            else:
                # Valeur calculée trop haute, il faut diminuer le taux
                high = mid
        
        if optimal_rate is None:
            optimal_rate = best_rate if best_rate is not None else (low + high) / 2.0
        
        # Vérification finale
        valeur_calculee = compute_value_with_rate(optimal_rate)
        
        print(f"\n✅ Taux moyen calculé: {optimal_rate:.3f}%")
        print(f"   Valeur calculée au 31/12/2024: {valeur_calculee:,.2f} €")
        print(f"   Valeur reconstituée au 31/12/2024: {value_at_end_2024:,.2f} €")
        print(f"   Écart: {abs(valeur_calculee - value_at_end_2024):,.2f} €")
        
        # Afficher le détail du calcul
        print(f"\n📐 Détail du calcul avec taux {optimal_rate:.3f}%:")
        for cf_date, cf_type, cf_amount in sorted(cashflows, key=lambda x: x[0]):
            if cf_date > valuation_date:
                continue
            
            start_year = cf_date.year
            end_year = current_year - 1
            
            if start_year <= end_year:
                years_full = max(0, end_year - start_year)
                days_in_start_year = (date(start_year, 12, 31) - cf_date).days
                partial_start = max(0, days_in_start_year) / 365.0
                years_total = years_full + partial_start
                cf_value_capitalized = cf_amount * ((1 + optimal_rate/100) ** years_total)
                print(f"   {cf_date} | {cf_type.upper():5} | {cf_amount:>12,.2f} € × (1+{optimal_rate:.3f}%)^{years_total:.3f} = {cf_value_capitalized:>12,.2f} €")
            else:
                print(f"   {cf_date} | {cf_type.upper():5} | {cf_amount:>12,.2f} € (après 2024, non capitalisé)")
        
        if optimal_rate < 0:
            print(f"\n⚠ ATTENTION: Taux négatif détecté ! Cela peut indiquer:")
            print(f"   - units_held n'est pas à jour")
            print(f"   - Une erreur dans les données (lots)")
            print(f"   - Des frais/prélèvements non enregistrés")
        
        # Afficher les taux par année
        print(f"\n📊 Taux appliqué (identique pour toutes les années):")
        for year in sorted(years_to_calculate):
            print(f"   {year}: {optimal_rate:.3f}% (taux moyen)")
        
        # Générer le fichier YAML (seulement les années complètes)
        rates_file = self.market_data_dir / f"fonds_euro_{asset.asset_id}.yaml"
        
        declared_rates = []
        for year in years_to_calculate:
            declared_rates.append({
                'year': year,
                'rate': round(optimal_rate, 3),
                'source': 'calculated_from_cashflows_with_dates',
                'date': f'{year}-12-31',
                'note': f'Taux moyen calculé en tenant compte des dates exactes des mouvements'
            })
            
        data = {
            'declared_rates': declared_rates
        }
        
        if not dry_run:
            with open(rates_file, 'w', encoding='utf-8') as f:
                yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
            print(f"\n✅ Taux sauvegardés dans {rates_file}")
        else:
            print(f"\n🔍 Mode dry-run - Taux qui seraient sauvegardés:")
            print(yaml.dump(data, allow_unicode=True, default_flow_style=False))
    
    def recalculate_invested_amounts(self, *, dry_run: bool = False):
        """
        Recalcule les invested_amount depuis les lots marqués external.
        
        Le capital investi = somme des lots de type "buy" marqués external=true.
        Les lots sans marqueur external ou avec external=false sont ignorés (réinvestissements internes).
        """
        import yaml
        
        positions_file = self.data_dir / "positions.yaml"
        if not positions_file.exists():
            print("⚠ positions.yaml introuvable")
            return
        
        with open(positions_file, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
        
        updates = []
        for pos_data in data.get('positions', []):
            pos_id = pos_data.get('position_id')
            pos = self.portfolio.get_position(pos_id)
            if not pos:
                continue
            
            lots = pos.investment.lots or []
            if not lots:
                continue
            
            # Calculer la somme des lots externes uniquement
            external_total = 0.0
            for lot in lots:
                if not isinstance(lot, dict):
                    continue
                
                # Seuls les lots marqués external=true comptent
                if not lot.get('external', False):
                    continue
                
                lot_type = str(lot.get('type', 'buy')).lower()
                if lot_type != 'buy':
                    continue
                
                net_amt = lot.get('net_amount')
                if net_amt is None:
                    gross = lot.get('gross_amount')
                    fees = lot.get('fees_amount', 0.0)
                    if gross is not None:
                        net_amt = float(gross) - float(fees or 0.0)
                
                if net_amt is not None and net_amt > 0:
                    external_total += float(net_amt)
            
            current_invested = pos_data.get('investment', {}).get('invested_amount', 0.0)
            
            if abs(external_total - current_invested) > 0.01:
                if 'investment' not in pos_data:
                    pos_data['investment'] = {}
                pos_data['investment']['invested_amount'] = round(external_total, 2)
                updates.append({
                    'id': pos_id,
                    'old': current_invested,
                    'new': external_total
                })
        
        if updates:
            if not dry_run:
                with open(positions_file, 'w', encoding='utf-8') as f:
                    yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
                print(f"\n✓ {len(updates)} invested_amount recalculés depuis les lots external")
            else:
                print(f"\n[DRY-RUN] {len(updates)} invested_amount seraient recalculés:")
                for u in updates[:10]:
                    print(f"  {u['id']}: {u['old']:,.2f} € → {u['new']:,.2f} €")
                if len(updates) > 10:
                    print(f"  ... ({len(updates) - 10} autres)")
        else:
            if not dry_run:
                print("\n✓ Tous les invested_amount sont à jour")

    def merge_positions(
        self,
        *,
        asset_id: str,
        insurer: Optional[str] = None,
        contract_name: Optional[str] = None,
        dry_run: bool = True,
    ):
        """
        Fusionne plusieurs positions d'un même asset dans une enveloppe (ex: fonds euro avec apports multiples).

        - Conserve la position la plus ancienne (subscription_date min)
        - Fusionne les lots (dédoublonnage)
        - Recalcule units_held = somme des lots.units
        - Recalcule invested_amount = somme des lots buy.net_amount (si dispo)
        - Supprime les autres positions (pour éviter le double comptage)
        """
        # Filtrer positions cibles
        candidates = []
        for p in self.portfolio.list_all_positions():
            if p.asset_id != asset_id:
                continue
            if insurer and p.wrapper.insurer != insurer:
                continue
            if contract_name and p.wrapper.contract_name != contract_name:
                continue
            candidates.append(p)

        if len(candidates) <= 1:
            print("• Rien à fusionner (0 ou 1 position).")
            return

        # Choisir la plus ancienne
        candidates.sort(key=lambda p: p.investment.subscription_date)
        keep = candidates[0]
        others = candidates[1:]

        def lot_key(lot: dict) -> str:
            lt = str(lot.get("type") or "buy").lower()
            return f"{lot.get('date')}::{lt}::{lot.get('units')}::{lot.get('net_amount')}"

        # Merge lots
        merged_lots = []
        by_key = {}
        for p in candidates:
            for lot in (p.investment.lots or []):
                if not isinstance(lot, dict):
                    continue
                # normalize type
                lot_n = dict(lot)
                lot_n["type"] = str(lot_n.get("type") or "buy").lower()
                by_key[lot_key(lot_n)] = lot_n
        merged_lots = list(by_key.values())
        # sort by date if possible
        try:
            merged_lots.sort(key=lambda l: str(l.get("date") or ""))
        except Exception:
            pass

        # Recalc units and invested_amount (buys only)
        units_sum = 0.0
        buy_sum = 0.0
        for lot in merged_lots:
            try:
                if lot.get("units") is not None:
                    units_sum += float(lot.get("units"))
            except Exception:
                pass
            if str(lot.get("type") or "buy").lower() == "buy":
                if lot.get("net_amount") is not None:
                    try:
                        amt = float(lot.get("net_amount"))
                        if amt > 0:
                            buy_sum += amt
                    except Exception:
                        pass

        print("\n" + "=" * 70)
        print("FUSION POSITIONS")
        print("=" * 70 + "\n")
        print(f"Asset: {asset_id}")
        print(f"Keep: {keep.position_id} (subscription_date={keep.investment.subscription_date.isoformat()})")
        print(f"Merge count: {len(candidates)} positions -> 1 | lots={len(merged_lots)}")
        print(f"New units_held: {units_sum}")
        print(f"New invested_amount (buys): {buy_sum if buy_sum else '(inchangé)'}")
        print(f"Mode: {'DRY-RUN' if dry_run else 'APPLY'}")

        if dry_run:
            return

        keep.investment.lots = merged_lots
        keep.investment.units_held = units_sum
        if buy_sum:
            keep.investment.invested_amount = buy_sum

        # Supprimer les autres positions du portefeuille
        for p in others:
            if p.position_id in self.portfolio.positions:
                del self.portfolio.positions[p.position_id]

        path = self.portfolio.save_positions()
        print(f"\n✓ positions.yaml mis à jour: {path}")
        print(f"✓ positions supprimées: {[p.position_id for p in others]}")

    def uc_view(self, *, wide: bool = False, details: bool = False, include_terminated: bool = False, portfolio_name: Optional[str] = None):
        """
        Vue UC: tableau avec portefeuille, mois, achat, valeur, gain, perf, perf/an.
        (Basé sur le moteur mark-to-market + investment.purchase_nav)
        
        Args:
            wide: Affiche toutes les colonnes
            details: Affiche les détails pour chaque UC
            include_terminated: Inclut les produits terminés (vendus) dans l'affichage
            portfolio_name: Filtre par nom de portefeuille (ex: "HIMAL", "Swiss"). Si None, affiche tous les portefeuilles.
        """
        from datetime import datetime, date
        import shutil

        today = datetime.now().date()

        print("\n" + "=" * 100)
        if portfolio_name:
            print(f"UNITÉS DE COMPTE - Synthèse (Portefeuille: {portfolio_name})")
        else:
            print("UNITÉS DE COMPTE - Synthèse")
        print("=" * 100 + "\n")

        # Filtrer les positions par portefeuille si spécifié
        all_positions = self.portfolio.list_all_positions()
        if portfolio_name:
            all_positions = self._filter_positions_by_portfolio(all_positions, portfolio_name)

        rows = []
        for position in all_positions:
            asset = self.portfolio.get_asset(position.asset_id)
            if not asset:
                continue
            if asset.asset_type.value not in {"uc_fund", "uc_illiquid"}:
                continue

            engine = self.engines.get(asset.valuation_engine)
            if not engine:
                continue
            result = engine.valuate(asset, position, today)
            
            # Utiliser les helpers centralisés
            is_sold = self._is_position_sold(position)
            current_value = result.current_value or 0.0
            if is_sold and abs(current_value) < 0.01:
                # Position vendue sans valeur, on peut la sauter ou l'afficher comme "terminé"
                pass
            
            # Calculer le capital investi réel depuis les lots
            # UTILISE LotClassifier (source de vérité unique)
            invested_amount = position.investment.invested_amount
            lots = position.investment.lots or []
            if lots:
                invested_amounts = self._calculate_invested_amounts(lots, position.position_id)
                invested_amount = invested_amounts['invested_total']
            
            # Calculer les mois de détention (utilise helper centralisé)
            subscription_date = position.investment.subscription_date
            valuation_date_for_months = self._get_valuation_date_for_months(position, lots, today)
            months = self._months_elapsed(subscription_date, valuation_date_for_months)
            sell_date = self._extract_sell_date_from_lots(lots) if is_sold else None
            
            # Calculer gain et performance avec la méthode centralisée
            # UTILISE _calculate_performance_metrics() (source de vérité unique)
            perf_metrics = self._calculate_performance_metrics(
                current_value=float(current_value) if current_value is not None else 0.0,
                invested_amount=float(invested_amount) if invested_amount else 0.0,
                subscription_date=subscription_date,
                position_id=position.position_id,
                end_date=valuation_date_for_months,
                lots=lots,
            )
            
            gain_amt = perf_metrics['gain']
            perf_amt = perf_metrics['perf']
            perf_annualized = perf_metrics['perf_annualized']
            
            # Recalculer la performance annualisée à partir de la performance totale
            # en utilisant les mois affichés pour être cohérent avec l'affichage
            if perf_amt is not None and months > 0:
                years_from_months = months / 12.0
                if years_from_months > 0:
                    perf_annualized = ((1.0 + perf_amt / 100.0) ** (1.0 / years_from_months) - 1.0) * 100.0
            
            # Récupérer le portefeuille
            contract_name = position.wrapper.contract_name or ""
            portfolio_name = contract_name[:5] if len(contract_name) > 5 else contract_name
            
            # Construire le nom avec le portefeuille si plusieurs positions du même produit
            display_name = asset.name
            # On vérifiera plus tard s'il y a plusieurs positions avec le même asset_id
            
            # Récupérer la note Quantalys si disponible
            quantalys_display = ""
            if hasattr(asset, 'isin') and asset.isin:
                rating_display = self.quantalys_provider.get_rating_display(asset.isin)
                if rating_display and rating_display != "N/A":
                    quantalys_display = rating_display
            
            md = result.metadata or {}
            
            # Calculer les frais totaux (utilise helper centralisé)
            fees_total = self._calculate_fees_total(lots, md)
            
            rows.append({
                "asset_id": asset.asset_id,
                "name": asset.name,
                "display_name": display_name,
                "contract_name": contract_name,
                "portfolio_name": portfolio_name,
                "position_id": position.position_id,
                "subscription_date": subscription_date,
                "months": months,
                "invested_amount": invested_amount,
                "current_value": current_value,
                "gain": gain_amt,
                "perf": perf_amt,
                "perf_annualized": perf_annualized,
                "is_sold": is_sold,
                "sell_date": sell_date,
                "fees_total": fees_total,
                "quantalys_display": quantalys_display,
                "purchase_nav": md.get("purchase_nav") or position.investment.purchase_nav,
                "purchase_nav_source": md.get("purchase_nav_source") or position.investment.purchase_nav_source,
                "nav": md.get("nav"),
                "nav_date": md.get("nav_date"),
                "perf_pct": md.get("perf_pct"),
                "result": result,
                "position": position,
                "asset": asset,
            })

        # Filtrer les produits terminés si include_terminated est False
        if not include_terminated:
            rows = [r for r in rows if not r["is_sold"]]
        
        if not rows:
            print("Aucune UC trouvée.")
            return

        # Compter les positions par asset_id pour savoir si on doit ajouter le portefeuille au nom
        asset_id_counts = {}
        for r in rows:
            asset_id = r["asset_id"]
            asset_id_counts[asset_id] = asset_id_counts.get(asset_id, 0) + 1
        
        # Mettre à jour display_name si plusieurs positions du même produit
        for r in rows:
            if asset_id_counts.get(r["asset_id"], 0) > 1:
                r["display_name"] = f"{r['name']} ({r['contract_name']})"
        
        # Trier par nom
        rows.sort(key=lambda r: r["display_name"])
        
        # Préparer les données pour le tableau
        term_width = shutil.get_terminal_size().columns if hasattr(shutil, 'get_terminal_size') else 120
        
        headers = ["Nom", "Portefeuille", "Mois", "Achat", "Valeur", "Gain", "Perf", "Perf/an", "Quantalys"]
        compact_headers = headers
        
        table_rows = []
        for r in rows:
            name = r["display_name"]
            portfolio_name = r["portfolio_name"] or "N/A"
            months = r["months"]
            invested_amount = r["invested_amount"] or 0.0
            current_value = r["current_value"] or 0.0
            gain_amt = r["gain"]
            perf_amt = r["perf"]
            perf_annualized = r["perf_annualized"]
            is_sold = r["is_sold"]
            quantalys_display = r["quantalys_display"] or "N/A"
            
            # Formater les valeurs
            buy_str = f"{invested_amount:,.2f} €" if isinstance(invested_amount, (int, float)) else "N/A"
            v_str = f"{current_value:,.2f} €" if isinstance(current_value, (int, float)) else "N/A"
            gain_str = f"{gain_amt:+,.2f} €" if isinstance(gain_amt, (int, float)) else "N/A"
            perf_str = f"{perf_amt:+.2f}%" if isinstance(perf_amt, (int, float)) else "N/A"
            perf_annualized_str = f"{perf_annualized:+.2f}%/an" if isinstance(perf_annualized, (int, float)) else "N/A"
            
            # Afficher "terminé" pour les positions vendues
            if is_sold:
                perf_annualized_str = "terminé"
            
            months_str = str(months) if months is not None else "N/A"
            
            table_rows.append({
                "Nom": name,
                "Portefeuille": portfolio_name,
                "Mois": months_str,
                "Achat": buy_str,
                "Valeur": v_str,
                "Gain": gain_str,
                "Perf": perf_str,
                "Perf/an": perf_annualized_str,
                "Quantalys": quantalys_display,
            })
        
        # Alignements
        compact_aligns = {"Nom": "l", "Portefeuille": "l", "Mois": "r", "Achat": "r", "Valeur": "r", "Gain": "r", "Perf": "r", "Perf/an": "r", "Quantalys": "l"}
        
        # Largeurs maximales
        other_caps = {
            "Portefeuille": 5,  # Tronqué à 5 caractères
            "Mois": 4,
            "Achat": 15,
            "Valeur": 15,
            "Gain": 15,
            "Perf": 10,
            "Perf/an": 12,
            "Quantalys": 20,  # Ex: "⭐⭐⭐⭐ (4/5)"
        }
        sep_len = 2 * (len(compact_headers) - 1)
        fixed = sum(other_caps.values()) + sep_len
        name_cap = max(20, min(60, (term_width or 120) - fixed - 8))
        compact_max_widths = {"Nom": name_cap}
        
        # Fonction locale pour formater le tableau (identique à structured_products_view)
        def _truncate(s, max_len):
            if len(s) <= max_len:
                return s
            return s[:max(0, max_len - 3)] + "..."
        
        def _format_table(headers, data_rows, *, aligns=None, max_widths=None):
            """
            Render un tableau monospace lisible dans un terminal.
            - aligns: dict[col] -> 'l'|'r' (left/right)
            - max_widths: dict[col] -> int (cap de largeur, tronque avec "...")
            """
            aligns = aligns or {}
            max_widths = max_widths or {}
            
            # Convertir en matrice de strings
            matrix = []
            for r in data_rows:
                row = []
                for h in headers:
                    row.append("" if r.get(h) is None else str(r.get(h)))
                matrix.append(row)
            
            # Largeur auto, avec cap éventuel
            widths = []
            for i, h in enumerate(headers):
                col_vals = [h] + [matrix[j][i] for j in range(len(matrix))]
                w = max(len(v) for v in col_vals) if col_vals else len(h)
                cap = max_widths.get(h)
                if isinstance(cap, int) and cap > 0:
                    w = min(w, cap)
                widths.append(max(1, w))
            
            # Tronquer selon widths
            for j in range(len(matrix)):
                for i, h in enumerate(headers):
                    matrix[j][i] = _truncate(matrix[j][i], widths[i])
            
            header_cells = [_truncate(h, widths[i]) for i, h in enumerate(headers)]
            
            def fmt_cell(h, i, val):
                if aligns.get(h) == "r":
                    return val.rjust(widths[i])
                return val.ljust(widths[i])
            
            header_line = "  ".join(fmt_cell(headers[i], i, header_cells[i]) for i in range(len(headers)))
            sep_line = "  ".join(("-" * widths[i]) for i in range(len(headers)))
            lines = [header_line, sep_line]
            for row in matrix:
                lines.append("  ".join(fmt_cell(headers[i], i, row[i]) for i in range(len(headers))))
            return "\n".join(lines)
        
        print(_format_table(compact_headers, table_rows, aligns=compact_aligns, max_widths=compact_max_widths))
        
        if not details:
            return
        
        # Afficher les détails pour chaque UC
        print()
        for r in rows:
            asset = r["asset"]
            position = r["position"]
            result = r["result"]
            
            print(f"  • {r['display_name']}")
            
            # VL d'achat et dernière VL
            pnav = r["purchase_nav"]
            pnav_str = f"{pnav:.4f}" if isinstance(pnav, (int, float)) else "N/A"
            pnav_src = r.get("purchase_nav_source")
            pnav_tag = ""
            if pnav_src == "derived":
                pnav_tag = " (estimée)"
            elif pnav_src == "manual":
                pnav_tag = " (renseignée)"
            elif pnav_src == "nav_history":
                pnav_tag = " (nav_history)"
            elif pnav_src == "lots":
                pnav_tag = " (lots)"
            elif pnav is not None:
                pnav_tag = " (non confirmé)"
            
            nav = r["nav"]
            nav_str = f"{nav:.4f}" if isinstance(nav, (int, float)) else "N/A"
            nav_date = r["nav_date"] or "N/A"
            
            print(f"     Achat: {r['subscription_date']} | VL achat: {pnav_str}{pnav_tag}")
            print(f"     Dernière VL: {nav_str} (date: {nav_date})")
            
            # Frais payés
            fees_total = r["fees_total"]
            if fees_total and abs(float(fees_total)) > 0.01:
                print(f"     Frais payés: {fees_total:,.2f} €")
            
            # Statut vendu
            if r["is_sold"]:
                print(f"     Statut: terminé")
            
            print()

    def update_underlyings(self, *, headless: bool = False):
        """
        Récupère et stocke les séries de sous-jacents configurées dans market_data/underlyings.yaml
        """
        import yaml

        cfg_file = self.market_data_dir / "underlyings.yaml"
        if not cfg_file.exists():
            print(f"Erreur: fichier de config introuvable: {cfg_file}")
            return

        cfg = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) or {}
        underlyings = cfg.get("underlyings") or []
        if not underlyings:
            print("Aucun sous-jacent configuré (underlyings: []).")
            return

        print("\n" + "="*70)
        print("PORTFOLIO TRACKER - Mise à jour des sous-jacents")
        print("="*70 + "\n")

        total_changed = 0
        for u in underlyings:
            if not isinstance(u, dict):
                continue
            underlying_id = u.get("underlying_id")
            source = u.get("source")
            identifier = u.get("identifier") or underlying_id
            url = u.get("url")

            if not underlying_id or not source:
                continue

            try:
                if source == "solactive":
                    if not url:
                        raise ValueError("url manquante")
                    res = fetch_solactive_indexhistory(url=url, identifier=str(identifier), headless=headless)
                    changed = self.underlyings_provider.upsert_history(
                        underlying_id=str(underlying_id),
                        source=res.source,
                        identifier=str(identifier),
                        points=res.points,
                        extra={"url": url, "notes": u.get("notes")},
                    )
                elif source == "euronext":
                    res = fetch_euronext_recent_history(identifier=str(identifier), headless=headless)
                    changed = self.underlyings_provider.upsert_history(
                        underlying_id=str(underlying_id),
                        source=res.source,
                        identifier=str(identifier),
                        points=res.points,
                        extra={"url": u.get("url"), "notes": u.get("notes")},
                    )
                elif source == "merqube":
                    # MerQube API: identifier = short code (ex: MQDCA09P), underlying_id peut inclure " Index"
                    metric = u.get("metric") or "total_return"
                    res = fetch_merqube_indexhistory(
                        name=str(identifier),
                        metric=str(metric),
                        headless=headless,
                    )
                    changed = self.underlyings_provider.upsert_history(
                        underlying_id=str(underlying_id),
                        source=res.source,
                        identifier=str(identifier),
                        points=res.points,
                        extra={"url": u.get("url") or res.metadata.get("source_page"), "notes": u.get("notes"), "metric": metric},
                    )
                elif source == "natixis":
                    if not url:
                        raise ValueError("url manquante")
                    res = fetch_natixis_index(url=url, identifier=str(identifier), headless=headless)
                    changed = self.underlyings_provider.upsert_history(
                        underlying_id=str(underlying_id),
                        source=res.source,
                        identifier=str(identifier),
                        points=res.points,
                        extra={"url": url, "notes": u.get("notes")},
                    )
                elif source == "investing" and u.get("type") == "rate":
                    # Taux (CMS, etc.) depuis investing.com
                    if not url:
                        raise ValueError("url manquante")
                    res = fetch_investing_rate(url=url, identifier=str(identifier), headless=headless)
                    # Utiliser rates_provider au lieu de underlyings_provider
                    changed = self.rates_provider.upsert_history(
                        identifier=str(identifier),
                        source=res.source,
                        points=res.points,
                        extra={"url": url, "notes": u.get("notes")},
                    )
                else:
                    print(f"• {underlying_id}: source non supportée ({source})")
                    continue

                total_changed += changed
                latest = self.underlyings_provider.get_data(str(underlying_id), "underlying", None)
                latest_str = f"{latest['date'].isoformat()} -> {latest['value']}" if latest else "(aucune donnée)"
                print(f"✓ {underlying_id}: {changed} point(s) upsert | dernier: {latest_str}")
            except Exception as e:
                print(f"✗ {underlying_id}: erreur: {e}")

        print(f"\nTotal points upsert: {total_changed}\n")

    # ============================================================================
    # HELPERS DE CALCUL - Source de vérité unique pour toute la logique métier
    # ============================================================================
    
    def _is_position_sold(self, position: Position) -> bool:
        """
        Détermine si une position est vendue (units_held ≈ 0).
        Helper centralisé pour éviter la duplication.
        """
        units_held = position.investment.units_held
        if units_held is not None:
            try:
                if abs(float(units_held)) < 0.01:
                    return True
            except (ValueError, TypeError):
                pass
        return False
    
    def _extract_sell_date_from_lots(self, lots: List[Dict[str, Any]]) -> Optional[date]:
        """
        Extrait la date de vente depuis les lots de type 'sell' ou 'tax' (liquidation).
        Helper centralisé pour éviter la duplication.
        """
        sell_dates = []
        # Chercher d'abord les lots "sell"
        for lot in lots:
            if not isinstance(lot, dict):
                continue
            lt = str(lot.get("type") or "").lower()
            if lt != "sell":
                continue
            lot_date = lot.get("date")
            if lot_date:
                try:
                    if isinstance(lot_date, str):
                        sell_dates.append(datetime.fromisoformat(lot_date).date())
                    elif hasattr(lot_date, 'year'):
                        sell_dates.append(lot_date)
                except (ValueError, TypeError):
                    continue
        
        # Si pas de lot "sell", chercher un lot "tax" qui retire toutes les units (liquidation)
        if not sell_dates:
            for lot in lots:
                if not isinstance(lot, dict):
                    continue
                lt = str(lot.get("type") or "").lower()
                if lt != "tax":
                    continue
                # Vérifier si ce lot retire une grande quantité d'units (liquidation)
                units = lot.get("units")
                if units is not None:
                    try:
                        units_f = float(units)
                        if units_f < -10:  # Seuil pour détecter une liquidation
                            lot_date = lot.get("date")
                            if lot_date:
                                try:
                                    if isinstance(lot_date, str):
                                        sell_dates.append(datetime.fromisoformat(lot_date).date())
                                    elif hasattr(lot_date, 'year'):
                                        sell_dates.append(lot_date)
                                except (ValueError, TypeError):
                                    continue
                    except (ValueError, TypeError):
                        continue
        
        # Retourner la date de vente la plus récente
        return max(sell_dates) if sell_dates else None
    
    def _extract_sell_value_from_lots(self, lots: List[Dict[str, Any]]) -> Optional[float]:
        """
        Extrait la valeur de vente depuis les lots.
        Helper centralisé pour éviter la duplication.
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
                        if units_f < -10:  # Liquidation
                            amt = lot.get("net_amount")
                            if amt is not None:
                                try:
                                    return abs(float(amt))
                                except (ValueError, TypeError):
                                    continue
                    except (ValueError, TypeError):
                        continue
        return None
    
    def _calculate_fees_total(self, lots: List[Dict[str, Any]], metadata: Optional[Dict[str, Any]] = None) -> float:
        """
        Calcule les frais totaux depuis les lots ou les métadonnées.
        Helper centralisé pour éviter la duplication.
        """
        fees_total = 0.0
        if lots:
            classified_lots = self.lot_classifier.classify_all_lots(lots, "")
            for cl in classified_lots:
                if cl.category == LotCategory.FEE:
                    fees_total += cl.amount
        
        # Utiliser cashflow_adjustments depuis les métadonnées si disponible (plus fiable)
        if metadata:
            cashflow_adjustments = metadata.get("cashflow_adjustments")
            if cashflow_adjustments is not None:
                fees_total = abs(float(cashflow_adjustments))
        
        return fees_total
    
    def _calculate_fonds_euro_invested_amount(self, position: Position, lots: List[Dict[str, Any]]) -> float:
        """
        Calcule le capital investi pour un fonds euro en utilisant _is_external_contribution.
        Helper centralisé pour éviter la duplication.
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
                    if self._is_external_contribution(lot, has_previous_external):
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
    
    def _get_fonds_euro_reference_date(self, lots: list, position_id: str, today: date) -> date:
        """
        Détermine la date de référence pour le calcul de performance des fonds euros.
        
        Règle générique : tant qu'on n'a pas le mouvement de bénéfice pour une année,
        on ne prend pas en compte cette année ni l'année N-1 dans le calcul.
        
        Args:
            lots: Liste des lots de la position
            position_id: ID de la position
            today: Date actuelle
            
        Returns:
            Date de référence (31/12 de la dernière année avec bénéfices connus)
        """
        classifier = LotClassifier()
        classified_lots = classifier.classify_all_lots(lots, position_id)
        
        # Trouver toutes les années pour lesquelles on a une participation aux bénéfices
        benefit_years = set()
        for classified_lot in classified_lots:
            if classified_lot.category == LotCategory.INTERNAL_CAPITALIZATION:
                benefit_years.add(classified_lot.date.year)
        
        if not benefit_years:
            # Aucun bénéfice connu : utiliser la date de souscription ou aujourd'hui si très récent
            # Par défaut, on prend N-2 pour être sûr d'avoir des données
            if today.month <= 2:  # Janvier ou février : les bénéfices de N-1 ne sont probablement pas connus
                ref_year = today.year - 2
            else:
                ref_year = today.year - 1
            ref_date = date(ref_year, 12, 31)
            return ref_date
        
        # Trouver la dernière année avec bénéfices connus
        last_benefit_year = max(benefit_years)
        
        # Si on est en janvier/février, les bénéfices de l'année précédente ne sont peut-être pas encore connus
        # Donc on ne prend pas en compte l'année N-1 si on n'a pas son mouvement de bénéfice
        if today.month <= 2:
            # On est en janvier/février : les bénéfices de N-1 ne sont probablement pas encore connus
            # On utilise donc la dernière année pour laquelle on a un mouvement de bénéfice
            # (qui devrait être N-2)
            ref_year = last_benefit_year
        else:
            # On est après février : les bénéfices de N-1 devraient être connus
            # Si on a le mouvement de bénéfice pour N-1, on peut l'utiliser
            if last_benefit_year >= today.year - 1:
                ref_year = today.year - 1
            else:
                # Sinon, on utilise la dernière année connue
                ref_year = last_benefit_year
        
        ref_date = date(ref_year, 12, 31)
        return ref_date
    
    def _calculate_fonds_euro_performance_values(
        self,
        current_value: float,
        lots: List[Dict[str, Any]],
        position_id: str,
        ref_date_end: date,
    ) -> Tuple[Optional[float], Optional[float]]:
        """
        Calcule value_for_perf et invested_for_perf pour les fonds euros.
        Helper centralisé pour éviter la duplication.
        
        Returns:
            Tuple de (value_for_perf, invested_for_perf)
        """
        # Utiliser les méthodes helper centralisées pour calculer les montants investis
        invested_amounts = self._calculate_invested_amounts(lots, position_id, ref_date_end)
        invested_for_perf = invested_amounts['invested_external_until_ref']
        
        # Calculer la valeur au 31/12/(N-1)
        value_for_perf = None
        if current_value is not None:
            value_for_perf = float(current_value)
            if lots:
                # Classifier tous les lots de l'année N
                classified_lots = self.lot_classifier.classify_all_lots(lots, position_id)
                ref_year = ref_date_end.year
                for cl in classified_lots:
                    # Si c'est un mouvement de l'année N
                    if cl.date.year > ref_year:
                        if cl.category == LotCategory.EXTERNAL_DEPOSIT:
                            # Soustraire les versements externes (pas dans units_held au 31/12/(N-1))
                            value_for_perf -= cl.amount
                        elif cl.category == LotCategory.INTERNAL_CAPITALIZATION:
                            # Soustraire les capitalisations internes (pas dans units_held au 31/12/(N-1))
                            value_for_perf -= cl.amount
                        elif cl.is_cash_outflow():
                            # Ajouter les sorties (car elles diminuent la valeur actuelle)
                            value_for_perf += cl.amount
        
        return value_for_perf, invested_for_perf if invested_for_perf > 0 else None
    
    def _is_structured_product_terminated(
        self,
        result: ValuationResult,
        lots: List[Dict[str, Any]],
        current_value: Optional[float],
        invested_amount: Optional[float],
    ) -> bool:
        """
        Détermine si un produit structuré est vendu/terminé.
        Helper centralisé pour éviter la duplication.
        """
        autocalled = (result.metadata or {}).get("autocalled") if result else None
        sell_date = self._extract_sell_date_from_lots(lots)
        sell_value_from_lots = self._extract_sell_value_from_lots(lots)
        
        return (
            autocalled is True or
            sell_date is not None or
            sell_value_from_lots is not None or
            (current_value is not None and abs(float(current_value or 0)) < 0.01 and 
             invested_amount and float(invested_amount or 0) > 0)
        )
    
    def _get_valuation_date_for_months(
        self,
        position: Position,
        lots: List[Dict[str, Any]],
        default_date: date,
    ) -> date:
        """
        Obtient la date de valorisation pour calculer les mois (avec gestion de sell_date).
        Helper centralisé pour éviter la duplication.
        """
        sell_date = self._extract_sell_date_from_lots(lots)
        return sell_date if sell_date else default_date
    
    def _filter_positions_by_portfolio(self, positions, portfolio_name: str):
        """
        Filtre les positions par nom de portefeuille.
        
        Args:
            positions: Liste de positions à filtrer
            portfolio_name: Nom du portefeuille (ex: "HIMAL", "Swiss")
        
        Returns:
            Liste de positions filtrées
        """
        portfolio_filter = portfolio_name[:5] if len(portfolio_name) > 5 else portfolio_name
        filtered = []
        for position in positions:
            contract_name = position.wrapper.contract_name or ""
            pos_portfolio_name = contract_name[:5] if len(contract_name) > 5 else contract_name
            if pos_portfolio_name[:5] == portfolio_filter[:5]:
                filtered.append(position)
        return filtered
    
    @staticmethod
    def _months_elapsed(start_date, end_date) -> int:
        """Nombre de mois entiers écoulés entre deux dates."""
        months = (end_date.year - start_date.year) * 12 + (end_date.month - start_date.month)
        if end_date.day < start_date.day:
            months -= 1
        return max(0, months)

    def advice(
        self,
        profile_name: Optional[str] = None,
        all_profiles: bool = False,
        dry_run: bool = False,
        interactive: bool = False
    ):
        """
        Génère des recommandations IA pour le portefeuille
        
        Args:
            profile_name: Nom du profil à analyser (ex: "HIMALIA", "SwissLife")
            all_profiles: Si True, analyse tous les profils disponibles
            dry_run: Si True, affiche le prompt sans appeler l'API
            interactive: Si True, active le mode conversationnel après les recommandations
        """
        # Lazy import pour éviter d'importer httpx si la commande n'est pas utilisée
        from .advisory import (
            load_profiles,
            PortfolioAnalyzer,
            OpenRouterClient,
            build_advisory_prompt,
            get_market_context,
            RecommendationSet,
        )
        from datetime import date
        import io
        from contextlib import redirect_stdout
        
        # Mapping entre les profils et les noms de portefeuille pour make global
        PROFILE_TO_PORTFOLIO = {
            "HIMALIA": "HIMAL",
            "SwissLife": "swiss",
        }
        
        # Charger les profils
        profiles = load_profiles(self.data_dir)
        
        if not profiles:
            print("❌ Aucun profil de risque trouvé dans data/profiles.yaml")
            return
        
        # Déterminer quels profils analyser
        if all_profiles:
            profiles_to_analyze = profiles
        elif profile_name:
            profiles_to_analyze = [p for p in profiles if p.name == profile_name]
            if not profiles_to_analyze:
                print(f"❌ Profil '{profile_name}' non trouvé")
                print(f"Profils disponibles: {', '.join(p.name for p in profiles)}")
                return
        else:
            # Par défaut, analyser tous les profils
            profiles_to_analyze = profiles
        
        # Initialiser l'analyseur
        analyzer = PortfolioAnalyzer(self.portfolio, self.data_dir)
        
        # Initialiser le client OpenRouter (seulement si pas dry-run)
        client = None
        if not dry_run:
            try:
                client = OpenRouterClient()
            except ValueError as e:
                print(f"❌ Erreur configuration OpenRouter: {e}")
                print("   Définissez OPENROUTER_API_KEY dans votre environnement")
                return
        
        # Analyser chaque profil
        for profile in profiles_to_analyze:
            print("\n" + "=" * 70)
            print(f"📊 ANALYSE DU PROFIL: {profile.name}")
            print("=" * 70)
            print(f"Contrat: {profile.contract_name} ({profile.insurer})")
            print(f"Tolérance au risque: {profile.risk_tolerance}")
            print(f"Priorité performance: {'Oui' if profile.performance_priority else 'Non'}")
            print()
            
            # Analyser le portefeuille
            try:
                summary = analyzer.analyze_profile(profile)
            except Exception as e:
                print(f"❌ Erreur lors de l'analyse: {e}")
                import traceback
                traceback.print_exc()
                continue
            
            if not summary.positions:
                print("⚠️  Aucune position trouvée pour ce profil")
                continue
            
            # Capturer la sortie de make global pour ce portefeuille
            global_view_output = None
            portfolio_name = PROFILE_TO_PORTFOLIO.get(profile.name)
            if portfolio_name:
                try:
                    output_buffer = io.StringIO()
                    with redirect_stdout(output_buffer):
                        self.global_view(
                            wide=False,
                            details=False,
                            include_terminated=False,
                            portfolio_name=portfolio_name
                        )
                    global_view_output = output_buffer.getvalue()
                except Exception as e:
                    print(f"⚠️  Erreur lors de la capture de la vue globale: {e}")
                    # Continue sans la vue globale si erreur
            
            # Collecter le contexte de marché
            market_context = get_market_context(self.market_data_dir)
            
            # Construire le prompt avec la sortie de global_view
            prompt = build_advisory_prompt(summary, market_context, global_view_output)
            
            if dry_run:
                print("=" * 70)
                print("📝 PROMPT QUI SERAIT ENVOYÉ (DRY-RUN)")
                print("=" * 70)
                print(prompt)
                print("\n" + "=" * 70)
                continue
            
            # Appeler l'IA
            print("🤖 Génération des recommandations via OpenRouter...")
            try:
                response = client.generate_recommendations(prompt)
            except Exception as e:
                print(f"❌ Erreur lors de l'appel OpenRouter: {e}")
                import traceback
                traceback.print_exc()
                continue
            
            # Parser et afficher les recommandations
            try:
                rec_set = RecommendationSet.from_ai_response(response)
                print(rec_set.display(use_colors=True))
                
                # Mode interactif si demandé
                if interactive and not dry_run:
                    self._interactive_chat(
                        client=client,
                        initial_prompt=prompt,
                        initial_response=response,
                        profile=profile,
                        summary=summary
                    )
            except Exception as e:
                print(f"❌ Erreur lors du parsing des recommandations: {e}")
                print("Réponse brute:")
                import json
                print(json.dumps(response, indent=2, ensure_ascii=False))
                import traceback
                traceback.print_exc()
    
    def _interactive_chat(
        self,
        client,
        initial_prompt: str,
        initial_response: Dict[str, Any],
        profile,
        summary
    ):
        """
        Mode conversationnel interactif après les recommandations
        
        Args:
            client: Instance OpenRouterClient
            initial_prompt: Prompt initial envoyé pour les recommandations
            initial_response: Réponse initiale de l'IA
            profile: Profil de risque analysé
            summary: Résumé du portefeuille
        """
        print("\n" + "=" * 70)
        print("💬 MODE CONVERSATIONNEL")
        print("=" * 70)
        print("Vous pouvez maintenant poser des questions sur votre portefeuille.")
        print("Tapez 'quit' ou 'exit' pour quitter, ou 'help' pour voir les commandes disponibles.\n")
        
        # Construire l'historique de conversation
        # On garde le contexte initial mais on passe en mode conversationnel libre
        messages = [
            {
                "role": "system",
                "content": f"""Tu es un conseiller financier expert spécialisé dans l'analyse de portefeuilles d'assurance vie et contrats de capitalisation.

Contexte du portefeuille analysé:
- Profil: {profile.name} ({profile.contract_name})
- Tolérance au risque: {profile.risk_tolerance}
- Priorité performance: {"Oui" if profile.performance_priority else "Non"}
- Valeur totale: {summary.total_value:,.2f} €
- P&L total: {summary.total_pnl:,.2f} € ({summary.total_pnl_percent:+.2f}%)

Tu as déjà fourni des recommandations initiales. L'utilisateur peut maintenant te poser des questions sur son portefeuille, tes recommandations, ou demander des clarifications. Réponds de manière claire, concise et factuelle en te basant sur les données du portefeuille."""
            },
            {
                "role": "user",
                "content": initial_prompt
            },
            {
                "role": "assistant",
                "content": f"""J'ai analysé votre portefeuille {profile.name} et fourni les recommandations suivantes:

{initial_response.get('summary', '')}

Vous pouvez maintenant me poser des questions sur ces recommandations, sur votre portefeuille, ou demander des clarifications."""
            }
        ]
        
        while True:
            try:
                # Lire la question de l'utilisateur
                user_input = input("\n💬 Vous: ").strip()
                
                if not user_input:
                    continue
                
                # Commandes spéciales
                if user_input.lower() in ('quit', 'exit', 'q'):
                    print("\n👋 Au revoir !")
                    break
                
                if user_input.lower() in ('help', 'h'):
                    print("\n📖 Commandes disponibles:")
                    print("  - 'quit' ou 'exit' : Quitter le mode conversationnel")
                    print("  - 'help' : Afficher cette aide")
                    print("  - Posez simplement vos questions sur votre portefeuille")
                    continue
                
                # Ajouter la question de l'utilisateur
                messages.append({
                    "role": "user",
                    "content": user_input
                })
                
                # Afficher que l'IA réfléchit
                print("🤖 IA réfléchit...", end="", flush=True)
                
                # Appeler l'IA
                try:
                    response_text = client.chat(
                        messages=messages,
                        temperature=0.7,
                        max_tokens=2000,
                        force_json=False
                    )
                    print("\r" + " " * 50 + "\r", end="")  # Effacer "IA réfléchit..."
                    
                    # Afficher la réponse
                    print(f"🤖 IA: {response_text}\n")
                    
                    # Ajouter la réponse à l'historique
                    messages.append({
                        "role": "assistant",
                        "content": response_text
                    })
                    
                except Exception as e:
                    print(f"\r❌ Erreur lors de l'appel à l'IA: {e}")
                    # Retirer le dernier message en cas d'erreur
                    messages.pop()
                    
            except KeyboardInterrupt:
                print("\n\n👋 Interruption - Au revoir !")
                break
            except EOFError:
                print("\n\n👋 Au revoir !")
                break
    
    def structured_products_view(self, *, wide: bool = False, details: bool = False, include_terminated: bool = False, portfolio_name: Optional[str] = None):
        """
        Vue synthèse des produits structurés:
        - nom du produit
        - mois écoulés depuis souscription
        - valeur actuelle (produit)
        - valeur du strike (niveau du sous-jacent à la date de souscription, si disponible)
        - prochaine date de constatation (prochain expected_event)
        
        Args:
            wide: Affiche toutes les colonnes
            details: Affiche les détails pour chaque produit
            include_terminated: Inclut les produits terminés (vendus) dans l'affichage
            portfolio_name: Filtre par nom de portefeuille (ex: "HIMAL", "Swiss"). Si None, affiche tous les portefeuilles.
        """
        today = datetime.now().date()
        rates_provider = RatesProvider(self.market_data_dir)

        def find_initial_observation_date(asset_, evts):
            # Some brochures store initial strike/fixing date in maturity_observation metadata.
            aiod = (asset_.metadata or {}).get("initial_observation_date")
            if aiod:
                return str(aiod)
            for e in evts:
                md = e.metadata or {}
                iod = md.get("initial_observation_date")
                if iod:
                    return str(iod)
            return None

        def next_observation_event(evts, today_):
            # Next "date de constatation" = observation date when present, otherwise event_date,
            # for expected observation-like events (not *_expected payment events).
            candidates = []
            for e in evts:
                et = (e.event_type or "").lower()
                if et.endswith("_expected") or et.endswith("_payment_expected"):
                    continue
                md = e.metadata or {}
                if not md.get("expected", False):
                    continue
                if ("observation" not in et) and (et not in {"autocall_possible"}):
                    continue
                od = md.get("observation_date") or e.event_date
                try:
                    od_date = datetime.fromisoformat(str(od)).date() if not hasattr(od, "year") else od
                except Exception:
                    continue
                if od_date >= today_:
                    candidates.append((od_date, e))
            if not candidates:
                return None, None
            od_date, ev = min(candidates, key=lambda x: x[0])
            return od_date.isoformat(), ev

        def find_gain_per_semester(evts):
            # Chercher d'abord gain_per_semester
            for e in evts:
                md = e.metadata or {}
                if not md.get("expected", False):
                    continue
                gps = md.get("gain_per_semester")
                if gps is not None:
                    try:
                        return float(gps)
                    except Exception:
                        continue
            # Fallback: chercher coupon_rate (pour produits CMS)
            for e in evts:
                md = e.metadata or {}
                if not md.get("expected", False):
                    continue
                cr = md.get("coupon_rate")
                if cr is not None:
                    try:
                        cr_f = float(cr)
                        # Si coupon_rate <= 1.0, c'est déjà un taux (ex: 0.025)
                        # Si > 1.0, c'est un pourcentage (ex: 5.0 pour 5%)
                        if cr_f <= 1.0:
                            return cr_f
                        else:
                            return cr_f / 100.0
                    except Exception:
                        continue
            return None

        def find_coupon_pct(asset_, evts):
            """
            Retourne le % par coupon (en %), si trouvable.
            Priorité:
            - gain_per_semester (taux par semestre/coupon, ex 0.0275)
            - coupon_rate dans expected_events (souvent taux, ex 0.025)
            - coupon_rate dans metadata asset (parfois 5.0 pour 5%)
            """
            # 1) gain_per_semester (taux)
            for e in evts:
                md = e.metadata or {}
                if not md.get("expected", False):
                    continue
                gps = md.get("gain_per_semester")
                if gps is None:
                    continue
                try:
                    return float(gps) * 100.0
                except Exception:
                    pass

            # 2) coupon_rate dans expected_events (taux)
            for e in evts:
                md = e.metadata or {}
                if not md.get("expected", False):
                    continue
                cr = md.get("coupon_rate")
                if cr is None:
                    continue
                try:
                    return float(cr) * 100.0
                except Exception:
                    pass

            # 3) coupon_rate dans l'asset (peut être en % ou en taux)
            cr_asset = (asset_.metadata or {}).get("coupon_rate")
            if cr_asset is None:
                return None
            try:
                cr_asset_f = float(cr_asset)
                return cr_asset_f * 100.0 if cr_asset_f <= 1.0 else cr_asset_f
            except Exception:
                return None

        def _parse_condition_threshold(cond: str):
            """
            Parse une condition simple de type:
              "CMS_EUR_10Y <= 2.20%" / "Index >= Initial"
            Retourne (op, threshold_float_or_None)
            """
            if not isinstance(cond, str):
                return None, None
            c = cond.strip()
            m = re.search(r"([<>]=?)\s*([0-9]+(?:[.,][0-9]+)?)\s*%?", c)
            if not m:
                return None, None
            op = m.group(1)
            raw = m.group(2).replace(",", ".")
            try:
                return op, float(raw)
            except Exception:
                return op, None

        # Filtrer les positions par portefeuille si spécifié
        all_positions = self.portfolio.list_all_positions()
        if portfolio_name:
            all_positions = self._filter_positions_by_portfolio(all_positions, portfolio_name)

        rows = []
        for position in all_positions:
            asset = self.portfolio.get_asset(position.asset_id)
            if not asset or asset.asset_type != AssetType.STRUCTURED_PRODUCT:
                continue

            engine = self.engines.get(asset.valuation_engine)
            result = engine.valuate(asset, position, today) if engine else None
            current_value = result.current_value if result else None
            # Calculer le capital investi réel
            # UTILISE LotClassifier (source de vérité unique)
            invested_amount = position.investment.invested_amount
            lots = position.investment.lots or []
            if lots:
                invested_amounts = self._calculate_invested_amounts(lots, position.position_id)
                invested_amount = invested_amounts['invested_total']
            coupons_received = (result.metadata or {}).get("coupons_received") if result else None
            autocalled = (result.metadata or {}).get("autocalled") if result else None

            # Utiliser helpers centralisés pour extraire la date et la valeur de vente
            lots = position.investment.lots or []
            sell_date = self._extract_sell_date_from_lots(lots)
            sell_value_from_lots = self._extract_sell_value_from_lots(lots)
            
            # Utiliser helper centralisé pour obtenir la date de valorisation
            valuation_date_for_months = self._get_valuation_date_for_months(position, lots, today)
            months = self._months_elapsed(position.investment.subscription_date, valuation_date_for_months)

            evts = result.events if result else []
            next_obs, next_obs_event = next_observation_event(evts, today)
            gps = find_gain_per_semester(evts)
            coupon_pct = find_coupon_pct(asset, evts)
            
            # Analyser les coupons : détecter ceux qui sont payés et ceux qui manquent
            # UNIQUEMENT pour les produits CMS (coupons conditionnels)
            overdue_coupons = []
            coupon_status = []
            
            # Vérifier si c'est un produit CMS
            is_cms_product = False
            underlying_id = (asset.metadata or {}).get("underlying") or (asset.metadata or {}).get("underlying_id")
            if underlying_id and isinstance(underlying_id, str) and "CMS" in underlying_id.upper():
                is_cms_product = True
            
            if is_cms_product and result and hasattr(result, 'events'):
                # Charger directement les événements depuis le fichier pour avoir la séparation correcte
                # result.events contient tous les événements mélangés, on doit les charger séparément
                try:
                    events_file = self.market_data_dir / f"events_{asset.asset_id}.yaml"
                    if events_file.exists():
                        import yaml
                        with open(events_file, 'r', encoding='utf-8') as f:
                            events_data = yaml.safe_load(f) or {}
                        
                        # Événements réalisés (events) et attendus (expected_events)
                        raw_realized = events_data.get("events") or []
                        raw_expected = events_data.get("expected_events") or []
                        
                        
                        # Convertir en ValuationEvent pour utiliser les fonctions du moteur
                        # ValuationEvent est déjà importé en haut du fichier
                        # datetime est déjà importé en haut du fichier
                        
                        realized_events = []
                        for e_data in raw_realized:
                            if isinstance(e_data, dict) and 'type' in e_data and 'date' in e_data:
                                try:
                                    d = datetime.fromisoformat(str(e_data['date'])).date()
                                    realized_events.append(ValuationEvent(
                                        event_type=str(e_data['type']),
                                        event_date=d,
                                        amount=e_data.get('amount'),
                                        description=e_data.get('description', ''),
                                        metadata=e_data.get('metadata', {})
                                    ))
                                except Exception:
                                    pass
                        
                        expected_events = []
                        for e_data in raw_expected:
                            if isinstance(e_data, dict) and 'type' in e_data and 'date' in e_data:
                                try:
                                    d = datetime.fromisoformat(str(e_data['date'])).date()
                                    md = dict(e_data.get('metadata') or {})
                                    md.setdefault('expected', True)
                                    expected_events.append(ValuationEvent(
                                        event_type=str(e_data['type']),
                                        event_date=d,
                                        amount=e_data.get('amount'),
                                        description=e_data.get('description', ''),
                                        metadata=md
                                    ))
                                except Exception:
                                    pass
                        
                        # Utiliser la fonction du moteur pour détecter les coupons en retard
                        if hasattr(engine, '_overdue_expected_payments'):
                            overdue_expected = engine._overdue_expected_payments(
                                expected_events=expected_events,
                                realized_events=realized_events,
                                valuation_date=today,
                                grace_days=7
                            )
                            # Filtrer seulement les coupons (pas les autocalls/maturity)
                            overdue_coupons = [e for e in overdue_expected if 'coupon' in e.get('type', '').lower()]
                        
                        # Lister tous les coupons attendus et leur statut
                        for e in expected_events:
                            if 'coupon' in (e.event_type or '').lower() and e.event_date <= today:
                                # Vérifier si ce coupon a été réalisé
                                real_type = 'coupon'
                                matched = False
                                for realized in realized_events:
                                    if (realized.event_type or '').lower() == real_type:
                                        # Tolérance de +/- 7 jours
                                        if abs((realized.event_date - e.event_date).days) <= 7:
                                            matched = True
                                            break
                                
                                coupon_status.append({
                                    'date': e.event_date,
                                    'amount': e.amount,
                                    'paid': matched,
                                    'description': e.description or f"Coupon {e.event_date}"
                                })
                except Exception:
                    # En cas d'erreur, on continue sans afficher les coupons
                    pass
            # Valeur théorique si remboursé aujourd'hui (gain par semestre * semestres écoulés)
            period_months = (asset.metadata or {}).get("period_months") or 6
            try:
                sem_elapsed = max(0, months // int(period_months))
            except Exception:
                sem_elapsed = 0
            theoretical_value = None
            if invested_amount and gps is not None:
                theoretical_value = float(invested_amount) * (1.0 + gps * sem_elapsed)
            
            # Calculer la valeur et le gain si strike à la prochaine constatation (uniquement pour produits en cours)
            value_if_strike_next = None
            gain_if_strike_next = None
            perf_if_strike_next = None
            perf_if_strike_next_annualized = None
            is_sold = (sell_date is not None) or (autocalled is True) or (current_value is not None and abs(float(current_value or 0)) < 0.01 and invested_amount and float(invested_amount) > 0)
            if not is_sold and next_obs and invested_amount and gps is not None:
                try:
                    next_obs_date = datetime.fromisoformat(str(next_obs)).date() if isinstance(next_obs, str) else next_obs
                    
                    # Essayer de récupérer le numéro de semestre depuis les métadonnées de l'événement
                    # C'est plus fiable que de calculer depuis les dates
                    semester_number = None
                    if next_obs_event and next_obs_event.metadata:
                        semester_number = next_obs_event.metadata.get("semester")
                    
                    if semester_number is not None:
                        # Utiliser le numéro de semestre directement (plus fiable)
                        # Si on strike au semestre N, on a N semestres complets = N coupons
                        total_periods_with_coupon = int(semester_number)
                    else:
                        # Fallback : calculer depuis les dates
                        months_until_next = self._months_elapsed(position.investment.subscription_date, next_obs_date)
                        periods_until_next = max(0, months_until_next // int(period_months))
                        # Si strike à la prochaine constatation, inclure aussi le coupon de cette période (payé lors du remboursement)
                        # Donc on compte periods_until_next + 1 coupons au total
                        total_periods_with_coupon = periods_until_next + 1
                    
                    # Récupérer les frais déjà payés (cashflow_adjustments) depuis le résultat de valorisation
                    cashflow_adjustments = result.metadata.get("cashflow_adjustments") or 0.0
                    # Valeur = capital initial + coupons jusqu'à la prochaine constatation (incluant le coupon de remboursement) + frais déjà payés
                    # Note: on n'inclut pas les frais futurs de rachat (non connus à l'avance)
                    value_if_strike_next = float(invested_amount) * (1.0 + gps * total_periods_with_coupon) + float(cashflow_adjustments)
                    gain_if_strike_next = value_if_strike_next - float(invested_amount)
                    if float(invested_amount) != 0:
                        perf_if_strike_next = (gain_if_strike_next / float(invested_amount)) * 100.0
                        
                        # Annualiser la performance si strike
                        # Calculer les mois jusqu'à la prochaine constatation
                        months_until_next_obs = self._months_elapsed(position.investment.subscription_date, next_obs_date)
                        if months_until_next_obs > 0:
                            years_until_next = months_until_next_obs / 12.0
                            perf_if_strike_next_annualized = ((1.0 + perf_if_strike_next / 100.0) ** (1.0 / years_until_next) - 1.0) * 100.0
                except Exception:
                    pass

            # Sous-jacent: soit un index/serie (underlying_id), soit un taux (underlying).
            # Fallback: certains produits n'ont pas underlying_id dans assets.yaml mais l'ont dans les events.
            underlying_id = (asset.metadata or {}).get("underlying_id") or (asset.metadata or {}).get("underlying")
            if not underlying_id:
                for e in evts:
                    md = e.metadata or {}
                    u = md.get("underlying")
                    if u:
                        underlying_id = u
                        break
            strike_val = None
            strike_date_used = None
            strike_note = None
            is_rate_like = isinstance(underlying_id, str) and underlying_id.upper().startswith("CMS_")

            if underlying_id and not is_rate_like:
                strike_date = position.investment.subscription_date
                iod = find_initial_observation_date(asset, evts)
                if iod:
                    try:
                        strike_date = datetime.fromisoformat(iod).date()
                    except Exception:
                        pass

                # Prefer explicit initial level from brochure if provided (more reliable than scraping long history)
                initial_level = (asset.metadata or {}).get("initial_level")
                if initial_level is not None:
                    try:
                        strike_val = float(initial_level)
                        strike_date_used = strike_date
                        strike_note = "from_brochure_level"
                    except Exception:
                        # fall back to time series lookup
                        strike_val = None
                        strike_date_used = None
                        strike_note = None

                strike = self.underlyings_provider.get_data(
                    underlying_id, "underlying", strike_date
                )
                if strike_val is None and strike:
                    strike_val = strike.get("value")
                    strike_date_used = strike.get("date")
                    if strike_date_used and strike_date_used != strike_date:
                        strike_note = f"fallback<= {strike_date.isoformat()}"
                elif strike_val is None:
                    strike_note = f"no_data_for<= {strike_date.isoformat()}"

            underlying_current = None
            underlying_current_date = None
            perf_vs_strike = None
            underlying_current_note = None
            if underlying_id:
                # 1) Tentative via UnderlyingProvider (indices / sous-jacents)
                cur = self.underlyings_provider.get_data(underlying_id, "underlying", today)
                if cur:
                    underlying_current = cur.get("value")
                    d = cur.get("date")
                    underlying_current_date = d.isoformat() if d else None
                    underlying_current_note = None
                # 2) Fallback via RatesProvider (CMS, etc.) si pas trouvé
                if underlying_current is None:
                    cur_r = rates_provider.get_data(str(underlying_id), "rate", today)
                    if cur_r:
                        underlying_current = cur_r.get("value")
                        d = cur_r.get("date")
                        underlying_current_date = d.isoformat() if d else None
                        underlying_current_note = "rates"
                # 3) Fallback manuel (assets.yaml) si aucune série disponible
                if underlying_current is None:
                    md_asset = asset.metadata or {}
                    manual_val = (
                        md_asset.get("underlying_current_level")
                        if md_asset.get("underlying_current_level") is not None
                        else md_asset.get("current_level")
                    )
                    if manual_val is not None:
                        try:
                            underlying_current = float(manual_val)
                            manual_date = md_asset.get("underlying_current_date") or md_asset.get("current_level_date")
                            underlying_current_date = str(manual_date) if manual_date else today.isoformat()
                            underlying_current_note = "manual"
                        except Exception:
                            underlying_current = None
                            underlying_current_date = None
                            underlying_current_note = None
            # If we still don't have a strike but we do have history, use first available point as an approximation
            # (Euronext CSV download can be limited to ~2 years).
            if underlying_id and (not is_rate_like) and strike_val is None:
                hist = self.underlyings_provider.get_history(underlying_id)
                if hist:
                    strike_val = hist[0].value
                    strike_date_used = hist[0].point_date
                    strike_note = f"approx_first_available({hist[0].point_date.isoformat()})"
            if strike_val is not None and underlying_current is not None and strike_val != 0:
                try:
                    perf_vs_strike = (float(underlying_current) / float(strike_val) - 1.0) * 100.0
                except Exception:
                    perf_vs_strike = None

            # Seuil de remboursement (autocall) à la prochaine constatation
            redemption_trigger = None
            redemption_trigger_level = None
            redemption_trigger_pct = None
            redemption_operator = None  # ">=" / "<=" etc.
            redemption_threshold_value = None  # float (niveau ou taux selon le cas)
            redemption_missing_reason = None
            if next_obs_event is not None:
                md = next_obs_event.metadata or {}
                # % of initial
                pct = md.get("autocall_threshold_pct_of_initial")
                if pct is None:
                    pct = md.get("autocall_barrier_pct_of_initial")
                if pct is not None and strike_val is not None:
                    try:
                        redemption_trigger_pct = float(pct)
                        lvl = float(strike_val) * float(pct) / 100.0
                        redemption_trigger_level = lvl
                        redemption_operator = ">="
                        redemption_threshold_value = lvl
                        redemption_trigger = f">= {lvl:.4g} ({float(pct):.2f}% du initial)"
                    except Exception:
                        redemption_trigger = None
                # CMS-style absolute condition in text
                if redemption_trigger is None:
                    cond = md.get("autocall_condition") or ""
                    if isinstance(cond, str) and "cms" in cond.lower():
                        op, thr = _parse_condition_threshold(cond)
                        redemption_operator = op
                        redemption_threshold_value = thr
                        redemption_trigger = cond
                    elif isinstance(cond, str) and "initial" in cond.lower():
                        # Interprétation: Index >= Initial
                        if strike_val is not None:
                            redemption_operator = ">="
                            redemption_threshold_value = float(strike_val)
                            redemption_trigger_level = float(strike_val)
                        else:
                            redemption_operator = ">="
                            redemption_threshold_value = None
                            redemption_missing_reason = "strike manquant (renseigner metadata.initial_level ou un historique)"
                        redemption_trigger = cond
                    elif (next_obs_event.event_type or "").lower() in {"autocall_observation", "autocall_possible"}:
                        fallback = md.get("autocall_condition") or "Index >= Initial"
                        if isinstance(fallback, str) and "initial" in fallback.lower() and strike_val is not None:
                            redemption_operator = ">="
                            redemption_threshold_value = float(strike_val)
                            redemption_trigger_level = float(strike_val)
                        elif isinstance(fallback, str) and "initial" in fallback.lower() and strike_val is None:
                            redemption_operator = ">="
                            redemption_threshold_value = None
                            redemption_missing_reason = "strike manquant (renseigner metadata.initial_level ou un historique)"
                        redemption_trigger = fallback

            # Construire le nom avec le portefeuille pour différencier les positions du même produit
            contract_name = position.wrapper.contract_name
            display_name = asset.name
            # Ajouter le portefeuille au nom si plusieurs positions du même produit existent
            # (on vérifiera plus tard s'il y a plusieurs positions avec le même asset_id)
            # Calculer les frais totaux depuis les lots ou les métadonnées
            # UTILISE LotClassifier (source de vérité unique)
            fees_total = 0.0
            if lots:
                classified_lots = self.lot_classifier.classify_all_lots(lots, position.position_id)
                for cl in classified_lots:
                    if cl.category == LotCategory.FEE:
                        fees_total += cl.amount
            
            # Utiliser cashflow_adjustments depuis les métadonnées si disponible (plus fiable)
            cashflow_adjustments = (result.metadata or {}).get("cashflow_adjustments") if result else None
            if cashflow_adjustments is not None:
                fees_total = abs(float(cashflow_adjustments))
            
            rows.append({
                "name": asset.name,
                "display_name": display_name,  # Sera mis à jour plus tard si nécessaire
                "contract_name": contract_name,
                "position_id": position.position_id,
                "months": months,
                "period_months": period_months,  # Fréquence des coupons (6 = semestriel, 12 = annuel, etc.)
                "current_value": current_value,
                "invested_amount": invested_amount,
                "coupons_received": coupons_received,
                "fees_total": fees_total,
                "autocalled": autocalled,
                "cms_coupons_confirmed": bool((asset.metadata or {}).get("cms_past_coupons_confirmed_paid", False)),
                "gain_per_semester": gps,
                "coupon_pct": coupon_pct,
                "semesters_elapsed": sem_elapsed,
                "theoretical_value": theoretical_value,
                "value_if_strike_next": value_if_strike_next,
                "gain_if_strike_next": gain_if_strike_next,
                "perf_if_strike_next": perf_if_strike_next,
                "perf_if_strike_next_annualized": perf_if_strike_next_annualized,
                "sell_date": sell_date.isoformat() if sell_date else None,
                "sell_value_from_lots": sell_value_from_lots,
                "strike": strike_val,
                "strike_date": strike_date_used.isoformat() if strike_date_used else None,
                "strike_note": strike_note,
                "next_obs": next_obs,
                "underlying_id": underlying_id,
                "underlying_current": underlying_current,
                "underlying_current_date": underlying_current_date,
                "underlying_current_note": underlying_current_note,
                "perf_vs_strike": perf_vs_strike,
                "redemption_trigger": redemption_trigger,
                "redemption_trigger_level": redemption_trigger_level,
                "redemption_trigger_pct": redemption_trigger_pct,
                "redemption_operator": redemption_operator,
                "redemption_threshold_value": redemption_threshold_value,
                "redemption_missing_reason": redemption_missing_reason,
                # Ajouter les seuils pour le calcul de la perf annualisée
                "autocall_condition_threshold": (asset.metadata or {}).get("autocall_condition_threshold"),
                "coupon_condition_threshold": (asset.metadata or {}).get("coupon_condition_threshold"),
                "overdue_coupons": overdue_coupons,  # Liste des coupons manquants
                "coupon_status": coupon_status,  # Statut de tous les coupons attendus
            })
            
            # Utiliser helper centralisé pour déterminer si terminé
            is_sold_or_terminated = self._is_structured_product_terminated(
                result, lots, current_value, invested_amount
            )
            rows[-1]["is_sold_or_terminated"] = is_sold_or_terminated

        # Compter les positions par asset_id pour savoir si on doit ajouter le portefeuille au nom
        asset_id_counts = {}
        for r in rows:
            asset_id = None
            for position in self.portfolio.list_all_positions():
                if position.position_id == r["position_id"]:
                    asset_id = position.asset_id
                    break
            if asset_id:
                asset_id_counts[asset_id] = asset_id_counts.get(asset_id, 0) + 1
        
        # Mettre à jour display_name pour inclure le portefeuille si plusieurs positions du même produit
        for r in rows:
            asset_id = None
            for position in self.portfolio.list_all_positions():
                if position.position_id == r["position_id"]:
                    asset_id = position.asset_id
                    break
            if asset_id and asset_id_counts.get(asset_id, 0) > 1:
                # Plusieurs positions du même produit : ajouter le portefeuille au nom
                r["display_name"] = f"{r['name']} ({r['contract_name']})"
            else:
                r["display_name"] = r["name"]
        
        rows.sort(key=lambda r: (r["name"], r["position_id"]))

        def _truncate(s: str, max_len: int) -> str:
            s = "" if s is None else str(s)
            if max_len <= 0:
                return ""
            if len(s) <= max_len:
                return s
            if max_len <= 3:
                return s[:max_len]
            return s[: max_len - 3] + "..."

        def _clip_to_term(s: str, width: int) -> str:
            """Tronque une ligne à la largeur du terminal, sans casser l'affichage."""
            if not width or width <= 0:
                return s
            return _truncate(s, width)

        def _format_table(headers, data_rows, *, aligns=None, max_widths=None):
            """
            Render un tableau monospace lisible dans un terminal.
            - aligns: dict[col] -> 'l'|'r' (left/right)
            - max_widths: dict[col] -> int (cap de largeur, tronque avec "...")
            """
            aligns = aligns or {}
            max_widths = max_widths or {}

            # Convertir en matrice de strings
            matrix = []
            for r in data_rows:
                row = []
                for h in headers:
                    row.append("" if r.get(h) is None else str(r.get(h)))
                matrix.append(row)

            # Largeur auto, avec cap éventuel
            widths = []
            for i, h in enumerate(headers):
                col_vals = [h] + [matrix[j][i] for j in range(len(matrix))]
                w = max(len(v) for v in col_vals) if col_vals else len(h)
                cap = max_widths.get(h)
                if isinstance(cap, int) and cap > 0:
                    w = min(w, cap)
                widths.append(max(1, w))

            # Tronquer selon widths
            for j in range(len(matrix)):
                for i, h in enumerate(headers):
                    matrix[j][i] = _truncate(matrix[j][i], widths[i])

            header_cells = [_truncate(h, widths[i]) for i, h in enumerate(headers)]

            def fmt_cell(h, i, val):
                if aligns.get(h) == "r":
                    return val.rjust(widths[i])
                return val.ljust(widths[i])

            header_line = "  ".join(fmt_cell(headers[i], i, header_cells[i]) for i in range(len(headers)))
            sep_line = "  ".join(("-" * widths[i]) for i in range(len(headers)))
            lines = [header_line, sep_line]
            for row in matrix:
                lines.append("  ".join(fmt_cell(headers[i], i, row[i]) for i in range(len(headers))))
            return "\n".join(lines)

        term_width = shutil.get_terminal_size(fallback=(120, 20)).columns
        print("\n" + "=" * min(term_width, 120))
        if portfolio_name:
            print(f"PRODUITS STRUCTURÉS - Synthèse (Portefeuille: {portfolio_name})")
        else:
            print("PRODUITS STRUCTURÉS - Synthèse")
        print("=" * min(term_width, 120))

        # Filtrer les produits terminés si include_terminated est False
        if not include_terminated:
            rows = [r for r in rows if not r.get("is_sold_or_terminated", False)]
        
        if not rows:
            print("Aucune position de produit structuré.")
            return

        table_rows = []
        for r in rows:
            v = f"{r['current_value']:,.2f} €" if isinstance(r["current_value"], (int, float)) else "N/A"
            buy = f"{r['invested_amount']:,.2f} €" if isinstance(r.get("invested_amount"), (int, float)) else "N/A"
            theo = f"{r['theoretical_value']:,.2f} €" if isinstance(r.get("theoretical_value"), (int, float)) else "N/A"

            coupons_amt = None
            if isinstance(r.get("invested_amount"), (int, float)) and isinstance(r.get("current_value"), (int, float)):
                coupons_amt = float(r["current_value"]) - float(r["invested_amount"])
            coupons = f"{coupons_amt:,.2f} €" if isinstance(coupons_amt, (int, float)) else "N/A"
            if r.get("cms_coupons_confirmed") and coupons != "N/A":
                coupons = f"{coupons} (confirmé)"

            # Gain / Perf: utiliser current_value (qui inclut les frais) au lieu de theoretical_value
            # theoretical_value ne tient pas compte des frais, donc on utilise current_value pour avoir la performance nette
            # UTILISE _calculate_performance_metrics() (source de vérité unique)
            gain_base = r.get("current_value")
            gain_amt = None
            perf_amt = None
            perf_annualized = None
            if isinstance(r.get("invested_amount"), (int, float)) and isinstance(gain_base, (int, float)):
                # Récupérer la position pour accéder aux lots et subscription_date
                position = self.portfolio.get_position(r.get("position_id"))
                if position:
                    perf_metrics = self._calculate_performance_metrics(
                        current_value=float(gain_base),
                        invested_amount=float(r["invested_amount"]),
                        subscription_date=position.investment.subscription_date,
                        position_id=position.position_id,
                        end_date=r.get("sell_date") and datetime.fromisoformat(r["sell_date"]).date() if r.get("sell_date") else today,
                        lots=position.investment.lots or [],
                    )
                    gain_amt = perf_metrics['gain']
                    perf_amt = perf_metrics['perf']
                    perf_annualized = perf_metrics['perf_annualized']
                else:
                    # Fallback si position non trouvée
                    gain_amt = float(gain_base) - float(r["invested_amount"])
                    if float(r["invested_amount"]) != 0:
                        perf_amt = (gain_amt / float(r["invested_amount"])) * 100.0
                    # Calculer la performance annualisée
                    months = r.get("months", 0)
                    period_months = r.get("period_months", 6)  # Fréquence des coupons (6 = semestriel)
                    perf_if_strike = r.get("perf_if_strike_next")  # Performance si strike à la prochaine constatation
                    underlying_id = r.get("underlying_id")
                    underlying_current = r.get("underlying_current")
                    autocall_threshold = r.get("autocall_condition_threshold")  # Seuil pour strike (ex: 2.20%)
                    coupon_threshold = r.get("coupon_condition_threshold")  # Seuil pour coupon (ex: 3.20%)
                    gps = r.get("gain_per_semester")  # Gain par période
                    cashflow_adjustments = (result.metadata or {}).get("cashflow_adjustments") if result else None
                    invested_amt = r.get("invested_amount")
                    
                    # Pour les produits avec perf_if_strike et coupons périodiques, annualiser correctement
                    # même si months = 0 (produit récemment souscrit)
                    if perf_if_strike is not None and period_months and period_months == 6 and gps is not None:
                        # Produit semestriel : le taux annuel est gps * 2 (pas perf_if_strike qui est cumulé)
                        perf_annualized = float(gps) * 2 * 100.0
                    elif months and months > 0:
                        years = months / 12.0
                        if years > 0:
                            # Pour les produits CMS avec coupons conditionnels, vérifier si le strike serait possible aujourd'hui
                            is_cms = underlying_id and isinstance(underlying_id, str) and "CMS" in underlying_id.upper()
                            if is_cms and period_months and period_months > 0 and period_months < 12:
                                # Produit CMS : vérifier si strike possible aujourd'hui
                                strike_possible = False
                                if underlying_current is not None and autocall_threshold is not None:
                                    try:
                                        cms_current = float(underlying_current)
                                        autocall_thresh = float(autocall_threshold)
                                        strike_possible = cms_current <= autocall_thresh
                                    except (ValueError, TypeError):
                                        strike_possible = False
                                
                                if strike_possible and perf_if_strike is not None:
                                    # Strike possible : utiliser la perf si strike (inclut remboursement + coupons)
                                    # MAIS pour les produits avec coupons périodiques, annualiser correctement
                                    # perf_if_strike est la performance totale jusqu'à la prochaine constatation
                                    # Il faut l'annualiser en fonction du nombre de semestres écoulés
                                    if period_months and period_months == 6 and gps is not None:
                                        # Produit semestriel : le taux annuel est gps * 2
                                        perf_annualized = float(gps) * 2 * 100.0
                                    else:
                                        # Sinon utiliser perf_if_strike tel quel
                                        perf_annualized = float(perf_if_strike)
                                elif underlying_current is not None and coupon_threshold is not None and gps is not None and invested_amt:
                                    # Pas de strike mais coupon possible : calculer la perf basée sur les coupons seulement
                                    try:
                                        cms_current = float(underlying_current)
                                        coupon_thresh = float(coupon_threshold)
                                        if cms_current <= coupon_thresh:
                                            # Coupon serait payé : projeter 2 coupons par an
                                            periods_per_year = 12.0 / float(period_months)
                                            # Performance brute annuelle (2 coupons)
                                            perf_brute_annuelle = float(gps) * periods_per_year * 100.0
                                            # Ajuster avec les frais (proportionnellement)
                                            periods_elapsed = months / float(period_months)
                                            if periods_elapsed > 0:
                                                perf_brute_per_period = float(gps) * 100.0
                                                perf_nette_per_period = perf_amt / periods_elapsed
                                                frais_ratio = (perf_brute_per_period - perf_nette_per_period) / perf_brute_per_period if perf_brute_per_period > 0 else 0
                                                perf_annualized = perf_brute_annuelle * (1.0 - frais_ratio)
                                            else:
                                                perf_annualized = None
                                        else:
                                            # Pas de coupon : extrapolation classique
                                            try:
                                                perf_annualized = ((1.0 + perf_amt / 100.0) ** (1.0 / years) - 1.0) * 100.0
                                            except Exception:
                                                perf_annualized = None
                                    except (ValueError, TypeError):
                                        # Fallback : extrapolation classique
                                        try:
                                            perf_annualized = ((1.0 + perf_amt / 100.0) ** (1.0 / years) - 1.0) * 100.0
                                        except Exception:
                                            perf_annualized = None
                                else:
                                    # Fallback : extrapolation classique
                                    try:
                                        perf_annualized = ((1.0 + perf_amt / 100.0) ** (1.0 / years) - 1.0) * 100.0
                                    except Exception:
                                        perf_annualized = None
                            elif period_months and period_months > 0 and period_months < 12 and perf_if_strike is not None:
                                # Produit avec coupons périodiques (non CMS) : annualiser correctement
                                # perf_if_strike est la performance totale jusqu'à la prochaine constatation
                                # Pour les produits semestriels, le taux annuel est gps * 2
                                if period_months == 6 and gps is not None:
                                    # Produit semestriel : le taux annuel est gps * 2
                                    perf_annualized = float(gps) * 2 * 100.0
                                else:
                                    # Autres périodes : utiliser perf_if_strike tel quel
                                    perf_annualized = float(perf_if_strike)
                            else:
                                # Produit sans coupons périodiques ou perf si strike non disponible : extrapolation classique
                                # Performance annualisée = (1 + perf/100)^(1/years) - 1
                                try:
                                    perf_annualized = ((1.0 + perf_amt / 100.0) ** (1.0 / years) - 1.0) * 100.0
                                except Exception:
                                    perf_annualized = None
                        else:
                            perf_annualized = None
                    else:
                        perf_annualized = None
            gain_str = f"{gain_amt:,.2f} €" if isinstance(gain_amt, (int, float)) else "N/A"
            perf_str = f"{perf_amt:+.2f}%" if isinstance(perf_amt, (int, float)) else "N/A"
            # Performance annualisée via XIRR (flux réels)
            perf_annualized_str = f"{perf_annualized:+.2f}%/an" if isinstance(perf_annualized, (int, float)) else "N/A"
            # Performance annualisée si strike à la prochaine observation
            perf_if_strike_annualized = r.get("perf_if_strike_next_annualized")
            perf_if_strike_annualized_str = f"{perf_if_strike_annualized:+.2f}%/an" if isinstance(perf_if_strike_annualized, (int, float)) else "N/A"

            # Utiliser display_name qui inclut le portefeuille si nécessaire
            name = r.get("display_name", r["name"])
            if isinstance(r.get("coupon_pct"), (int, float)):
                name = f"{name} ({float(r['coupon_pct']):.2f}%)"

            if r["underlying_current"] is None:
                und_last = "N/A"
                if r.get("underlying_id"):
                    und_last = f"{r['underlying_id']} (pas de série)"
            else:
                note = f" ({r['underlying_current_note']})" if r.get("underlying_current_note") else ""
                und_last = f"{r['underlying_current']} @ {r['underlying_current_date']}{note}"

            if r["strike"] is None:
                strike = "N/A"
                if r.get("strike_note"):
                    strike = f"{strike} [{r['strike_note']}]"
            else:
                strike = f"{r['strike']} @ {r['strike_date']}" if r["strike_date"] else f"{r['strike']}"
                if r.get("strike_note"):
                    strike = f"{strike} [{r['strike_note']}]"

            perf_u = f"{r['perf_vs_strike']:+.2f}%" if isinstance(r.get("perf_vs_strike"), (int, float)) else "N/A"
            
            # Utiliser helper centralisé pour déterminer si terminé
            # Note: on doit reconstruire un ValuationResult minimal pour utiliser le helper
            # mais on peut aussi utiliser directement les valeurs stockées dans r
            current_val = r.get("current_value")
            invested_amt = r.get("invested_amount")
            lots_for_check = r.get("lots") or []
            result_for_check = r.get("result")
            if result_for_check:
                is_sold_or_terminated = self._is_structured_product_terminated(
                    result_for_check, lots_for_check, current_val, invested_amt
                )
            else:
                # Fallback si result n'est pas disponible
                is_sold_or_terminated = (
                    r.get("autocalled") is True or
                    r.get("sell_date") is not None or
                    r.get("sell_value_from_lots") is not None or
                    (current_val is not None and abs(float(current_val or 0)) < 0.01 and 
                     invested_amt and float(invested_amt or 0) > 0)
                )
            
            nxt = r["next_obs"] or ("terminé" if is_sold_or_terminated else "N/A")
            trig = r.get("redemption_trigger") or "N/A"

            # Remboursement si constatation aujourd'hui ?
            redeem_today = "terminé" if is_sold_or_terminated else "N/A"
            if not is_sold_or_terminated:
                if r.get("autocalled") is True:
                    redeem_today = "OUI (déjà)"
                else:
                    op = r.get("redemption_operator")
                    thr = r.get("redemption_threshold_value")
                    uc = r.get("underlying_current")
                    if op in {">=", "<=", ">", "<"} and thr is None:
                        # Condition connue mais seuil introuvable (ex: strike manquant)
                        reason = r.get("redemption_missing_reason")
                        redeem_today = f"N/A ({reason})" if reason else "N/A"
                    elif op in {">=", "<=", ">", "<"} and isinstance(thr, (int, float)) and uc is not None:
                        try:
                            uc_f = float(uc)
                            thr_f = float(thr)
                            if op == ">=":
                                redeem_today = "OUI" if uc_f >= thr_f else "non"
                            elif op == ">":
                                redeem_today = "OUI" if uc_f > thr_f else "non"
                            elif op == "<=":
                                redeem_today = "OUI" if uc_f <= thr_f else "non"
                            elif op == "<":
                                redeem_today = "OUI" if uc_f < thr_f else "non"
                            else:
                                redeem_today = "N/A"
                        except Exception:
                            redeem_today = "N/A"

            # Formatage des nouvelles colonnes "si strike à la prochaine constatation"
            if is_sold_or_terminated:
                value_if_strike_str = "terminé"
                gain_if_strike_str = "terminé"
                perf_if_strike_str = "terminé"
            else:
                value_if_strike_str = f"{r['value_if_strike_next']:,.2f} €" if isinstance(r.get("value_if_strike_next"), (int, float)) else "N/A"
                gain_if_strike_str = f"{r['gain_if_strike_next']:,.2f} €" if isinstance(r.get("gain_if_strike_next"), (int, float)) else "N/A"
                perf_if_strike_str = f"{r['perf_if_strike_next']:+.2f}%" if isinstance(r.get("perf_if_strike_next"), (int, float)) else "N/A"

            # Formatage du taux de coupon
            coupon_pct_str = f"{r['coupon_pct']:.2f}%" if isinstance(r.get("coupon_pct"), (int, float)) else "N/A"

            # Tronquer le nom du portefeuille à 5 caractères
            portfolio_name = r.get("contract_name", "N/A")
            if portfolio_name and portfolio_name != "N/A":
                portfolio_name = portfolio_name[:5]
            
            table_rows.append({
                "Nom": name,
                "Portefeuille": portfolio_name,
                "Mois": str(r["months"]),
                "Sous-jacent": und_last,
                "Initial": strike,
                "Var%": perf_u,
                "Prochaine": nxt,
                "Seuil remb.": trig,
                "Remb. si ajd ?": redeem_today,
                "Coupon %": coupon_pct_str,
                "Achat": buy,
                "Coupons": coupons,
                "Valeur": v,
                "Remb. théorique": theo,
                "Gain": gain_str,
                "Perf": perf_str,
                "Perf/an": perf_annualized_str,
                "Perf si strike/an": perf_if_strike_annualized_str,
                "Valeur si strike": value_if_strike_str,
                "Gain si strike": gain_if_strike_str,
                "Perf si strike": perf_if_strike_str,
            })

        if wide:
            headers = [
                "Nom",
                "Portefeuille",
                "Mois",
                "Sous-jacent",
                "Initial",
                "Var%",
                "Prochaine",
                "Seuil remb.",
                "Remb. si ajd ?",
                "Coupon %",
                "Achat",
                "Coupons",
                "Valeur",
                "Remb. théorique",
                "Gain",
                "Perf",
                "Perf/an",
                "Perf si strike/an",
                "Valeur si strike",
                "Gain si strike",
                "Perf si strike",
            ]
            aligns = {
                "Mois": "r",
                "Var%": "r",
                "Coupon %": "r",
                "Achat": "r",
                "Coupons": "r",
                "Valeur": "r",
                "Remb. théorique": "r",
                "Gain": "r",
                "Perf": "r",
                "Perf/an": "r",
                "Perf si strike/an": "r",
                "Valeur si strike": "r",
                "Gain si strike": "r",
                "Perf si strike": "r",
            }
            # Caps par défaut (évite des lignes infinies). Ajustables au besoin.
            max_widths = {
                "Nom": 42,
                "Sous-jacent": 22,
                "Initial": 26,
                "Seuil remb.": 26,
                "Remb. si ajd ?": 12,
                "Coupon %": 8,
            }
            # Si terminal étroit, on serre un peu.
            if term_width and term_width < 120:
                max_widths["Nom"] = min(max_widths["Nom"], 32)
                max_widths["Seuil remb."] = min(max_widths["Seuil remb."], 20)

            print(_format_table(headers, table_rows, aligns=aligns, max_widths=max_widths))
            return

        # Vue compacte (par défaut) : moins de colonnes => pas de wrap, + détails en 2e ligne.
        compact_headers = ["Nom", "Portefeuille", "Mois", "Prochaine", "Remb. si ajd ?", "Coupon %", "Achat", "Valeur", "Gain", "Perf", "Perf/an", "Perf si strike/an", "Valeur si strike", "Gain si strike", "Perf si strike"]
        compact_aligns = {"Mois": "r", "Coupon %": "r", "Achat": "r", "Valeur": "r", "Gain": "r", "Perf": "r", "Perf/an": "r", "Perf si strike/an": "r", "Valeur si strike": "r", "Gain si strike": "r", "Perf si strike": "r"}
        compact_rows = [{h: r.get(h) for h in compact_headers} for r in table_rows]

        # Ajuster largeur du nom selon la place dispo (pour éviter le wrap).
        # Largeur approx = somme des autres colonnes + séparateurs.
        other_caps = {
            "Portefeuille": 5,
            "Mois": 4,
            "Prochaine": 10,
            "Remb. si ajd ?": 12,
            "Coupon %": 8,
            "Achat": 14,
            "Valeur": 14,
            "Gain": 12,
            "Perf": 8,
            "Perf/an": 12,
            "Perf si strike/an": 16,
            "Valeur si strike": 16,
            "Gain si strike": 14,
            "Perf si strike": 10,
        }
        sep_len = 2 * (len(compact_headers) - 1)
        fixed = sum(other_caps.values()) + sep_len
        # 8 = marge pour en-têtes et respirations.
        name_cap = max(20, min(60, (term_width or 120) - fixed - 8))
        compact_max_widths = {"Nom": name_cap}

        print(_format_table(compact_headers, compact_rows, aligns=compact_aligns, max_widths=compact_max_widths))

        if not details:
            return

        # Lignes de détails par produit (une ou deux lignes), tronquées à la largeur du terminal.
        print()
        for idx, r_full in enumerate(table_rows):
            # Récupérer le row original correspondant
            r_original = rows[idx] if idx < len(rows) else {}
            
            detail_1 = f"  • Sous-jacent: {r_full.get('Sous-jacent')} | Initial: {r_full.get('Initial')} | Var%: {r_full.get('Var%')}"
            # Ajouter les frais dans la ligne de détails
            fees_str = ""
            fees_value = r_original.get("fees_total", 0.0)
            if fees_value and abs(float(fees_value)) > 0.01:
                fees_str = f" | Frais: {abs(float(fees_value)):,.2f} €"
            detail_2 = f"    Seuil remb.: {r_full.get('Seuil remb.')} | Remb. si ajd ?: {r_full.get('Remb. si ajd ?')} | Coupons: {r_full.get('Coupons')}{fees_str} | Remb. théorique: {r_full.get('Remb. théorique')}"
            
            # Afficher le statut des coupons et les erreurs si coupons manquants
            coupon_status_list = r_original.get("coupon_status", [])
            overdue_coupons_list = r_original.get("overdue_coupons", [])
            
            if coupon_status_list or overdue_coupons_list:
                detail_3_parts = []
                # Afficher les coupons avec leur statut
                if coupon_status_list:
                    coupon_status_strs = []
                    for cs in sorted(coupon_status_list, key=lambda x: x.get('date') or date.min):
                        cs_date = cs.get('date')
                        if isinstance(cs_date, str):
                            try:
                                cs_date = datetime.fromisoformat(cs_date).date()
                            except:
                                pass
                        date_str = cs_date.isoformat() if hasattr(cs_date, 'isoformat') else str(cs_date)
                        amount = cs.get('amount', 0)
                        amount_pct = f"{float(amount) * 100:.2f}%" if amount else "N/A"
                        status_icon = "✅" if cs.get('paid', False) else "❌"
                        coupon_status_strs.append(f"{status_icon} {date_str}: {amount_pct}")
                    if coupon_status_strs:
                        detail_3_parts.append(f"Coupons: {', '.join(coupon_status_strs)}")
                
                # Afficher les erreurs pour les coupons manquants
                if overdue_coupons_list:
                    missing_dates = [c.get('date', 'N/A') for c in overdue_coupons_list]
                    missing_str = ', '.join(str(d) for d in missing_dates)
                    detail_3_parts.append(f"⚠️  ERREUR: Coupons manquants (non saisis): {missing_str}")
                
                if detail_3_parts:
                    detail_3 = "    " + " | ".join(detail_3_parts)
                    print(_clip_to_term(detail_3, term_width))
            
            print(_clip_to_term(detail_1, term_width))
            print(_clip_to_term(detail_2, term_width))
            print()
    
    def _calculate_xirr(self, cashflows: list, guess: float = 0.1, max_iter: int = 100, precision: float = 1e-6) -> Optional[float]:
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
    
    @staticmethod
    def _is_external_contribution(lot: dict, has_previous_external: bool = False) -> bool:
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
        lot_date = lot.get('date')
        if lot_date and has_previous_external:
            try:
                if isinstance(lot_date, str):
                    lot_date_obj = datetime.fromisoformat(lot_date).date()
                else:
                    lot_date_obj = lot_date
                # Si c'est le 31/12 et qu'on a déjà vu des versements externes, c'est probablement des intérêts
                if lot_date_obj.month == 12 and lot_date_obj.day == 31:
                    return False
            except:
                pass
        
        # Par défaut, considérer comme versement externe (pour compatibilité)
        return True
    
    def _calculate_invested_amounts(self, lots: list, position_id: str, ref_date: Optional[date] = None) -> dict:
        """
        Calcule les montants investis à partir des lots.
        MÉTHODE CENTRALISÉE utilisant LotClassifier (source de vérité unique).
        
        Args:
            lots: Liste des lots (mouvements)
            position_id: ID de la position (pour la classification)
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
        classified_lots = self.lot_classifier.classify_all_lots(lots, position_id)
        
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
    
    def _build_cashflows_for_xirr(self, lots: list, position_id: str, value_at_end: float, end_date: date) -> list:
        """
        Construit la liste des flux de trésorerie pour le calcul XIRR.
        MÉTHODE CENTRALISÉE utilisant LotClassifier (source de vérité unique).
        
        Args:
            lots: Liste des lots (mouvements)
            position_id: ID de la position (pour la classification)
            value_at_end: Valeur finale (positive)
            end_date: Date de la valeur finale
        
        Returns:
            Liste de tuples (date, montant) pour XIRR
        """
        cashflows = []
        
        # Classifier tous les lots (source de vérité unique)
        classified_lots = self.lot_classifier.classify_all_lots(lots, position_id)
        
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
    
    def _calculate_performance_metrics(
        self,
        current_value: float,
        invested_amount: float,
        subscription_date: date,
        position_id: str,
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
                    cashflows = self._build_cashflows_for_xirr(lots, position_id, value_for_xirr, end_date)
                    if cashflows:
                        xirr_result = self._calculate_xirr(cashflows)
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
    
    def _collect_view_data(self, *, include_terminated: bool = False):
        """
        Collecte les données de toutes les vues (fonds euros, UC, structurés)
        en utilisant exactement les mêmes calculs que les vues.
        
        Returns:
            Tuple de (fonds_euro_rows, uc_rows, structured_rows)
        """
        # Utiliser les méthodes helper qui utilisent exactement la même logique que les vues
        fonds_euro_rows = self._get_fonds_euro_data(include_terminated=include_terminated)
        uc_rows = self._get_uc_data(include_terminated=include_terminated)
        structured_rows = self._get_structured_data(include_terminated=include_terminated)
        
        return fonds_euro_rows, uc_rows, structured_rows
    
    def _get_uc_data(self, *, include_terminated: bool = False):
        """
        Collecte les données des UC (même logique que uc_view).
        Retourne la liste des rows calculées.
        """
        today = datetime.now().date()
        rows = []
        
        for position in self.portfolio.list_all_positions():
            asset = self.portfolio.get_asset(position.asset_id)
            if not asset or asset.asset_type.value not in {"uc_fund", "uc_illiquid"}:
                continue
            
            engine = self.engines.get(asset.valuation_engine)
            if not engine:
                continue
            
            result = engine.valuate(asset, position, today)
            units_held = position.investment.units_held
            is_sold = False
            if units_held is not None:
                try:
                    if abs(float(units_held)) < 0.01:
                        is_sold = True
                except:
                    pass
            
            current_value = result.current_value or 0.0
            if is_sold and abs(current_value) < 0.01:
                if not include_terminated:
                    continue
            
            invested_amount = position.investment.invested_amount
            lots = position.investment.lots or []
            if lots:
                invested_amounts = self._calculate_invested_amounts(lots, position.position_id)
                invested_amount = invested_amounts['invested_total']
            
            contract_name = position.wrapper.contract_name or ""
            portfolio_name = contract_name[:5] if len(contract_name) > 5 else contract_name
            
            # Collecter les frais depuis cashflow_adjustments
            fees = 0.0
            cashflow_adjustments = (result.metadata or {}).get("cashflow_adjustments")
            if cashflow_adjustments is not None:
                fees = abs(float(cashflow_adjustments))
            
            rows.append({
                "portfolio_name": portfolio_name,
                "invested_amount": float(invested_amount) if invested_amount else 0.0,
                "current_value": float(current_value),
                "gain": float(current_value) - float(invested_amount) if invested_amount else 0.0,
                "fees": fees,
                "is_sold": is_sold,
            })
        
        # Filtrer comme dans uc_view
        if not include_terminated:
            rows = [r for r in rows if not r["is_sold"]]
        
        return rows
    
    def _get_structured_data(self, *, include_terminated: bool = False):
        """
        Collecte les données des produits structurés (même logique que structured_products_view).
        Retourne la liste des rows calculées.
        """
        today = datetime.now().date()
        rows = []
        
        for position in self.portfolio.list_all_positions():
            asset = self.portfolio.get_asset(position.asset_id)
            if not asset or asset.asset_type != AssetType.STRUCTURED_PRODUCT:
                continue
            
            engine = self.engines.get(asset.valuation_engine)
            if not engine:
                continue
            
            result = engine.valuate(asset, position, today)
            current_value = result.current_value if result else None
            
            invested_amount = position.investment.invested_amount
            lots = position.investment.lots or []
            if lots:
                invested_amounts = self._calculate_invested_amounts(lots, position.position_id)
                invested_amount = invested_amounts['invested_total']
            
            # Utiliser helper centralisé pour déterminer si terminé
            is_sold_or_terminated = self._is_structured_product_terminated(
                result, lots, current_value, invested_amount
            )
            
            if current_value is None:
                current_value = 0.0
            
            contract_name = position.wrapper.contract_name or ""
            portfolio_name = contract_name[:5] if len(contract_name) > 5 else contract_name
            
            # Collecter les frais depuis cashflow_adjustments
            fees = 0.0
            cashflow_adjustments = (result.metadata or {}).get("cashflow_adjustments")
            if cashflow_adjustments is not None:
                fees = abs(float(cashflow_adjustments))
            
            rows.append({
                "portfolio_name": portfolio_name,
                "invested_amount": float(invested_amount) if invested_amount else 0.0,
                "current_value": float(current_value),
                "gain": float(current_value) - float(invested_amount) if invested_amount else 0.0,
                "fees": fees,
                "is_sold_or_terminated": is_sold_or_terminated,
            })
        
        # Filtrer comme dans structured_products_view
        if not include_terminated:
            rows = [r for r in rows if not r.get("is_sold_or_terminated", False)]
        
        return rows
    
    def global_view(self, *, wide: bool = False, details: bool = False, include_terminated: bool = False, portfolio_name: Optional[str] = None):
        """
        Vue globale : affiche fonds euros, UC, produits structurés, puis un récapitulatif
        par portefeuille et par type de produit.
        
        Args:
            wide: Affiche toutes les colonnes
            details: Affiche les détails pour chaque produit
            include_terminated: Inclut les produits terminés (vendus) dans l'affichage
            portfolio_name: Filtre par nom de portefeuille (ex: "HIMAL", "Swiss"). Si None, affiche tous les portefeuilles.
        """
        from collections import defaultdict
        import io
        from contextlib import redirect_stdout
        
        # Collecter les données AVANT d'afficher (pour capturer les valeurs exactes)
        fonds_euro_rows, uc_rows, structured_rows = self._collect_view_data(include_terminated=include_terminated)
        
        # Filtrer par portefeuille si spécifié
        if portfolio_name:
            # Normaliser le nom du portefeuille (prendre les 5 premiers caractères comme dans les méthodes de collecte)
            portfolio_filter = portfolio_name[:5] if len(portfolio_name) > 5 else portfolio_name
            fonds_euro_rows = [r for r in fonds_euro_rows if (r.get('portfolio_name') or '')[:5] == portfolio_filter[:5]]
            uc_rows = [r for r in uc_rows if (r.get('portfolio_name') or '')[:5] == portfolio_filter[:5]]
            structured_rows = [r for r in structured_rows if (r.get('portfolio_name') or '')[:5] == portfolio_filter[:5]]
        
        # Afficher les 3 vues
        print("\n" + "=" * 100)
        if portfolio_name:
            print(f"VUE GLOBALE - FONDS EUROS, UC ET PRODUITS STRUCTURÉS (Portefeuille: {portfolio_name})")
        else:
            print("VUE GLOBALE - FONDS EUROS, UC ET PRODUITS STRUCTURÉS")
        print("=" * 100 + "\n")
        
        # Afficher fonds euros (filtré si portfolio_name est spécifié)
        self.fonds_euro_view(wide=wide, details=details, include_terminated=include_terminated, portfolio_name=portfolio_name)
        
        # Afficher UC (filtré si portfolio_name est spécifié)
        self.uc_view(wide=wide, details=details, include_terminated=include_terminated, portfolio_name=portfolio_name)
        
        # Afficher produits structurés (filtré si portfolio_name est spécifié)
        self.structured_products_view(wide=wide, details=details, include_terminated=include_terminated, portfolio_name=portfolio_name)
        
        # Récapitulatif par portefeuille et par type
        print("\n" + "=" * 100)
        print("RÉCAPITULATIF PAR PORTEFEUILLE ET PAR TYPE")
        print("=" * 100 + "\n")
        
        # Collecter les données par portefeuille et par type
        recap_by_portfolio = defaultdict(lambda: {
            'fonds_euro': {'invested': 0.0, 'value': 0.0, 'gain': 0.0, 'fees': 0.0, 'count': 0},
            'uc': {'invested': 0.0, 'value': 0.0, 'gain': 0.0, 'fees': 0.0, 'count': 0},
            'structured': {'invested': 0.0, 'value': 0.0, 'gain': 0.0, 'fees': 0.0, 'count': 0},
        })
        
        recap_by_type = {
            'fonds_euro': {'invested': 0.0, 'value': 0.0, 'gain': 0.0, 'fees': 0.0, 'count': 0},
            'uc': {'invested': 0.0, 'value': 0.0, 'gain': 0.0, 'fees': 0.0, 'count': 0},
            'structured': {'invested': 0.0, 'value': 0.0, 'gain': 0.0, 'fees': 0.0, 'count': 0},
        }
        
        # Agréger les données collectées (déjà filtrées si portfolio_name est spécifié)
        for row in fonds_euro_rows:
            row_portfolio_name = row['portfolio_name'] or "Autre"
            recap_by_portfolio[row_portfolio_name]['fonds_euro']['invested'] += row['invested_amount']
            recap_by_portfolio[row_portfolio_name]['fonds_euro']['value'] += row['current_value']
            recap_by_portfolio[row_portfolio_name]['fonds_euro']['gain'] += row['gain']
            recap_by_portfolio[row_portfolio_name]['fonds_euro']['fees'] += row.get('fees', 0.0)
            recap_by_portfolio[row_portfolio_name]['fonds_euro']['count'] += 1
            
            recap_by_type['fonds_euro']['invested'] += row['invested_amount']
            recap_by_type['fonds_euro']['value'] += row['current_value']
            recap_by_type['fonds_euro']['gain'] += row['gain']
            recap_by_type['fonds_euro']['fees'] += row.get('fees', 0.0)
            recap_by_type['fonds_euro']['count'] += 1
        
        for row in uc_rows:
            row_portfolio_name = row['portfolio_name'] or "Autre"
            recap_by_portfolio[row_portfolio_name]['uc']['invested'] += row['invested_amount']
            recap_by_portfolio[row_portfolio_name]['uc']['value'] += row['current_value']
            recap_by_portfolio[row_portfolio_name]['uc']['gain'] += row['gain']
            recap_by_portfolio[row_portfolio_name]['uc']['fees'] += row.get('fees', 0.0)
            recap_by_portfolio[row_portfolio_name]['uc']['count'] += 1
            
            recap_by_type['uc']['invested'] += row['invested_amount']
            recap_by_type['uc']['value'] += row['current_value']
            recap_by_type['uc']['gain'] += row['gain']
            recap_by_type['uc']['fees'] += row.get('fees', 0.0)
            recap_by_type['uc']['count'] += 1
        
        for row in structured_rows:
            row_portfolio_name = row['portfolio_name'] or "Autre"
            recap_by_portfolio[row_portfolio_name]['structured']['invested'] += row['invested_amount']
            recap_by_portfolio[row_portfolio_name]['structured']['value'] += row['current_value']
            recap_by_portfolio[row_portfolio_name]['structured']['gain'] += row['gain']
            recap_by_portfolio[row_portfolio_name]['structured']['fees'] += row.get('fees', 0.0)
            recap_by_portfolio[row_portfolio_name]['structured']['count'] += 1
            
            recap_by_type['structured']['invested'] += row['invested_amount']
            recap_by_type['structured']['value'] += row['current_value']
            recap_by_type['structured']['gain'] += row['gain']
            recap_by_type['structured']['fees'] += row.get('fees', 0.0)
            recap_by_type['structured']['count'] += 1
        
        # Afficher le récap par portefeuille
        print("📊 PAR PORTEFEUILLE")
        print("-" * 100)
        print(f"{'Portefeuille':<20} {'Type':<15} {'Nb':<5} {'Investi':>15} {'Valeur':>15} {'Gain':>15} {'Perf':>10}")
        print("-" * 100)
        
        # Filtrer les portefeuilles si un filtre est appliqué
        portfolios_to_show = sorted(recap_by_portfolio.keys())
        if portfolio_name:
            portfolio_filter = portfolio_name[:5] if len(portfolio_name) > 5 else portfolio_name
            portfolios_to_show = [p for p in portfolios_to_show if (p or '')[:5] == portfolio_filter[:5]]
            if not portfolios_to_show:
                print(f"⚠️  Aucun portefeuille trouvé correspondant à '{portfolio_name}'")
                return
        
        for portfolio_name_display in portfolios_to_show:
            portfolio_data = recap_by_portfolio[portfolio_name_display]
            portfolio_total_invested = 0.0
            portfolio_total_value = 0.0
            portfolio_total_gain = 0.0
            
            for asset_type in ['fonds_euro', 'uc', 'structured']:
                data = portfolio_data[asset_type]
                if data['count'] > 0:
                    perf = (data['gain'] / data['invested'] * 100.0) if data['invested'] > 0 else 0.0
                    type_label = {'fonds_euro': 'Fonds Euros', 'uc': 'UC', 'structured': 'Structurés'}[asset_type]
                    print(f"{portfolio_name_display:<20} {type_label:<15} {data['count']:<5} "
                          f"{data['invested']:>15,.2f} € {data['value']:>15,.2f} € "
                          f"{data['gain']:>+15,.2f} € {perf:>+9.2f}%")
                    
                    portfolio_total_invested += data['invested']
                    portfolio_total_value += data['value']
                    portfolio_total_gain += data['gain']
            
            # Ligne total portefeuille
            if portfolio_total_invested > 0:
                portfolio_perf = (portfolio_total_gain / portfolio_total_invested * 100.0)
                print(f"{'':<20} {'TOTAL':<15} {'':<5} "
                      f"{portfolio_total_invested:>15,.2f} € {portfolio_total_value:>15,.2f} € "
                      f"{portfolio_total_gain:>+15,.2f} € {portfolio_perf:>+9.2f}%")
                print("-" * 100)
        
        # Afficher le récap par type
        print("\n📊 PAR TYPE DE PRODUIT")
        print("-" * 100)
        print(f"{'Type':<20} {'Nb':<5} {'Investi':>15} {'Valeur':>15} {'Gain':>15} {'Perf':>10}")
        print("-" * 100)
        
        for asset_type in ['fonds_euro', 'uc', 'structured']:
            data = recap_by_type[asset_type]
            if data['count'] > 0:
                perf = (data['gain'] / data['invested'] * 100.0) if data['invested'] > 0 else 0.0
                type_label = {'fonds_euro': 'Fonds Euros', 'uc': 'UC', 'structured': 'Produits Structurés'}[asset_type]
                print(f"{type_label:<20} {data['count']:<5} "
                      f"{data['invested']:>15,.2f} € {data['value']:>15,.2f} € "
                      f"{data['gain']:>+15,.2f} € {perf:>+9.2f}%")
        
        # Total général
        total_invested = sum(d['invested'] for d in recap_by_type.values())
        total_value = sum(d['value'] for d in recap_by_type.values())
        total_gain = sum(d['gain'] for d in recap_by_type.values())
        total_fees = sum(d['fees'] for d in recap_by_type.values())
        total_count = sum(d['count'] for d in recap_by_type.values())
        
        if total_invested > 0:
            total_perf = (total_gain / total_invested * 100.0)
            print("-" * 100)
            print(f"{'TOTAL GÉNÉRAL':<20} {total_count:<5} "
                  f"{total_invested:>15,.2f} € {total_value:>15,.2f} € "
                  f"{total_gain:>+15,.2f} € {total_perf:>+9.2f}%")
        
        # Afficher le montant total des frais
        if total_fees > 0.01:
            print(f"{'FRAIS TOTAUX':<20} {'':<5} {'':<15} {'':<15} "
                  f"{-total_fees:>+15,.2f} € {'':<10}")
        
        # Ligne avec capital investi initial
        initial_capital = 1_190_000.00
        gain_from_initial = total_value - initial_capital
        perf_from_initial = (gain_from_initial / initial_capital * 100.0) if initial_capital > 0 else 0.0
        print("-" * 100)
        print(f"{'CAPITAL INITIAL':<20} {'':<5} "
              f"{initial_capital:>15,.2f} € {total_value:>15,.2f} € "
              f"{gain_from_initial:>+15,.2f} € {perf_from_initial:>+9.2f}%")
        
        print("=" * 100 + "\n")
    
    def _get_fonds_euro_data(self, *, include_terminated: bool = False):
        """
        Collecte les données des fonds euros (même logique que fonds_euro_view).
        Retourne la liste des rows calculées.
        """
        today = datetime.now().date()
        rows = []
        
        for position in self.portfolio.list_all_positions():
            asset = self.portfolio.get_asset(position.asset_id)
            if not asset or asset.asset_type != AssetType.FONDS_EURO:
                continue
            
            engine = self.engines.get(asset.valuation_engine)
            if not engine:
                continue
            
            result = engine.valuate(asset, position, today)
            
            # Utiliser helpers centralisés
            is_sold = self._is_position_sold(position)
            lots = position.investment.lots or []
            invested_amount = self._calculate_fonds_euro_invested_amount(position, lots)
            current_value = result.current_value or 0.0
            
            if is_sold and abs(current_value) < 0.01 and invested_amount < 0.01:
                if not include_terminated:
                    continue
            
            subscription_date = position.investment.subscription_date
            
            # Déterminer automatiquement la date de référence en fonction des mouvements de bénéfices disponibles
            ref_date_end = self._get_fonds_euro_reference_date(lots, position.position_id, today)
            
            # Calculer les mois de détention jusqu'à ref_date_end pour être cohérent avec la performance
            # Si la position est vendue avant ref_date_end, utiliser la date de vente
            sell_date = self._extract_sell_date_from_lots(lots) if is_sold else None
            if sell_date and sell_date < ref_date_end:
                valuation_date_for_months = sell_date
            else:
                valuation_date_for_months = ref_date_end
            months = self._months_elapsed(subscription_date, valuation_date_for_months)
            
            value_for_perf, invested_for_perf = self._calculate_fonds_euro_performance_values(
                current_value, lots, position.position_id, ref_date_end
            )
            
            perf_metrics = self._calculate_performance_metrics(
                current_value=float(current_value) if current_value is not None else 0.0,
                invested_amount=float(invested_amount) if invested_amount else 0.0,
                subscription_date=subscription_date,
                position_id=position.position_id,
                end_date=ref_date_end,
                lots=lots,
                value_for_perf=value_for_perf,
                invested_for_perf=invested_for_perf if invested_for_perf > 0 else None,
            )
            
            contract_name = position.wrapper.contract_name or ""
            portfolio_name = contract_name[:5] if len(contract_name) > 5 else contract_name
            
            # Collecter les frais depuis cashflow_adjustments
            fees = 0.0
            cashflow_adjustments = (result.metadata or {}).get("cashflow_adjustments")
            if cashflow_adjustments is not None:
                fees = abs(float(cashflow_adjustments))
            
            rows.append({
                "portfolio_name": portfolio_name,
                "invested_amount": float(invested_amount) if invested_amount else 0.0,
                "current_value": float(current_value),
                "gain": perf_metrics['gain'],
                "fees": fees,
                "is_sold": is_sold,
            })
        
        # Filtrer comme dans fonds_euro_view
        if not include_terminated:
            rows = [r for r in rows if not r["is_sold"]]
        
        return rows
    
    def fonds_euro_view(self, *, wide: bool = False, details: bool = False, include_terminated: bool = False, portfolio_name: Optional[str] = None):
        """
        Vue synthèse des fonds euros:
        - nom du fonds
        - assureur
        - mois écoulés depuis souscription
        - capital investi
        - valeur actuelle
        - gain et performance
        - taux déclarés
        
        Args:
            wide: Affiche toutes les colonnes
            details: Affiche les détails pour chaque fonds euro
            include_terminated: Inclut les fonds euros terminés (rachat total) dans l'affichage
            portfolio_name: Filtre par nom de portefeuille (ex: "HIMAL", "Swiss"). Si None, affiche tous les portefeuilles.
        """
        today = datetime.now().date()

        print("\n" + "=" * 100)
        if portfolio_name:
            print(f"FONDS EUROS - Synthèse (Portefeuille: {portfolio_name})")
        else:
            print("FONDS EUROS - Synthèse")
        print("=" * 100 + "\n")

        # Filtrer les positions par portefeuille si spécifié
        all_positions = self.portfolio.list_all_positions()
        if portfolio_name:
            all_positions = self._filter_positions_by_portfolio(all_positions, portfolio_name)

        rows = []
        for position in all_positions:
            asset = self.portfolio.get_asset(position.asset_id)
            if not asset:
                continue
            if asset.asset_type != AssetType.FONDS_EURO:
                continue

            engine = self.engines.get(asset.valuation_engine)
            if not engine:
                continue
            result = engine.valuate(asset, position, today)
            
            # Vérifier si la position est rachetée (units_held ≈ 0 ou invested_amount ≈ 0)
            # Utiliser helpers centralisés
            is_sold = self._is_position_sold(position)
            lots = position.investment.lots or []
            invested_amount = self._calculate_fonds_euro_invested_amount(position, lots)
            
            # Exclure les positions rachetées du total (mais les garder pour l'affichage avec "terminé")
            current_value = result.current_value or 0.0
            if is_sold and abs(current_value) < 0.01 and (invested_amount is None or abs(float(invested_amount or 0)) < 0.01):
                # Position rachetée sans valeur, on peut la sauter ou l'afficher comme "terminé"
                pass
            
            # Calculer gain et performance avec XIRR (méthode centralisée)
            # Règle générique : tant qu'on n'a pas le mouvement de bénéfice pour une année,
            # on ne prend pas en compte cette année ni l'année N-1 dans le calcul.
            # UTILISE _calculate_performance_metrics() (source de vérité unique)
            
            # Déterminer automatiquement la date de référence en fonction des mouvements de bénéfices disponibles
            ref_date_end = self._get_fonds_euro_reference_date(lots, position.position_id, today)
            
            # Calculer les mois de détention jusqu'à ref_date_end pour être cohérent avec la performance
            # (qui est calculée jusqu'à N-1, pas jusqu'à aujourd'hui)
            subscription_date = position.investment.subscription_date
            # Si la position est vendue avant ref_date_end, utiliser la date de vente
            sell_date = self._extract_sell_date_from_lots(lots) if is_sold else None
            if sell_date and sell_date < ref_date_end:
                valuation_date_for_months = sell_date
            else:
                valuation_date_for_months = ref_date_end
            
            months = self._months_elapsed(subscription_date, valuation_date_for_months)
            
            # Utiliser helper centralisé pour calculer les valeurs de performance
            value_for_perf, invested_for_perf = self._calculate_fonds_euro_performance_values(
                current_value, lots, position.position_id, ref_date_end
            )
            
            # Calculer les métriques de performance avec la méthode centralisée
            perf_metrics = self._calculate_performance_metrics(
                current_value=float(current_value) if current_value is not None else 0.0,
                invested_amount=float(invested_amount) if invested_amount else 0.0,
                subscription_date=subscription_date,
                position_id=position.position_id,
                end_date=ref_date_end,
                lots=lots,
                value_for_perf=value_for_perf,
                invested_for_perf=invested_for_perf if invested_for_perf > 0 else None,
            )
            
            gain_amt = perf_metrics['gain']
            perf_amt = perf_metrics['perf']
            perf_annualized = perf_metrics['perf_annualized']
            
            # Pour les fonds euros, recalculer la performance annualisée à partir de la performance totale
            # en utilisant les mois affichés (calculés jusqu'à ref_date_end) pour être cohérent
            # La date de référence (ref_date_end) est déterminée automatiquement en fonction des
            # mouvements de bénéfices disponibles (dernière année avec bénéfices connus)
            if perf_amt is not None and months > 0:
                years_from_months = months / 12.0
                if years_from_months > 0:
                    perf_annualized = ((1.0 + perf_amt / 100.0) ** (1.0 / years_from_months) - 1.0) * 100.0
            
            # Récupérer l'assureur et le contrat
            insurer = (asset.metadata or {}).get("insurer", "")
            contract_name = position.wrapper.contract_name or ""
            portfolio_name = contract_name[:5] if len(contract_name) > 5 else contract_name
            
            # Construire le nom avec le portefeuille si plusieurs positions du même produit
            display_name = asset.name
            
            # Calculer les frais totaux (utilise helper centralisé)
            md = result.metadata or {}
            fees_total = self._calculate_fees_total(lots, md)
            
            rows.append({
                "asset_id": asset.asset_id,
                "name": asset.name,
                "display_name": display_name,
                "insurer": insurer,
                "contract_name": contract_name,
                "portfolio_name": portfolio_name,
                "position_id": position.position_id,
                "subscription_date": subscription_date,
                "months": months,
                "invested_amount": invested_amount,
                "current_value": current_value,
                "gain": gain_amt,
                "perf": perf_amt,
                "perf_annualized": perf_annualized,
                "is_sold": is_sold,
                "sell_date": sell_date,
                "fees_total": fees_total,
                "result": result,
                "position": position,
                "asset": asset,
            })

        # Filtrer les fonds euros terminés si include_terminated est False
        if not include_terminated:
            rows = [r for r in rows if not r["is_sold"]]
        
        if not rows:
            print("Aucun fonds euro trouvé.")
            return

        # Compter les positions par asset_id pour savoir si on doit ajouter le portefeuille au nom
        asset_id_counts = {}
        for r in rows:
            asset_id = r["asset_id"]
            asset_id_counts[asset_id] = asset_id_counts.get(asset_id, 0) + 1
        
        # Mettre à jour display_name si plusieurs positions du même produit
        for r in rows:
            if asset_id_counts.get(r["asset_id"], 0) > 1:
                r["display_name"] = f"{r['name']} ({r['contract_name']})"
        
        # Trier par nom
        rows.sort(key=lambda r: r["display_name"])
        
        # Préparer les données pour le tableau
        term_width = shutil.get_terminal_size().columns if hasattr(shutil, 'get_terminal_size') else 120
        
        headers = ["Nom", "Assureur", "Portefeuille", "Mois", "Achat", "Valeur", "Gain", "Perf", "Perf/an"]
        compact_headers = headers
        
        table_rows = []
        for r in rows:
            name = r["display_name"]
            insurer = r["insurer"] or "N/A"
            portfolio_name = r["portfolio_name"] or "N/A"
            months = r["months"]
            invested_amount = r["invested_amount"] or 0.0
            current_value = r["current_value"] or 0.0
            gain_amt = r["gain"]
            perf_amt = r["perf"]
            perf_annualized = r["perf_annualized"]
            is_sold = r["is_sold"]
            
            # Formater les valeurs
            buy_str = f"{invested_amount:,.2f} €" if isinstance(invested_amount, (int, float)) else "N/A"
            v_str = f"{current_value:,.2f} €" if isinstance(current_value, (int, float)) else "N/A"
            gain_str = f"{gain_amt:+,.2f} €" if isinstance(gain_amt, (int, float)) else "N/A"
            perf_str = f"{perf_amt:+.2f}%" if isinstance(perf_amt, (int, float)) else "N/A"
            perf_annualized_str = f"{perf_annualized:+.2f}%/an" if isinstance(perf_annualized, (int, float)) else "N/A"
            
            # Afficher "terminé" pour les positions rachetées
            if is_sold:
                perf_annualized_str = "terminé"
            
            months_str = str(months) if months is not None else "N/A"
            
            table_rows.append({
                "Nom": name,
                "Assureur": insurer,
                "Portefeuille": portfolio_name,
                "Mois": months_str,
                "Achat": buy_str,
                "Valeur": v_str,
                "Gain": gain_str,
                "Perf": perf_str,
                "Perf/an": perf_annualized_str,
            })
        
        # Alignements
        compact_aligns = {"Nom": "l", "Assureur": "l", "Portefeuille": "l", "Mois": "r", "Achat": "r", "Valeur": "r", "Gain": "r", "Perf": "r", "Perf/an": "r"}
        
        # Largeurs maximales
        other_caps = {
            "Assureur": 12,
            "Portefeuille": 5,
            "Mois": 4,
            "Achat": 15,
            "Valeur": 15,
            "Gain": 15,
            "Perf": 10,
            "Perf/an": 12,
        }
        sep_len = 2 * (len(compact_headers) - 1)
        fixed = sum(other_caps.values()) + sep_len
        name_cap = max(20, min(60, (term_width or 120) - fixed - 8))
        compact_max_widths = {"Nom": name_cap}
        
        # Fonction locale pour formater le tableau (identique à uc_view)
        def _truncate(s, max_len):
            if len(s) <= max_len:
                return s
            return s[:max(0, max_len - 3)] + "..."
        
        def _format_table(headers, data_rows, *, aligns=None, max_widths=None):
            """
            Render un tableau monospace lisible dans un terminal.
            - aligns: dict[col] -> 'l'|'r' (left/right)
            - max_widths: dict[col] -> int (cap de largeur, tronque avec "...")
            """
            aligns = aligns or {}
            max_widths = max_widths or {}
            
            # Convertir en matrice de strings
            matrix = []
            for r in data_rows:
                row = []
                for h in headers:
                    row.append("" if r.get(h) is None else str(r.get(h)))
                matrix.append(row)
            
            # Largeur auto, avec cap éventuel
            widths = []
            for i, h in enumerate(headers):
                col_vals = [h] + [matrix[j][i] for j in range(len(matrix))]
                w = max(len(v) for v in col_vals) if col_vals else len(h)
                cap = max_widths.get(h)
                if isinstance(cap, int) and cap > 0:
                    w = min(w, cap)
                widths.append(max(1, w))
            
            # Tronquer selon widths
            for j in range(len(matrix)):
                for i, h in enumerate(headers):
                    matrix[j][i] = _truncate(matrix[j][i], widths[i])
            
            header_cells = [_truncate(h, widths[i]) for i, h in enumerate(headers)]
            
            def fmt_cell(h, i, val):
                if aligns.get(h) == "r":
                    return val.rjust(widths[i])
                return val.ljust(widths[i])
            
            header_line = "  ".join(fmt_cell(headers[i], i, header_cells[i]) for i in range(len(headers)))
            sep_line = "  ".join(("-" * widths[i]) for i in range(len(headers)))
            lines = [header_line, sep_line]
            for row in matrix:
                lines.append("  ".join(fmt_cell(headers[i], i, row[i]) for i in range(len(headers))))
            return "\n".join(lines)
        
        print(_format_table(compact_headers, table_rows, aligns=compact_aligns, max_widths=compact_max_widths))
        
        if not details:
            return
        
        # Afficher les détails pour chaque fonds euro
        print()
        for r in rows:
            asset = r["asset"]
            position = r["position"]
            result = r["result"]
            
            print(f"  • {r['display_name']}")
            
            # Informations de base
            print(f"     Souscription: {r['subscription_date']} | Assureur: {r['insurer']} | Contrat: {r['contract_name']}")
            
            # Frais payés
            fees_total = r["fees_total"]
            if fees_total and abs(float(fees_total)) > 0.01:
                print(f"     Frais payés: {fees_total:,.2f} €")
            
            # Statut racheté
            if r["is_sold"]:
                print(f"     Statut: terminé")
            
            # Message de statut depuis le résultat
            if result.message:
                print(f"     Info: {result.message}")
            
            print()
    
    @staticmethod
    def validate_data_dir(data_dir: Path) -> int:
        """
        Valide l'intégrité des données sans rien exécuter.
        
        Returns:
            0 si ok, 1 si erreurs
        """
        from .validation import validate_assets_file, validate_positions_file
        
        print("Validation du portefeuille...")
        print()
        
        assets_file = data_dir / "assets.yaml"
        validated_assets, assets_report = validate_assets_file(assets_file)
        
        positions_file = data_dir / "positions.yaml"
        valid_asset_ids = {a.asset_id for a in validated_assets}
        validated_positions, positions_report = validate_positions_file(positions_file, valid_asset_ids)
        
        print(assets_report.format_summary())
        print()
        print(positions_report.format_summary())
        print()
        
        total_errors = len(assets_report.errors) + len(positions_report.errors)
        total_warnings = len(assets_report.warnings) + len(positions_report.warnings)
        
        if total_errors > 0:
            print(f"❌ VALIDATION ÉCHOUÉE : {total_errors} erreur(s) bloquante(s)")
            return 1
        elif total_warnings > 0:
            print(f"⚠️  VALIDATION OK avec {total_warnings} warning(s)")
            return 0
        else:
            print("✓ VALIDATION OK : aucun problème détecté")
            return 0

def main():
    """Point d'entrée du CLI"""
    parser = argparse.ArgumentParser(
        description="Portfolio Tracker - Suivi patrimonial multi-actifs",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument(
        '--data-dir',
        type=str,
        default='portfolio_tracker/data',
        help='Chemin vers le dossier data/ (défaut: portfolio_tracker/data)'
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Commandes disponibles')
    
    # Commande: validate
    subparsers.add_parser('validate', help='Valide l\'intégrité des données (assets.yaml, positions.yaml)')
    
    # Commande: type
    type_parser = subparsers.add_parser('type', help='Affiche l\'état par type d\'actif')
    type_parser.add_argument(
        '--type',
        type=str,
        choices=['structured_product', 'fonds_euro', 'uc_fund', 'uc_illiquid'],
        help='Filtrer par type d\'actif'
    )
    
    # Commande: alerts
    alerts_parser = subparsers.add_parser('alerts', help='Affiche les alertes')
    alerts_parser.add_argument(
        '--severity',
        type=str,
        choices=['info', 'warning', 'error'],
        help='Filtrer par sévérité'
    )
    
    # Commande: list-assets
    subparsers.add_parser('list-assets', help='Liste tous les actifs')
    
    # Commande: list-positions
    subparsers.add_parser('list-positions', help='Liste toutes les positions')

    # Commande: uc (vue performance UC)
    # Commande: uc
    uc_parser = subparsers.add_parser('uc', help="Vue performance des unités de compte (VL achat, dernière VL, perf)")
    uc_parser.add_argument(
        '--include-terminated',
        action='store_true',
        help='Inclut les produits terminés (vendus) dans l\'affichage'
    )
    uc_parser.add_argument(
        '--details',
        action='store_true',
        help='Affiche les détails (VL achat, dernière VL, frais) pour chaque UC'
    )

    # Commande: set-purchase-nav
    spn = subparsers.add_parser(
        'set-purchase-nav',
        help="Renseigne/efface la VL d'achat d'une position UC (corrige la perf depuis achat).",
    )
    spn.add_argument('--position-id', type=str, help="ID de position (ex: pos_011)")
    spn.add_argument('--asset-id', type=str, help="ID d'asset UC (ex: uc_eleva_absolute_return_europe)")
    spn.add_argument('--value', type=float, help="VL d'achat (ex: 148.42)")
    spn.add_argument('--currency', type=str, default="EUR", help="Devise (défaut EUR)")
    spn.add_argument('--clear', action='store_true', help="Efface purchase_nav (repassera en 'estimée' si possible)")

    # Commande: add-uc-lot (achats multiples)
    lotp = subparsers.add_parser(
        'add-uc-lot',
        help="Ajoute un lot d'achat UC (date/quantité/cours/montant net/frais) à une position.",
    )
    lotp.add_argument('--position-id', type=str, required=True, help="ID de position (ex: pos_011)")
    lotp.add_argument('--date', type=str, required=True, help="Date d'achat (ISO, ex: 2025-09-04)")
    lotp.add_argument('--type', type=str, default="buy", help="Type de lot: buy|fee|tax|income|sell|other (défaut buy)")
    lotp.add_argument('--units', type=float, required=True, help="Quantité achetée (ex: 43.7946)")
    lotp.add_argument('--nav', type=float, help="Cours/VL (ex: 148.42)")
    lotp.add_argument('--net-amount', type=float, help="Montant net investi (après frais) (ex: 6500.00)")
    lotp.add_argument('--gross-amount', type=float, help="Montant brut (avant frais) (optionnel)")
    lotp.add_argument('--fees-amount', type=float, help="Frais en montant (optionnel)")
    lotp.add_argument('--currency', type=str, default="EUR", help="Devise (défaut EUR)")
    lotp.add_argument('--update-units-held', action='store_true', help="Met à jour units_held = somme des lots")

    # Commande: import-movements (générique)
    imp = subparsers.add_parser(
        "import-movements",
        help="Importe un export texte des mouvements (Generali/Swiss Life) et crée des lots dans positions.yaml. Peut être utilisé régulièrement (doublons ignorés automatiquement).",
    )
    imp.add_argument("--file", type=str, required=True, help="Chemin du fichier texte exporté/collé")
    imp.add_argument("--insurer", type=str, default="Generali", help="Assureur (défaut Generali)")
    imp.add_argument("--contract", type=str, default="HIMALIA", help="Nom du contrat (défaut HIMALIA)")
    imp.add_argument("--apply", action="store_true", help="Applique l'import (sinon dry-run)")
    imp.add_argument("--all-assets", action="store_true", help="Importe aussi les non-UC (par défaut UC uniquement)")
    imp.add_argument("--no-update-units-held", action="store_true", help="Ne recalcule pas units_held à partir des lots")
    imp.add_argument("--since", type=str, help="Date ISO (YYYY-MM-DD) - n'importer que les mouvements depuis cette date (utile pour imports incrémentaux)")

    # Alias pour compatibilité
    imp_alias = subparsers.add_parser(
        "import-himalia-movements",
        help="[ALIAS] Utilise 'import-movements' à la place. Importe un export texte des mouvements Generali HIMALIA.",
    )
    imp_alias.add_argument("--file", type=str, required=True, help="Chemin du fichier texte exporté/collé")
    imp_alias.add_argument("--insurer", type=str, default="Generali", help="Assureur (défaut Generali)")
    imp_alias.add_argument("--contract", type=str, default="HIMALIA", help="Nom du contrat (défaut HIMALIA)")
    imp_alias.add_argument("--apply", action="store_true", help="Applique l'import (sinon dry-run)")
    imp_alias.add_argument("--all-assets", action="store_true", help="Importe aussi les non-UC (par défaut UC uniquement)")
    imp_alias.add_argument("--no-update-units-held", action="store_true", help="Ne recalcule pas units_held à partir des lots")
    imp_alias.add_argument("--since", type=str, help="Date ISO (YYYY-MM-DD) - n'importer que les mouvements depuis cette date")

    # Commande: merge-positions
    mp = subparsers.add_parser(
        "merge-positions",
        help="Fusionne plusieurs positions d'un même asset dans une enveloppe (évite le double comptage).",
    )
    mp.add_argument("--asset-id", type=str, required=True, help="asset_id à fusionner (ex: fonds_euro_generali_aggv090)")
    mp.add_argument("--insurer", type=str, help="Filtre assureur (ex: Generali)")
    mp.add_argument("--contract", type=str, help="Filtre contrat (ex: HIMALIA)")
    mp.add_argument("--apply", action="store_true", help="Applique la fusion (sinon dry-run)")

    # Commande: update-underlyings
    upd_parser = subparsers.add_parser(
        'update-underlyings',
        help='Télécharge/stocke les séries des sous-jacents (market_data/underlyings.yaml)'
    )
    upd_parser.add_argument(
        '--headless',
        action='store_true',
        help='Utilise un navigateur headless (Playwright) en fallback quand nécessaire (optionnel)'
    )

    # Commande: structured
    
    # Commande: update-uc-navs (tâche quotidienne)
    upd_uc_parser = subparsers.add_parser(
        'update-uc-navs',
        help="Enregistre la VL du jour pour les UC (manuel via --set).",
    )
    upd_uc_parser.add_argument(
        '--date',
        type=str,
        help="Date cible (ISO, ex: 2025-12-27). Défaut: aujourd'hui",
    )
    upd_uc_parser.add_argument(
        '--set',
        action='append',
        default=[],
        help="Valeur manuelle: --set uc_asset_id=123.45 (répétable)",
    )
    upd_uc_parser.add_argument(
        '--headless',
        action='store_true',
        help="Force l'utilisation d'un navigateur headless (Playwright) pour récupérer les VL web",
    )
    structured_parser = subparsers.add_parser(
        'structured',
        help='Vue des produits structurés (mois écoulés, valeur, strike, prochaine constatation)'
    )
    structured_parser.add_argument(
        '--wide',
        action='store_true',
        help='Affiche toutes les colonnes (peut être large / wrap en terminal)'
    )
    structured_parser.add_argument(
        '--details',
        action='store_true',
        help='Affiche les lignes de détails (sous-jacent/initial/seuil) pour chaque produit'
    )
    structured_parser.add_argument(
        '--include-terminated',
        action='store_true',
        help='Inclut les produits terminés (vendus) dans l\'affichage'
    )
    
    # Commande: fonds-euro
    fonds_euro_parser = subparsers.add_parser(
        'fonds-euro',
        help='Vue des fonds euros (capital investi, valeur actuelle, taux déclarés)'
    )
    fonds_euro_parser.add_argument(
        '--wide',
        action='store_true',
        help='Affiche toutes les colonnes (peut être large / wrap en terminal)'
    )
    fonds_euro_parser.add_argument(
        '--details',
        action='store_true',
        help='Affiche les détails (taux déclarés par année, frais) pour chaque fonds euro'
    )
    fonds_euro_parser.add_argument(
        '--include-terminated',
        action='store_true',
        help='Inclut les fonds euros terminés (rachat total) dans l\'affichage'
    )
    
    # Commande: global
    global_parser = subparsers.add_parser(
        'global',
        help='Vue globale : affiche fonds euros, UC, produits structurés, puis un récapitulatif par portefeuille et par type'
    )
    global_parser.add_argument(
        '--wide',
        action='store_true',
        help='Affiche toutes les colonnes'
    )
    global_parser.add_argument(
        '--details',
        action='store_true',
        help='Affiche les détails pour chaque produit'
    )
    global_parser.add_argument(
        '--include-terminated',
        action='store_true',
        help='Inclut les produits terminés (vendus) dans l\'affichage'
    )
    global_parser.add_argument(
        '--portfolio',
        type=str,
        help='Filtre par nom de portefeuille (ex: HIMAL, Swiss). Affiche uniquement le récapitulatif pour ce portefeuille.'
    )
    
    # Commande: advice
    advice_parser = subparsers.add_parser(
        'advice',
        help='Génère des recommandations IA pour le portefeuille basées sur les profils de risque'
    )
    advice_parser.add_argument(
        '--profile',
        type=str,
        help='Nom du profil à analyser (ex: HIMALIA, SwissLife). Si non spécifié, analyse tous les profils.'
    )
    advice_parser.add_argument(
        '--all',
        action='store_true',
        help='Analyse tous les profils disponibles'
    )
    advice_parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Affiche le prompt qui serait envoyé sans appeler l\'API OpenRouter'
    )
    advice_parser.add_argument(
        '--interactive',
        action='store_true',
        help='Active le mode conversationnel après les recommandations pour poser des questions'
    )
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    # Initialiser le CLI
    try:
        cli = PortfolioCLI(args.data_dir)
    except FileNotFoundError as e:
        print(f"Erreur: {e}")
        return
    except Exception as e:
        print(f"Erreur lors de l'initialisation: {e}")
        return
    
    # Exécuter la commande
    try:
        if args.command == 'validate':
            # Validation sans charger le portfolio complet
            exit_code = PortfolioCLI.validate_data_dir(Path(args.data_dir))
            return exit_code
        elif args.command == 'type':
            cli.status_by_asset_type(args.type)
        elif args.command == 'alerts':
            cli.alerts(args.severity)
        elif args.command == 'list-assets':
            cli.list_assets()
        elif args.command == 'list-positions':
            cli.list_positions()
        elif args.command == 'uc':
            cli.uc_view(
                include_terminated=getattr(args, 'include_terminated', False),
                details=bool(getattr(args, 'details', False))
            )
        elif args.command == 'set-purchase-nav':
            cli.set_purchase_nav(
                position_id=getattr(args, "position_id", None),
                asset_id=getattr(args, "asset_id", None),
                value=getattr(args, "value", None),
                currency=str(getattr(args, "currency", "EUR") or "EUR"),
                clear=bool(getattr(args, "clear", False)),
            )
        elif args.command == 'add-uc-lot':
            cli.add_uc_lot(
                position_id=str(getattr(args, "position_id")),
                lot_date=str(getattr(args, "date")),
                lot_type=str(getattr(args, "type", "buy") or "buy"),
                units=float(getattr(args, "units")),
                nav=getattr(args, "nav", None),
                net_amount=getattr(args, "net_amount", None),
                gross_amount=getattr(args, "gross_amount", None),
                fees_amount=getattr(args, "fees_amount", None),
                currency=str(getattr(args, "currency", "EUR") or "EUR"),
                update_units_held=bool(getattr(args, "update_units_held", False)),
            )
        elif args.command in ("import-movements", "import-himalia-movements"):
            cli.import_movements(
                file_path=str(getattr(args, "file")),
                insurer=str(getattr(args, "insurer", "Generali") or "Generali"),
                contract_name=str(getattr(args, "contract", "HIMALIA") or "HIMALIA"),
                dry_run=not bool(getattr(args, "apply", False)),
                only_uc=not bool(getattr(args, "all_assets", False)),
                update_units_held=not bool(getattr(args, "no_update_units_held", False)),
                since_date=getattr(args, "since", None),
            )
        elif args.command == "merge-positions":
            cli.merge_positions(
                asset_id=str(getattr(args, "asset_id")),
                insurer=getattr(args, "insurer", None),
                contract_name=getattr(args, "contract", None),
                dry_run=not bool(getattr(args, "apply", False)),
            )
        elif args.command == 'update-underlyings':
            cli.update_underlyings(headless=bool(getattr(args, "headless", False)))
        elif args.command == 'structured':
            cli.structured_products_view(
                wide=bool(getattr(args, "wide", False)),
                details=bool(getattr(args, "details", False)),
                include_terminated=getattr(args, 'include_terminated', False),
            )
        elif args.command == 'fonds-euro':
            cli.fonds_euro_view(
                wide=bool(getattr(args, "wide", False)),
                details=bool(getattr(args, "details", False)),
                include_terminated=getattr(args, 'include_terminated', False),
            )
        elif args.command == 'global':
            cli.global_view(
                wide=bool(getattr(args, "wide", False)),
                details=bool(getattr(args, "details", False)),
                include_terminated=getattr(args, 'include_terminated', False),
                portfolio_name=getattr(args, 'portfolio', None),
            )
        elif args.command == 'update-uc-navs':
            cli.update_uc_navs(
                target_date=getattr(args, "date", None),
                set_values=list(getattr(args, "set", []) or []),
                headless=bool(getattr(args, "headless", False)),
            )
        elif args.command == 'advice':
            cli.advice(
                profile_name=getattr(args, "profile", None),
                all_profiles=bool(getattr(args, "all", False)),
                dry_run=bool(getattr(args, "dry_run", False)),
                interactive=bool(getattr(args, "interactive", False)),
            )
    except Exception as e:
        print(f"Erreur lors de l'exécution: {e}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    main()


