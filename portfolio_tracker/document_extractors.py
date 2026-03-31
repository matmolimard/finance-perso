"""Post-ingestion hooks and document extraction logic for V2 GED."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def extract_structured_brochure_suggestions(text: str, filename: str = "") -> dict[str, Any]:
    """Extract structured product metadata from brochure PDF text.

    Returns a dict of suggested fields for structured_product_rules,
    never persisted automatically — the user decides what to keep.
    """
    blob = f"{filename}\n{text}"

    isin_match = re.search(r"\b(FR[A-Z0-9]{10}|LU[A-Z0-9]{10})\b", blob)
    isin = isin_match.group(1) if isin_match else None

    coupon_rate_match = re.search(
        r"coupon[^0-9]{0,40}?(\d{1,2}[.,]\d{1,4})\s*%", blob, re.IGNORECASE
    )
    coupon_rate = coupon_rate_match.group(1).replace(",", ".") if coupon_rate_match else None

    frequency = None
    freq_blob = blob.lower()
    if "trimestriel" in freq_blob:
        frequency = "trimestriel"
    elif "semestriel" in freq_blob:
        frequency = "semestriel"
    elif "annuel" in freq_blob:
        frequency = "annuel"
    elif "mensuel" in freq_blob:
        frequency = "mensuel"

    has_autocall = bool(re.search(r"autocall|remboursement\s+anticip", blob, re.IGNORECASE))
    has_memory = bool(re.search(r"m[ée]moire|memory", blob, re.IGNORECASE))
    has_barrier = bool(re.search(r"barri[èe]re|barrier", blob, re.IGNORECASE))

    barrier_pct_match = re.search(
        r"barri[èe]re[^0-9]{0,40}?(\d{2,3}[.,]?\d{0,2})\s*%", blob, re.IGNORECASE
    )
    barrier_pct = barrier_pct_match.group(1).replace(",", ".") if barrier_pct_match else None

    maturity_match = re.search(
        r"(?:[ée]ch[ée]ance|maturit[ée])[^0-9]{0,30}?(\d{1,2}[/.-]\d{1,2}[/.-]\d{4})", blob, re.IGNORECASE
    )
    maturity_date = maturity_match.group(1) if maturity_match else None

    coupon_payment_mode = None
    if has_memory:
        coupon_payment_mode = "memory"
    elif "in fine" in blob.lower() or "in-fine" in blob.lower():
        coupon_payment_mode = "in_fine"
    elif coupon_rate:
        coupon_payment_mode = "periodic"

    suggestions: dict[str, Any] = {}
    if isin:
        suggestions["isin_override"] = isin
    if coupon_rate:
        suggestions["coupon_rule_summary"] = f"Coupon {coupon_rate}%"
        if frequency:
            suggestions["coupon_rule_summary"] += f" {frequency}"
    if coupon_payment_mode:
        suggestions["coupon_payment_mode"] = coupon_payment_mode
    if frequency:
        suggestions["coupon_frequency"] = frequency
    if has_autocall:
        suggestions["autocall_rule_summary"] = "Mécanisme de remboursement anticipé détecté"
    if has_barrier and barrier_pct:
        capital_summary = f"Barrière de protection à {barrier_pct}%"
        suggestions["capital_rule_summary"] = capital_summary
    elif has_barrier:
        suggestions["capital_rule_summary"] = "Barrière de protection détectée"
    if maturity_date:
        suggestions.setdefault("notes", "")
        suggestions["notes"] = f"Échéance détectée : {maturity_date}"

    return {
        "suggestions": suggestions,
        "extracted": {
            "isin": isin,
            "coupon_rate": coupon_rate,
            "frequency": frequency,
            "coupon_payment_mode": coupon_payment_mode,
            "has_autocall": has_autocall,
            "has_memory": has_memory,
            "has_barrier": has_barrier,
            "barrier_pct": barrier_pct,
            "maturity_date": maturity_date,
        },
    }


def run_post_ingest_hooks(
    data_dir: Path,
    document_entry: dict[str, Any],
    extracted_text: str,
) -> dict[str, Any]:
    """Route to type-specific extractors after a document is ingested.

    Returns extraction results (never modifies the document entry directly).
    """
    doc_type = str(document_entry.get("document_type") or "")
    results: dict[str, Any] = {"document_type": doc_type}

    if doc_type == "structured_brochure":
        results["structured_suggestions"] = extract_structured_brochure_suggestions(
            extracted_text,
            filename=str(document_entry.get("original_filename") or ""),
        )

    return results
