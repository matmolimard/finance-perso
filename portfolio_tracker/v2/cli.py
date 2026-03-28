"""CLI V2 principal du projet."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ..web.app import run_web_app
from .bootstrap import bootstrap_v2_data
from .dashboard import build_v2_dashboard_data
from .market_actions import backfill_v2_market_history, update_v2_uc_navs, update_v2_underlyings
from .runtime import V2Runtime
from .structured_summary import build_structured_summary_rows


def _default_data_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "data"


def _print_status(data_dir: Path) -> int:
    payload = build_v2_dashboard_data(data_dir)
    overview = payload.get("overview") or {}
    print("Portfolio Tracker V2")
    print(f"Contrats: {overview.get('contracts_count', 0)}")
    print(f"Valeur actuelle: {overview.get('current_value', 0.0):.2f} EUR")
    print(f"Documents: {len(payload.get('documents') or [])}")
    print(f"Snapshots: {sum(len(rows) for rows in (payload.get('snapshots_by_contract') or {}).values())}")
    return 0


def _format_euro(value: Any) -> str:
    amount = float(value or 0.0)
    return f"{amount:,.2f} €"


def _format_pct(value: Any, *, signed: bool = True, suffix: str = "%") -> str:
    if value is None:
        return "-"
    amount = float(value)
    if signed:
        return f"{amount:+.2f}{suffix}"
    return f"{amount:.2f}{suffix}"


def _truncate(value: Any, width: int) -> str:
    text = "" if value is None else str(value)
    if len(text) <= width:
        return text
    if width <= 3:
        return text[:width]
    return text[: width - 3] + "..."


def _pad(value: Any, width: int, *, align: str = "left") -> str:
    text = _truncate(value, width)
    return text.rjust(width) if align == "right" else text.ljust(width)


def _render_table(title: str, columns: list[dict[str, Any]], rows: list[dict[str, Any]]) -> str:
    widths = []
    for column in columns:
        header_width = len(column["header"])
        content_width = max((len(str(row.get(column["key"], ""))) for row in rows), default=0)
        widths.append(min(column.get("max_width", max(header_width, content_width)), max(header_width, content_width)))

    lines = [title, "=" * max(sum(widths) + (3 * (len(columns) - 1)), len(title))]
    header = "  ".join(
        _pad(column["header"], widths[index], align=column.get("align", "left"))
        for index, column in enumerate(columns)
    )
    separator = "  ".join("-" * width for width in widths)
    lines.extend([header, separator])

    for row in rows:
        lines.append(
            "  ".join(
                _pad(row.get(column["key"], ""), widths[index], align=column.get("align", "left"))
                for index, column in enumerate(columns)
            )
        )
    return "\n".join(lines)


def _print_structured_summary(data_dir: Path) -> int:
    runtime = V2Runtime(data_dir)
    rows = [
        {
            "name": row["name"],
            "portfolio": row["portfolio_name"],
            "purchase_date": row["subscription_date"] or "",
            "months": str(row["months"]),
            "next_obs": row["next_observation_date"] or "-",
            "redeem_today": row["redeem_if_today"],
            "coupon_pct": _format_pct(row["coupon_pct"], signed=False),
            "purchase_amount": _format_euro(row["invested_amount"]),
            "current_value": _format_euro(row["current_value"]),
            "gain": _format_euro(row["gain"]),
            "perf": _format_pct(row["perf"]),
            "perf_annualized": _format_pct(row["perf_annualized"], suffix="%/an"),
            "perf_if_strike_annualized": _format_pct(row["perf_if_strike_annualized"], suffix="%/an"),
            "value_if_strike": _format_euro(row["value_if_strike"]),
            "gain_if_strike": _format_euro(row["gain_if_strike"]),
            "perf_if_strike": _format_pct(row["perf_if_strike"]),
        }
        for row in build_structured_summary_rows(runtime)
    ]

    columns = [
        {"key": "name", "header": "Nom", "max_width": 34},
        {"key": "portfolio", "header": "Portefeuille", "max_width": 12},
        {"key": "purchase_date", "header": "Date achat", "align": "right", "max_width": 10},
        {"key": "months", "header": "Mois", "align": "right", "max_width": 4},
        {"key": "next_obs", "header": "Prochaine", "align": "right", "max_width": 10},
        {"key": "redeem_today", "header": "Remb. si ajd ?", "max_width": 14},
        {"key": "coupon_pct", "header": "Coupon %", "align": "right", "max_width": 8},
        {"key": "purchase_amount", "header": "Achat", "align": "right", "max_width": 14},
        {"key": "current_value", "header": "Valeur", "align": "right", "max_width": 14},
        {"key": "gain", "header": "Gain", "align": "right", "max_width": 14},
        {"key": "perf", "header": "Perf", "align": "right", "max_width": 10},
        {"key": "perf_annualized", "header": "Perf/an", "align": "right", "max_width": 12},
        {"key": "perf_if_strike_annualized", "header": "Perf si strike/an", "align": "right", "max_width": 18},
        {"key": "value_if_strike", "header": "Valeur si strike", "align": "right", "max_width": 16},
        {"key": "gain_if_strike", "header": "Gain si strike", "align": "right", "max_width": 16},
        {"key": "perf_if_strike", "header": "Perf si strike", "align": "right", "max_width": 15},
    ]
    print(_render_table("PRODUITS STRUCTURÉS - Synthèse", columns, rows))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Portfolio Tracker V2")
    parser.add_argument("--data-dir", type=Path, default=_default_data_dir())

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("global")
    subparsers.add_parser("status")
    subparsers.add_parser("structured")
    subparsers.add_parser("web-payload")
    subparsers.add_parser("bootstrap")

    web_parser = subparsers.add_parser("web")
    web_parser.add_argument("--host", default="127.0.0.1")
    web_parser.add_argument("--port", type=int, default=8765)

    nav_parser = subparsers.add_parser("update-uc-navs")
    nav_parser.add_argument("--target-date")
    nav_parser.add_argument("--set", dest="set_values", action="append")
    nav_parser.add_argument("--headless", action="store_true")
    nav_parser.add_argument("--include-historical", action="store_true")
    nav_parser.add_argument("--asset-id", dest="asset_ids", action="append")

    underlyings_parser = subparsers.add_parser("update-underlyings")
    underlyings_parser.add_argument("--years", type=int)
    underlyings_parser.add_argument("--headless", action="store_true")

    backfill_parser = subparsers.add_parser("backfill-market-history")
    backfill_parser.add_argument("--years", type=int, default=3)
    backfill_parser.add_argument("--headless", action="store_true")
    backfill_parser.add_argument("--include-historical", action="store_true")
    backfill_parser.add_argument("--asset-id", dest="asset_ids", action="append")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    data_dir = Path(args.data_dir)

    if args.command in {"global", "status"}:
        return _print_status(data_dir)
    if args.command == "structured":
        return _print_structured_summary(data_dir)
    if args.command == "web-payload":
        print(json.dumps(build_v2_dashboard_data(data_dir), ensure_ascii=False, indent=2))
        return 0
    if args.command == "bootstrap":
        print(json.dumps(bootstrap_v2_data(data_dir), ensure_ascii=False, indent=2))
        return 0
    if args.command == "web":
        run_web_app(data_dir, host=args.host, port=args.port)
        return 0
    if args.command == "update-uc-navs":
        print(
            json.dumps(
                update_v2_uc_navs(
                    data_dir,
                    target_date=args.target_date,
                    set_values=args.set_values,
                    headless=bool(args.headless),
                    include_historical=bool(args.include_historical),
                    asset_ids=args.asset_ids,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "update-underlyings":
        print(
            json.dumps(
                update_v2_underlyings(
                    data_dir,
                    years=args.years,
                    headless=bool(args.headless),
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "backfill-market-history":
        print(
            json.dumps(
                backfill_v2_market_history(
                    data_dir,
                    years=int(args.years or 3),
                    headless=bool(args.headless),
                    include_historical=bool(args.include_historical),
                    asset_ids=args.asset_ids,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    raise ValueError(f"Commande non supportée: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
