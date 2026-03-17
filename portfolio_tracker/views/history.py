"""
Vue historique — affichage des séries temporelles (NAV, sous-jacents, taux).
"""
from pathlib import Path
from datetime import datetime, date
from typing import Optional, List, Dict, Tuple

from ..formatting import format_table


def list_history_choices(market_data_dir: Path) -> List[Tuple[str, str]]:
    """
    Liste les séries disponibles pour history : (libellé affiché, terme de recherche).
    Ordre : NAV UC, puis sous-jacents, puis taux.
    """
    choices: List[Tuple[str, str]] = []
    for prefix, category in (
        ("nav_uc", "NAV UC"),
        ("underlying", "Sous-jacent"),
        ("rates", "Taux"),
    ):
        for f in sorted(market_data_dir.glob(f"{prefix}_*.yaml")):
            stem = f.stem
            short = stem.replace(f"{prefix}_", "", 1) if stem.startswith(prefix + "_") else stem
            label = f"{category} — {short.replace('_', ' ')}"
            choices.append((label, stem))
    return choices


def history_view(
    market_data_dir: Path,
    value: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    no_chart: bool = False,
    all_series: bool = False,
    chart_type: str = "line",
    chart_marker: str = "dot",
    chart_color: str = "green",
) -> None:
    """
    Affiche l'historique d'une série temporelle enregistrée dans market_data/.

    Si value est vide et pas --all : menu pour choisir la série.
    Si --all : affiche l'historique (et le graphe) pour toutes les séries (filtres date conservés).
    """
    import yaml
    import shutil as _shutil

    # ------------------------------------------------------------------
    # Menu ou liste des séries à afficher
    # ------------------------------------------------------------------
    choices = list_history_choices(market_data_dir)
    if not choices:
        print("Aucune série d'historique trouvée dans market_data/.")
        return

    if not value or not str(value).strip():
        if all_series:
            values_to_show = [stem for _, stem in choices]
        else:
            print("\n  Historique — Choisir une série :\n")
            for i, (label, _) in enumerate(choices, 1):
                print(f"    {i:2}. {label}")
            print(f"    {0:2}. Quitter")
            try:
                raw = input("\n  Numéro ou terme de recherche : ").strip()
            except EOFError:
                print("Annulé.")
                return
            if not raw:
                print("Annulé.")
                return
            if raw.isdigit():
                num = int(raw)
                if num == 0:
                    print("Annulé.")
                    return
                if 1 <= num <= len(choices):
                    value = choices[num - 1][1]
                else:
                    print(f"Choix invalide (1–{len(choices)} ou 0 pour quitter).")
                    return
            else:
                value = raw.replace("-", "_").replace(" ", "_")
            values_to_show = [value]
            print()
    else:
        values_to_show = [value]

    # ------------------------------------------------------------------
    # Validation des dates (une seule fois)
    # ------------------------------------------------------------------
    def _parse_opt_date(s: Optional[str], label: str) -> Optional[date]:
        if not s or not str(s).strip():
            return None
        try:
            return datetime.strptime(str(s).strip(), "%Y-%m-%d").date()
        except ValueError:
            print(f"Date invalide pour {label} : « {s} ». Utilisez le format AAAA-MM-JJ (ex. 2026-03-01).")
            raise SystemExit(1)

    from_d = _parse_opt_date(date_from, "--from")
    to_d = _parse_opt_date(date_to, "--to")

    for value in values_to_show:
        if len(values_to_show) > 1:
            print("\n" + "=" * 80)

        # ------------------------------------------------------------------
        # 1. Découverte du fichier correspondant
        # ------------------------------------------------------------------
        candidates: List[Path] = []
        search = value.lower().replace("-", "_").replace(" ", "_")

        for prefix in ("nav_uc", "underlying", "rates"):
            for f in sorted(market_data_dir.glob(f"{prefix}_*.yaml")):
                if search in f.stem.lower():
                    candidates.append(f)

        if not candidates:
            print(f"\nAucun fichier trouvé pour la recherche « {value} »")
            if len(values_to_show) > 1:
                continue
            print("Conseil : utilisez un terme présent dans le nom du fichier")
            print("  ex: bdl_rempart, MQDCA09P, CMS_EUR_10Y, eleva, dynastrat")
            return

        if len(candidates) > 1 and len(values_to_show) == 1:
            print(f"\n{len(candidates)} fichiers correspondent — affichage du premier.")
            print("Précisez le terme pour être plus sélectif. Fichiers trouvés :")
            for c in candidates:
                print(f"  • {c.name}")

        target_file = candidates[0]

        # ------------------------------------------------------------------
        # 2. Chargement et détection du type
        # ------------------------------------------------------------------
        with open(target_file, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)

        stem = target_file.stem.lower()
        if stem.startswith("nav_uc"):
            series_key = "nav_history"
            series_type = "NAV UC"
            value_label = "VL"
            unit_suffix = ""
        elif stem.startswith("underlying"):
            series_key = "history"
            series_type = "Sous-jacent"
            value_label = data.get("metric", "valeur").replace("_", " ").title()
            unit_suffix = ""
        else:
            series_key = "history"
            series_type = "Taux"
            value_label = "Taux"
            unit_suffix = "%" if data.get("units") == "pct" else ""

        raw_series = data.get(series_key, [])
        if not raw_series:
            print(f"Aucune donnée dans {target_file.name}")
            if len(values_to_show) > 1:
                continue
            return

        # Construire un dict date → source pour les NAV UC
        source_by_date: Dict[str, str] = {}
        if stem.startswith("nav_uc"):
            for entry in raw_series:
                src = entry.get("source", "")
                if src:
                    source_by_date[str(entry.get("date", ""))] = src

        # ------------------------------------------------------------------
        # 3. Filtres de date + tri chronologique
        # ------------------------------------------------------------------
        points: List[Tuple[date, float, str]] = []
        for entry in raw_series:
            try:
                d = datetime.strptime(str(entry["date"]), "%Y-%m-%d").date()
                v = float(entry["value"])
            except (KeyError, TypeError, ValueError):
                continue
            if from_d and d < from_d:
                continue
            if to_d and d > to_d:
                continue
            points.append((d, v, entry.get("currency", "")))

        points.sort(key=lambda x: x[0])

        if not points:
            print("Aucun point dans la plage de dates demandée.")
            if len(values_to_show) > 1:
                continue
            return

        first_val = points[0][1]
        last_val = points[-1][1]
        min_val = min(p[1] for p in points)
        max_val = max(p[1] for p in points)
        total_pct = (last_val / first_val - 1) * 100 if first_val else 0.0

        currency_display = ""
        if stem.startswith("nav_uc"):
            currencies = {p[2] for p in points if p[2]}
            currency_display = next(iter(currencies), "EUR")

        # ------------------------------------------------------------------
        # 4. En-tête
        # ------------------------------------------------------------------
        title_human = (
            target_file.stem
            .replace("nav_uc_", "")
            .replace("underlying_", "")
            .replace("rates_", "")
            .replace("_", " ")
            .upper()
        )
        term_width = _shutil.get_terminal_size().columns

        print()
        print("=" * min(term_width, 100))
        print(f"HISTORIQUE  ·  {series_type}  ·  {title_human}")
        print(f"Fichier : {target_file.name}")
        if data.get("notes"):
            print(f"Note    : {data['notes']}")
        if data.get("url"):
            print(f"URL     : {data['url']}")
        print("=" * min(term_width, 100))
        print()

        # ------------------------------------------------------------------
        # 5. Tableau
        # ------------------------------------------------------------------
        col_val = f"{value_label} ({currency_display})" if currency_display else value_label
        show_source = bool(source_by_date)
        headers = ["Date", col_val, "Δ préc.", "Δ départ"]
        if show_source:
            headers.append("Source")

        aligns = {"Date": "l", col_val: "r", "Δ préc.": "r", "Δ départ": "r", "Source": "l"}
        max_widths_table = {col_val: 14, "Δ préc.": 10, "Δ départ": 10, "Source": 18}

        table_rows: List[Dict[str, str]] = []
        prev_val = None
        for d, v, _ in points:
            val_str = f"{v:,.4f}{unit_suffix}"

            delta_prev_str = ""
            if prev_val is not None and prev_val != 0:
                dp = (v / prev_val - 1) * 100
                sign = "+" if dp >= 0 else ""
                delta_prev_str = f"{sign}{dp:.2f}%"

            delta_start_str = ""
            if first_val != 0:
                ds = (v / first_val - 1) * 100
                sign = "+" if ds >= 0 else ""
                delta_start_str = f"{sign}{ds:.2f}%"

            row: Dict[str, str] = {
                "Date": str(d),
                col_val: val_str,
                "Δ préc.": delta_prev_str,
                "Δ départ": delta_start_str,
            }
            if show_source:
                row["Source"] = source_by_date.get(str(d), "")

            table_rows.append(row)
            prev_val = v

        print(format_table(headers, table_rows, aligns=aligns, max_widths=max_widths_table))

        # ------------------------------------------------------------------
        # 6. Résumé
        # ------------------------------------------------------------------
        sign = "+" if total_pct >= 0 else ""
        print()
        print(
            f"Résumé : {len(points)} points  |"
            f"  Premier : {points[0][0]} = {first_val:,.4f}"
            f"  →  Dernier : {points[-1][0]} = {last_val:,.4f}"
            f"  |  Évolution : {sign}{total_pct:.2f}%"
            f"  |  Min : {min_val:,.4f}  Max : {max_val:,.4f}"
        )

        # ------------------------------------------------------------------
        # 7. Graphe (plotext si dispo, sinon ASCII)
        # ------------------------------------------------------------------
        if no_chart or len(points) < 2:
            print()
            if len(values_to_show) > 1:
                continue
            return

        x_vals = list(range(len(points)))
        y_vals = [p[1] for p in points]
        dates = [str(p[0]) for p in points]

        try:
            import plotext as plt
            plt.clf()
            n = len(dates)
            if chart_type == "bar":
                plt.bar(x_vals, y_vals, color=chart_color, marker=chart_marker)
                # Limiter l'axe Y aux données pour que les barres aient des hauteurs visibles
                y_min, y_max = min(y_vals), max(y_vals)
                margin = (y_max - y_min) * 0.05 if y_max > y_min else 1.0
                plt.ylim(max(0, y_min - margin), y_max + margin)
            else:
                plt.plot(x_vals, y_vals, color=chart_color, marker=chart_marker)
            plt.xlabel("Date")
            plt.ylabel("VL (EUR)")
            if n > 0:
                indices = [0, n // 2, n - 1] if n >= 3 else list(range(n))
                plt.xticks([x_vals[i] for i in indices], [dates[i] for i in indices])
            plt.theme("clear")
            plt.plotsize(80, 16)
            print()
            plt.show()
            if len(values_to_show) > 1:
                continue
            return
        except (ImportError, AttributeError, TypeError, Exception):
            pass

        # Fallback : ASCII
        chart_width = min(80, len(points))
        chart_height = 8
        if len(y_vals) > chart_width:
            step = len(y_vals) / chart_width
            sampled = [y_vals[int(i * step)] for i in range(chart_width)]
        else:
            sampled = y_vals

        v_min = min(sampled)
        v_max = max(sampled)
        v_range = v_max - v_min if v_max != v_min else 1.0
        normalized = [int((v - v_min) / v_range * (chart_height - 1)) for v in sampled]

        print()
        print("Graphe :")
        for row_idx in range(chart_height - 1, -1, -1):
            line_chars_loop: List[str] = []
            for col_idx, nv in enumerate(normalized):
                prev_nv = normalized[col_idx - 1] if col_idx > 0 else nv
                if nv == row_idx:
                    line_chars_loop.append("─")
                elif min(prev_nv, nv) < row_idx < max(prev_nv, nv):
                    line_chars_loop.append("│")
                else:
                    line_chars_loop.append(" ")
            if row_idx == chart_height - 1:
                label = f"{v_max:>10,.4f}"
            elif row_idx == 0:
                label = f"{v_min:>10,.4f}"
            else:
                label = " " * 10
            print(f"  {label} ┤{''.join(line_chars_loop)}")

        first_date = dates[0]
        last_date = dates[-1]
        half = chart_width // 2
        print(f"  {' ' * 12}{first_date:<{half}}{last_date:>{half}}")
        print()
