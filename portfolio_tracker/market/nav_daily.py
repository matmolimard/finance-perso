"""
NAV Daily - utilitaires pour la mise à jour quotidienne des VL UC
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional, Tuple

from ..core.asset import Asset, AssetType
from ..core.portfolio import Portfolio
from .nav_store import NavPoint, upsert_nav_point
from .nav_fetch import fetch_nav_for_asset_id
from .quantalys import QuantalysProvider


@dataclass(frozen=True)
class NavUpdateResult:
    asset_id: str
    target_date: date
    status: str  # ok|skipped|error
    message: str
    changed: bool = False


def _parse_set_values(set_pairs: Optional[List[str]]) -> Dict[str, float]:
    """
    Parse des arguments de type:
      ["uc_x=123.45", "uc_y=67.89"]
    """
    out: Dict[str, float] = {}
    if not set_pairs:
        return out
    for raw in set_pairs:
        if "=" not in str(raw):
            raise ValueError(f"--set invalide (attendu asset_id=value): {raw!r}")
        k, v = str(raw).split("=", 1)
        k = k.strip()
        v = v.strip().replace(",", ".")
        if not k:
            raise ValueError(f"--set invalide (asset_id vide): {raw!r}")
        out[k] = float(v)
    return out


def update_uc_navs(
    *,
    portfolio: Portfolio,
    market_data_dir,
    target_date: date,
    set_values: Optional[List[str]] = None,
    init_purchase_nav_in_history: bool = True,
    persist_positions_purchase_nav: bool = False,
    headless: bool = False,
) -> Tuple[List[NavUpdateResult], bool]:
    """
    Met à jour les VL du jour pour toutes les UC détenues.

    - Si `--set uc_id=value` est fourni, on utilise ces valeurs.
    - On peut initialiser l'historique avec la VL d'achat (à la date de souscription) si disponible.
    - On peut persister `investment.purchase_nav` dans positions.yaml si manquant et déductible (investi/parts).

    Returns:
      (results, positions_changed)
    """
    set_map = _parse_set_values(set_values)
    results: List[NavUpdateResult] = []
    positions_changed = False
    
    # Initialiser le provider Quantalys pour sauvegarder les notes
    quantalys_provider = QuantalysProvider(market_data_dir)

    # Positions UC = asset_type UC_* + moteur mark_to_market
    # On ne met à jour qu'une fois par asset_id (plusieurs positions peuvent détenir la même UC).
    # On ignore les actifs historiques (vendus).
    seen_assets = set()
    for pos in portfolio.list_all_positions():
        asset = portfolio.get_asset(pos.asset_id)
        if not asset:
            continue
        if asset.asset_type not in {AssetType.UC_FUND, AssetType.UC_ILLIQUID}:
            continue
        if asset.valuation_engine.value != "mark_to_market":
            continue
        if asset.asset_id in seen_assets:
            continue
        # Ignorer les actifs historiques (vendus)
        if asset.metadata.get("status") == "historical":
            continue
        seen_assets.add(asset.asset_id)

        # 1) compléter purchase_nav si manquant et calculable
        if pos.investment.purchase_nav is None and pos.investment.invested_amount and pos.investment.units_held:
            try:
                if float(pos.investment.units_held) != 0:
                    pos.investment.purchase_nav = float(pos.investment.invested_amount) / float(pos.investment.units_held)
                    pos.investment.purchase_nav_source = "derived"
                    positions_changed = True
            except Exception:
                pass

        # 2) initialiser l'historique à la date d'achat (optionnel)
        if init_purchase_nav_in_history and pos.investment.purchase_nav is not None:
            try:
                _, changed = upsert_nav_point(
                    market_data_dir=market_data_dir,
                    identifier=asset.asset_id,
                    point=NavPoint(point_date=pos.investment.subscription_date, value=float(pos.investment.purchase_nav), currency=str(pos.investment.purchase_nav_currency or "EUR"), source="purchase_nav"),
                )
                # pas de result pour ça: c'est une init silencieuse
                _ = changed
            except Exception as e:
                results.append(
                    NavUpdateResult(
                        asset_id=asset.asset_id,
                        target_date=target_date,
                        status="error",
                        message=f"Erreur init purchase_nav dans l'historique: {e}",
                    )
                )
                continue

        # 3) valeur du jour:
        # - si --set fourni -> manuel
        # - sinon -> auto via market_data/nav_sources.yaml (si configuré)
        nav_value = None
        nav_date_for_point = target_date
        nav_source = "manual"
        if asset.asset_id in set_map:
            nav_value = float(set_map[asset.asset_id])
        else:
            fetched = fetch_nav_for_asset_id(
                market_data_dir=market_data_dir,
                asset_id=asset.asset_id,
                target_date=target_date,
                force_headless=headless,
            )
            if fetched is not None:
                nav_value = fetched.value
                nav_date_for_point = fetched.nav_date
                nav_source = fetched.source or "auto"
            else:
                results.append(
                    NavUpdateResult(
                        asset_id=asset.asset_id,
                        target_date=target_date,
                        status="skipped",
                        message="VL du jour manquante (configure market_data/nav_sources.yaml ou utilise --set uc_id=value)",
                        changed=False,
                    )
                )
                continue

        try:
            nav_val = float(nav_value)
            _, changed = upsert_nav_point(
                market_data_dir=market_data_dir,
                identifier=asset.asset_id,
                point=NavPoint(point_date=nav_date_for_point, value=nav_val, currency="EUR", source=nav_source),
            )
            
            # Si on a récupéré des infos Quantalys, les sauvegarder
            if fetched is not None and hasattr(asset, 'isin') and asset.isin:
                if fetched.quantalys_rating is not None or fetched.quantalys_category is not None:
                    try:
                        rating_changed = quantalys_provider.upsert_rating(
                            isin=asset.isin,
                            name=asset.name,
                            rating=fetched.quantalys_rating,
                            category=fetched.quantalys_category,
                            update_date=nav_date_for_point
                        )
                        if rating_changed:
                            rating_msg = f" + Note Quantalys: {fetched.quantalys_rating or 'N/A'}"
                        else:
                            rating_msg = ""
                    except Exception as e_rating:
                        rating_msg = f" (note Quantalys non sauvegardée: {e_rating})"
                else:
                    rating_msg = ""
            else:
                rating_msg = ""
            
            results.append(
                NavUpdateResult(
                    asset_id=asset.asset_id,
                    target_date=target_date,
                    status="ok",
                    message=f"VL enregistrée ({nav_source}) {nav_date_for_point.isoformat()}: {nav_val}{rating_msg}",
                    changed=changed,
                )
            )
        except Exception as e:
            results.append(
                NavUpdateResult(
                    asset_id=asset.asset_id,
                    target_date=target_date,
                    status="error",
                    message=f"Erreur enregistrement VL: {e}",
                    changed=False,
                )
            )

    if positions_changed and persist_positions_purchase_nav:
        portfolio.save_positions()

    return results, positions_changed


