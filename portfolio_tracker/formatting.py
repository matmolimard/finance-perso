"""
Formatting de tableaux terminal — version unique et centralisée.

Remplace les 4 copies locales de _truncate / _format_table / fmt_cell dans cli.py.
"""
from typing import Dict, List, Optional


def truncate(s, max_len: int) -> str:
    """Tronque une chaîne à max_len caractères, avec '...' si nécessaire."""
    s = "" if s is None else str(s)
    if max_len <= 0:
        return ""
    if len(s) <= max_len:
        return s
    if max_len <= 3:
        return s[:max_len]
    return s[:max_len - 3] + "..."


def format_table(
    headers: List[str],
    data_rows: List[Dict[str, str]],
    *,
    aligns: Optional[Dict[str, str]] = None,
    max_widths: Optional[Dict[str, int]] = None,
) -> str:
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
        row = [str(r.get(h) or "") for h in headers]
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
            matrix[j][i] = truncate(matrix[j][i], widths[i])

    header_cells = [truncate(h, widths[i]) for i, h in enumerate(headers)]

    def fmt_cell(h, i, val):
        if aligns.get(h) == "r":
            return val.rjust(widths[i])
        return val.ljust(widths[i])

    header_line = "  ".join(fmt_cell(headers[i], i, header_cells[i]) for i in range(len(headers)))
    sep_line = "  ".join("-" * widths[i] for i in range(len(headers)))
    lines = [header_line, sep_line]
    for row in matrix:
        lines.append("  ".join(fmt_cell(headers[i], i, row[i]) for i in range(len(headers))))
    return "\n".join(lines)
