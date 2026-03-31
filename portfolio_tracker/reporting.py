"""Service de reporting annuel V2 par contrat et par exercice structuré.

Principes métier :
- Fonds euro   : valeur officielle assureur à Dec 31, gain = intérêts constatés
- UC           : valeur officielle assureur nette des structurés estimés, gain = perf marché
- Structurés   : valorisation moteur à Dec 31, PAS de gain_pct (produits à terme)
  → seule exception : realized_gain calculé à la date de remboursement effectif
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Optional

import yaml

from .bootstrap import ensure_v2_db
from .runtime import V2Runtime
from .storage import connect, default_db_path


# ─── Requêtes ledger ─────────────────────────────────────────────────────────

def _fetch_annual_ledger_flows(
    conn,
    contract_name: str,
) -> dict[tuple[int, str], dict[str, float]]:
    """Agrège les flux par (année fiscale, bucket) depuis contract_ledger_entries."""
    rows = conn.execute(
        """
        SELECT
            fiscal_year, bucket,
            SUM(CASE WHEN entry_kind = 'external_contribution' AND direction = 'credit'
                     THEN amount ELSE 0 END) AS ext_contributions,
            SUM(CASE WHEN entry_kind = 'withdrawal' AND direction = 'debit'
                     THEN amount ELSE 0 END) AS ext_withdrawals,
            SUM(CASE WHEN entry_kind = 'internal_transfer_in' AND direction = 'credit'
                     THEN amount ELSE 0 END) AS transfers_in,
            SUM(CASE WHEN entry_kind = 'internal_transfer_out' AND direction = 'debit'
                     THEN amount ELSE 0 END) AS transfers_out,
            SUM(CASE WHEN entry_kind = 'internal_credit' AND direction = 'credit'
                     THEN amount ELSE 0 END) AS credited_income,
            SUM(CASE WHEN entry_kind = 'fee' AND direction = 'debit'
                     THEN amount ELSE 0 END) AS fees,
            SUM(CASE WHEN entry_kind = 'tax' AND direction = 'debit'
                     THEN amount ELSE 0 END) AS taxes,
            SUM(CASE WHEN entry_kind = 'structured_redemption' AND direction = 'debit'
                     THEN amount ELSE 0 END) AS structured_redemptions
        FROM contract_ledger_entries
        WHERE contract_name = ?
        GROUP BY fiscal_year, bucket
        ORDER BY fiscal_year, bucket
        """,
        (contract_name,),
    ).fetchall()

    result: dict[tuple[int, str], dict[str, float]] = {}
    for row in rows:
        result[(int(row["fiscal_year"]), str(row["bucket"]))] = {
            "ext_contributions": float(row["ext_contributions"] or 0.0),
            "ext_withdrawals": float(row["ext_withdrawals"] or 0.0),
            "transfers_in": float(row["transfers_in"] or 0.0),
            "transfers_out": float(row["transfers_out"] or 0.0),
            "credited_income": float(row["credited_income"] or 0.0),
            "fees": float(row["fees"] or 0.0),
            "taxes": float(row["taxes"] or 0.0),
            "structured_redemptions": float(row["structured_redemptions"] or 0.0),
        }
    return result


def _fetch_snapshots_indexed(
    conn,
    contract_name: str,
) -> dict[int, dict[str, Any]]:
    """Snapshots officiels annuels indexés par année (clôture Dec 31)."""
    rows = conn.execute(
        """
        SELECT reference_date, official_total_value, official_uc_value,
               official_fonds_euro_value, official_euro_interest_net, status
        FROM annual_snapshots
        WHERE contract_name = ?
        ORDER BY reference_date
        """,
        (contract_name,),
    ).fetchall()
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        year = int(str(row["reference_date"])[:4])
        result[year] = {
            "reference_date": str(row["reference_date"]),
            "official_total_value": float(row["official_total_value"] or 0.0),
            # official_uc_value = total - fonds_euro (include les structurés dans le relevé)
            "official_uc_gross_value": float(row["official_uc_value"] or 0.0),
            "official_fonds_euro_value": float(row["official_fonds_euro_value"] or 0.0),
            "official_euro_interest_net": float(row["official_euro_interest_net"] or 0.0),
        }
    return result


def _load_event_validations(conn, asset_id: str) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        "SELECT event_key, validation_status, notes FROM structured_event_validations WHERE asset_id = ?",
        (asset_id,),
    ).fetchall()
    return {
        str(row["event_key"]): {
            "validation_status": str(row["validation_status"]),
            "notes": row["notes"],
        }
        for row in rows
    }


# ─── Calculs structurés (valorisation moteur à des dates de clôture) ─────────

def _structured_year_end_values(
    runtime: V2Runtime,
    contract_name: str,
    years: list[int],
) -> dict[int, float]:
    """Valeur totale estimée des structurés non liquidés à la clôture de chaque année."""
    positions = [
        p for p in runtime.portfolio.list_all_positions()
        if (p.wrapper.contract_name or "") == contract_name
    ]
    result: dict[int, float] = {}
    for year in years:
        ref_date = date(year, 12, 31)
        total = 0.0
        for position in positions:
            asset = runtime.portfolio.get_asset(position.asset_id)
            if asset is None or asset.asset_type.value != "structured_product":
                continue
            if position.investment.subscription_date > ref_date:
                continue
            if runtime.analytics_service.is_position_sold(position, valuation_date=ref_date):
                continue
            engine = runtime.engines.get(asset.valuation_engine)
            if engine is None:
                continue
            try:
                val = engine.valuate(asset, position, ref_date)
                total += float(val.current_value or 0.0)
            except Exception:
                pass
        result[year] = round(total, 2)
    return result


# ─── Lignes annuelles par contrat / bucket ────────────────────────────────────

def _empty_flows() -> dict[str, float]:
    return {
        "ext_contributions": 0.0,
        "ext_withdrawals": 0.0,
        "transfers_in": 0.0,
        "transfers_out": 0.0,
        "credited_income": 0.0,
        "fees": 0.0,
        "taxes": 0.0,
        "structured_redemptions": 0.0,
    }


def _annual_rows_for_contract(
    conn,
    contract_name: str,
    runtime: V2Runtime,
) -> list[dict[str, Any]]:
    flows = _fetch_annual_ledger_flows(conn, contract_name)
    snapshots = _fetch_snapshots_indexed(conn, contract_name)
    years_with_data = sorted({key[0] for key in flows} | set(snapshots.keys()))
    if not years_with_data:
        return []

    # Structurés estimés aux dates de clôture connues (snapshots uniquement)
    structured_at_year_end = _structured_year_end_values(
        runtime, contract_name, sorted(snapshots.keys())
    )

    # UC pure = total officiel - fonds euro - estimé structurés
    uc_pure_by_year: dict[int, float] = {}
    for year, snap in snapshots.items():
        structured_val = structured_at_year_end.get(year, 0.0)
        uc_pure_by_year[year] = round(
            max(snap["official_total_value"] - snap["official_fonds_euro_value"] - structured_val, 0.0),
            2,
        )

    rows: list[dict[str, Any]] = []
    for year in years_with_data:
        opening_snap = snapshots.get(year - 1)
        closing_snap = snapshots.get(year)

        for bucket in ("fonds_euro", "uc", "structured"):
            f = flows.get((year, bucket), _empty_flows())

            if bucket == "fonds_euro":
                opening_value: Optional[float] = (
                    opening_snap["official_fonds_euro_value"] if opening_snap else None
                )
                closing_value: Optional[float] = (
                    closing_snap["official_fonds_euro_value"] if closing_snap else None
                )
                opening_source = "insurer_statement"
                closing_source = "insurer_statement"
            elif bucket == "uc":
                opening_value = uc_pure_by_year.get(year - 1) if opening_snap else None
                closing_value = uc_pure_by_year.get(year) if closing_snap else None
                opening_source = "derived_statement_minus_structured_estimate"
                closing_source = "derived_statement_minus_structured_estimate"
            else:  # structured
                opening_value = structured_at_year_end.get(year - 1) if opening_snap else None
                closing_value = structured_at_year_end.get(year) if closing_snap else None
                opening_source = "engine_estimate"
                closing_source = "engine_estimate"

            gain: Optional[float] = None
            gain_pct: Optional[float] = None

            if opening_value is not None and closing_value is not None:
                net_flows_in = f["ext_contributions"] + f["transfers_in"]
                net_flows_out = (
                    f["ext_withdrawals"] + f["transfers_out"] + f["fees"] + f["taxes"]
                )
                if bucket == "structured":
                    net_flows_out += f["structured_redemptions"]
                gain = round(closing_value - opening_value - net_flows_in + net_flows_out, 2)

                # gain_pct : meaningful uniquement pour fonds_euro et UC
                # Les structurés à terme NE donnent PAS de gain_pct annuel
                if bucket in ("fonds_euro", "uc"):
                    denom = opening_value + f["ext_contributions"] + f["transfers_in"]
                    if denom > 1.0:
                        gain_pct = round(gain / denom * 100.0, 4)

            data_quality = "no_snapshot"
            if opening_snap and closing_snap:
                data_quality = "complete"
            elif opening_snap or closing_snap:
                data_quality = "partial"

            # Exclure les lignes sans données pertinentes
            has_flows = any(abs(v) > 0.01 for v in f.values())
            has_snaps = opening_value is not None or closing_value is not None
            if not has_flows and not has_snaps:
                continue

            rows.append({
                "contract_name": contract_name,
                "fiscal_year": year,
                "bucket": bucket,
                "opening_value": opening_value,
                "opening_value_source": opening_source if opening_value is not None else None,
                "closing_value": closing_value,
                "closing_value_source": closing_source if closing_value is not None else None,
                "ext_contributions": f["ext_contributions"],
                "ext_withdrawals": f["ext_withdrawals"],
                "transfers_in": f["transfers_in"],
                "transfers_out": f["transfers_out"],
                "credited_income": f["credited_income"],
                "fees": f["fees"],
                "taxes": f["taxes"],
                "structured_redemptions": f["structured_redemptions"],
                "gain": gain,
                "gain_pct": gain_pct,
                "data_quality": data_quality,
            })

    return rows


# ─── Exercices annuels par produit structuré ──────────────────────────────────

def _coupon_events_for_year(events: list[dict[str, Any]], year: int) -> list[dict[str, Any]]:
    result = []
    for event in events:
        event_date_str = str(event.get("date") or "")
        if not event_date_str:
            continue
        try:
            event_year = int(event_date_str[:4])
        except ValueError:
            continue
        if event_year == year and str(event.get("type", "")).startswith("coupon"):
            result.append(event)
    return result


def _enrich_coupon_events(
    events_list: list[dict[str, Any]],
    invested_amount: float,
    validations: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    enriched = []
    for ev in events_list:
        rate = float(ev.get("amount") or 0.0)
        abs_amount = round(rate * invested_amount, 2) if rate and invested_amount else None
        event_key = (
            f"{ev.get('type')}::{ev.get('date')}::{(ev.get('description') or '')[:60]}"
        )
        validation = validations.get(event_key, {})
        enriched.append({
            **ev,
            "abs_amount": abs_amount,
            "is_conditional": bool((ev.get("metadata") or {}).get("coupon_condition")),
            "event_key": event_key,
            "validation_status": validation.get("validation_status", "unknown"),
            "validation_notes": validation.get("notes"),
        })
    return enriched


def _exercises_for_structured_position(
    runtime: V2Runtime,
    conn,
    data_dir: Path,
    position,
) -> list[dict[str, Any]]:
    asset = runtime.portfolio.get_asset(position.asset_id)
    if asset is None:
        return []
    if str((asset.metadata or {}).get("status") or "").lower() == "historical":
        return []

    lots = list(position.investment.lots or [])
    # invested_amount depuis les lots buy (position.investment.invested_amount peut être 0
    # quand le champ n'est pas renseigné dans le YAML)
    invested_amount = sum(
        float(lot.get("net_amount") or 0.0)
        for lot in lots
        if str(lot.get("type", "")).lower() == "buy"
        and float(lot.get("net_amount") or 0.0) > 0
    )
    if invested_amount == 0.0:
        invested_amount = float(position.investment.invested_amount or 0.0)
    sub_date = position.investment.subscription_date
    sell_date = runtime.extract_sell_date_from_lots(lots)
    sell_value = runtime.extract_sell_value_from_lots(lots)
    engine = runtime.engines.get(asset.valuation_engine)

    events_file = data_dir / "market_data" / f"events_{asset.asset_id}.yaml"
    event_payload: dict[str, Any] = {}
    if events_file.exists():
        event_payload = yaml.safe_load(events_file.read_text(encoding="utf-8")) or {}
    confirmed_events = list(event_payload.get("events") or [])
    expected_events = list(event_payload.get("expected_events") or [])

    validations = _load_event_validations(conn, asset.asset_id)

    today = date.today()
    start_year = sub_date.year
    end_year = sell_date.year if sell_date else today.year

    exercises: list[dict[str, Any]] = []
    for year in range(start_year, end_year + 1):
        year_end = date(year, 12, 31)
        effective_ref_date = min(year_end, today)

        subscribed_this_year = sub_date.year == year
        redeemed_this_year = sell_date is not None and sell_date.year == year

        if redeemed_this_year:
            position_status = (
                "subscribed_and_redeemed_this_year" if subscribed_this_year else "redeemed_this_year"
            )
        elif subscribed_this_year:
            position_status = "subscribed_this_year"
        else:
            position_status = "active"

        opening_invested: Optional[float] = None if subscribed_this_year else invested_amount
        closing_invested: Optional[float] = 0.0 if redeemed_this_year else invested_amount

        # Valorisation de clôture
        closing_valuation: Optional[float] = None
        if redeemed_this_year:
            closing_valuation = abs(sell_value) if sell_value is not None else 0.0
        elif engine is not None:
            is_sold = runtime.analytics_service.is_position_sold(
                position, valuation_date=effective_ref_date
            )
            if not is_sold and sub_date <= effective_ref_date:
                try:
                    val = engine.valuate(asset, position, effective_ref_date)
                    closing_valuation = round(float(val.current_value or 0.0), 2)
                except Exception:
                    closing_valuation = None

        # Valorisation d'ouverture = clôture de l'année précédente
        opening_valuation: Optional[float] = None
        if not subscribed_this_year and year > start_year and engine is not None:
            prev_year_end = date(year - 1, 12, 31)
            is_sold_prev = runtime.analytics_service.is_position_sold(
                position, valuation_date=prev_year_end
            )
            if not is_sold_prev:
                try:
                    val = engine.valuate(asset, position, prev_year_end)
                    opening_valuation = round(float(val.current_value or 0.0), 2)
                except Exception:
                    opening_valuation = None

        # Coupons
        confirmed_this_year = _coupon_events_for_year(confirmed_events, year)
        expected_this_year = _coupon_events_for_year(expected_events, year)
        confirmed_with_amounts = _enrich_coupon_events(confirmed_this_year, invested_amount, validations)
        expected_with_amounts = _enrich_coupon_events(expected_this_year, invested_amount, validations)

        # Montant total coupons confirmés (None si conditionnel)
        total_confirmed: Optional[float] = None
        if confirmed_with_amounts:
            if all(c["abs_amount"] is not None for c in confirmed_with_amounts):
                total_confirmed = round(sum(c["abs_amount"] for c in confirmed_with_amounts), 2)

        # Gain réalisé (uniquement en cas de remboursement)
        realized_gain: Optional[float] = None
        if redeemed_this_year and sell_value is not None:
            realized_gain = round(abs(sell_value) - invested_amount, 2)

        # Gain latent (uniquement si position encore active)
        unrealized_gain: Optional[float] = None
        if not redeemed_this_year and closing_valuation is not None and closing_invested:
            unrealized_gain = round(closing_valuation - closing_invested, 2)

        exercises.append({
            "asset_id": asset.asset_id,
            "asset_name": asset.name,
            "isin": asset.isin,
            "contract_name": str(position.wrapper.contract_name or ""),
            "position_id": position.position_id,
            "fiscal_year": year,
            "subscription_date": sub_date.isoformat(),
            "redemption_date": sell_date.isoformat() if sell_date else None,
            "position_status": position_status,
            "opening_invested_amount": opening_invested,
            "closing_invested_amount": closing_invested,
            "opening_valuation": opening_valuation,
            "closing_valuation": closing_valuation,
            "closing_valuation_date": effective_ref_date.isoformat(),
            "coupons_confirmed_this_year": confirmed_with_amounts,
            "coupons_expected_this_year": expected_with_amounts,
            "total_coupons_confirmed_amount": total_confirmed,
            "redemption_amount": abs(sell_value) if redeemed_this_year and sell_value is not None else None,
            "realized_gain": realized_gain,
            "unrealized_gain": unrealized_gain,
            # NE JAMAIS afficher un taux de rendement annuel pour un structuré à terme
            # (sauf rendement total au dénouement, à calculer séparément si besoin)
            "annual_return_pct": None,
            "has_events_file": events_file.exists(),
            "note": (
                "Produit remboursé — gain réalisé calculé sur durée totale"
                if redeemed_this_year
                else "Valorisation estimée — pas de rendement annuel affiché pour les produits à terme"
            ),
        })

    return exercises


# ─── Points d'entrée publics ──────────────────────────────────────────────────

def build_annual_contract_report(
    data_dir: Path,
    db_path: Optional[Path] = None,
) -> dict[str, Any]:
    """Rapport annuel par contrat et par bucket (fonds_euro, uc, structured).

    Retourne :
    - rows         : toutes les lignes (contract_name, fiscal_year, bucket, ...)
    - by_contract  : dict[contract_name][fiscal_year][bucket] → row
    """
    data_dir = Path(data_dir)
    db_path = Path(db_path or default_db_path(data_dir))
    ensure_v2_db(data_dir, db_path=db_path)
    runtime = V2Runtime(data_dir, db_path=db_path)

    with connect(db_path) as conn:
        contract_names = [
            str(row["contract_name"])
            for row in conn.execute(
                "SELECT contract_name FROM contracts ORDER BY contract_name"
            ).fetchall()
        ]
        all_rows: list[dict[str, Any]] = []
        for contract_name in contract_names:
            all_rows.extend(_annual_rows_for_contract(conn, contract_name, runtime))

    by_contract: dict[str, dict[int, dict[str, Any]]] = {}
    for row in all_rows:
        by_contract.setdefault(row["contract_name"], {}).setdefault(
            row["fiscal_year"], {}
        )[row["bucket"]] = row

    return {
        "rows": all_rows,
        "by_contract": by_contract,
    }


def build_structured_exercises(
    data_dir: Path,
    db_path: Optional[Path] = None,
) -> list[dict[str, Any]]:
    """Exercices annuels par produit structuré : stock, flux, coupons, gains.

    Règles métier :
    - annual_return_pct est toujours None (produits à terme)
    - realized_gain est calculé uniquement à la date de remboursement
    - unrealized_gain est l'écart entre valorisation estimée et capital investi
    - Les produits marqués `metadata.status = historical` sont ignorés
    """
    data_dir = Path(data_dir)
    db_path = Path(db_path or default_db_path(data_dir))
    ensure_v2_db(data_dir, db_path=db_path)
    runtime = V2Runtime(data_dir, db_path=db_path)

    exercises: list[dict[str, Any]] = []
    with connect(db_path) as conn:
        for position in runtime.portfolio.list_all_positions():
            asset = runtime.portfolio.get_asset(position.asset_id)
            if asset is None or asset.asset_type.value != "structured_product":
                continue
            exercises.extend(
                _exercises_for_structured_position(runtime, conn, data_dir, position)
            )

    exercises.sort(
        key=lambda e: (e["contract_name"], e["asset_name"], e["fiscal_year"])
    )
    return exercises
