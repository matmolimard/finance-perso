"""Import minimal de données réelles dans le socle V2."""

from __future__ import annotations

from datetime import datetime
from functools import lru_cache
import json
from pathlib import Path
import re
from typing import Any
import unicodedata

from pypdf import PdfReader
import yaml

from .domain.movements import MovementNormalizer
from .runtime import V2Runtime
from .storage import connect, default_db_path, init_db, upsert_market_series_points, upsert_structured_events


CONTRACT_SEEDS: list[dict[str, Any]] = [
    {
        "contract_id": "83914927",
        "contract_name": "HIMALIA",
        "insurer": "Generali",
        "wrapper_type": "assurance_vie",
        "holder_type": "individual",
        "fiscal_applicability": "applicable",
        "status": "active",
        "notes": "Contrat personnel HIMALIA.",
    },
    {
        "contract_id": "0010645288001",
        "contract_name": "SwissLife Capi Stratégic Premium",
        "insurer": "SwissLife",
        "wrapper_type": "contrat_de_capitalisation",
        "holder_type": "holding",
        "fiscal_applicability": "not_applicable",
        "status": "active",
        "notes": "Contrat SwissLife détenu via holding.",
    },
]

BOOTSTRAP_DB_VERSION = "2026-03-30-pdf-only-v2"


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _to_amount(raw: str) -> float:
    cleaned = raw.replace("\xa0", " ").replace("€", "").strip()
    cleaned = cleaned.replace(" ", "").replace(",", ".")
    return float(cleaned)


def _parse_fr_number(raw: str) -> float:
    cleaned = str(raw or "").replace("\xa0", " ").replace("€", "").strip()
    cleaned = cleaned.replace(" ", "").replace(",", ".")
    return float(cleaned)


def _contract_seed_by_name(contract_name: str) -> dict[str, Any] | None:
    return next((seed for seed in CONTRACT_SEEDS if str(seed["contract_name"]) == str(contract_name)), None)


def _contract_wrapper_type(contract_name: str) -> str:
    seed = _contract_seed_by_name(contract_name)
    if seed is not None:
        return str(seed["wrapper_type"])
    low = str(contract_name or "").lower()
    if "capi" in low or "capitalisation" in low:
        return "contrat_de_capitalisation"
    return "assurance_vie"


def _position_holder_type(contract_holder_type: str) -> str:
    return "individual" if str(contract_holder_type or "").lower() == "individual" else "company"


_MARKET_YAML_MIGRATION_VERSION = "1"


def _migrate_market_yaml_to_sqlite(data_dir: Path, db_path: Path) -> None:
    """Migration one-shot : charge les YAML events_* et fonds_euro_* dans SQLite."""
    init_db(db_path)
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT meta_value FROM app_meta WHERE meta_key = 'market_yaml_migration_version'"
        ).fetchone()
        if row is not None and str(row["meta_value"]) == _MARKET_YAML_MIGRATION_VERSION:
            return

    market_data_dir = Path(data_dir) / "market_data"
    now = datetime.now().isoformat(timespec="seconds")

    # Migration des events_*.yaml → structured_events
    for events_file in sorted(market_data_dir.glob("events_*.yaml")):
        asset_id = events_file.stem[len("events_"):]
        try:
            data = _load_yaml(events_file)
            if not data:
                continue
            realized = [e for e in (data.get("events") or []) if isinstance(e, dict)]
            expected = [e for e in (data.get("expected_events") or []) if isinstance(e, dict)]
            if realized:
                upsert_structured_events(db_path, asset_id=asset_id, events=realized, is_expected=False)
            if expected:
                upsert_structured_events(db_path, asset_id=asset_id, events=expected, is_expected=True)
        except Exception:
            pass

    # Migration des fonds_euro_*.yaml → market_series_points (kind="fonds_euro_rate")
    for rates_file in sorted(market_data_dir.glob("fonds_euro_*.yaml")):
        asset_id = rates_file.stem[len("fonds_euro_"):]
        try:
            data = _load_yaml(rates_file)
            declared = data.get("declared_rates") or []
            if not declared:
                continue
            points = []
            for entry in declared:
                year = entry.get("year")
                rate = entry.get("rate")
                if year is None or rate is None:
                    continue
                points.append({
                    "date": f"{year}-12-31",
                    "value": float(rate),
                    "source": entry.get("source") or "declared",
                })
            if points:
                upsert_market_series_points(
                    db_path,
                    kind="fonds_euro_rate",
                    identifier=asset_id,
                    points=points,
                )
        except Exception:
            pass

    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO app_meta (meta_key, meta_value)
            VALUES ('market_yaml_migration_version', ?)
            ON CONFLICT(meta_key) DO UPDATE SET meta_value = excluded.meta_value
            """,
            (_MARKET_YAML_MIGRATION_VERSION,),
        )


def ensure_v2_db(data_dir: Path, db_path: Path | None = None) -> dict[str, Any]:
    data_dir = Path(data_dir)
    db_path = Path(db_path or default_db_path(data_dir))
    if not db_path.exists():
        return bootstrap_v2_data(data_dir, db_path=db_path)
    try:
        with connect(db_path) as conn:
            row = conn.execute(
                "SELECT meta_value FROM app_meta WHERE meta_key = 'bootstrap_version'"
            ).fetchone()
        if row is None or str(row["meta_value"] or "") != BOOTSTRAP_DB_VERSION:
            return bootstrap_v2_data(data_dir, db_path=db_path)
    except Exception:
        return bootstrap_v2_data(data_dir, db_path=db_path)
    _migrate_market_yaml_to_sqlite(data_dir, db_path)
    return {
        "ok": True,
        "db_path": str(db_path),
        "imported_at": datetime.now().isoformat(timespec="seconds"),
    }


@lru_cache(maxsize=256)
def _pdf_text_cached(path_str: str, mtime_ns: int, size: int) -> str:
    reader = PdfReader(path_str)
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _pdf_text(path: Path) -> str:
    resolved = Path(path).resolve()
    stat = resolved.stat()
    return _pdf_text_cached(str(resolved), int(stat.st_mtime_ns), int(stat.st_size))


def _normalize_pdf_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    normalized = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", "", normalized).lower()


def _match_amount(text: str, patterns: list[str]) -> float | None:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            return _to_amount(match.group(1))
    return None


def _match_amount_normalized(text: str, patterns: list[str]) -> float | None:
    normalized = _normalize_pdf_text(text)
    for pattern in patterns:
        match = re.search(pattern, normalized, re.IGNORECASE | re.MULTILINE)
        if match:
            return _to_amount(match.group(1))
    return None


def _parse_statement_snapshot(doc: dict[str, Any], data_dir: Path) -> dict[str, Any]:
    pdf_path = Path(data_dir) / str(doc["filepath"])
    text = _pdf_text(pdf_path)
    insurer = str(doc["insurer"])
    coverage_year = int(doc["coverage_year"])

    if insurer.lower().startswith("swiss"):
        total = _match_amount(
            text,
            [
                r"Montant\s*de\s*l['’]épargne\s*(?:\(\*\))?\s*([0-9 \xa0]+,\d{2})\s*€",
            ],
        )
        uc_value = _match_amount(
            text,
            [
                r"Epargne\s*investie\s*en\s*unités\s*de\s*compte\s*([0-9 \xa0]+,\d{2})\s*€",
            ],
        )
        euro_value = _match_amount(
            text,
            [
                r"Epargne\s*investie\s*sur\s*le\s*fonds\s*en\s*euros\s*([0-9 \xa0]+,\d{2})\s*€",
            ],
        )
        euro_interest = _match_amount(
            text,
            [
                r"titre\s*de\s*l['’]année\s*%s\s*([0-9 \xa0]+,\d{2})\s*€" % coverage_year,
            ],
        )
        if total is None:
            total = _match_amount_normalized(
                text,
                [
                    r"montantdel'?epargne(?:\(\*\))?([0-9]+,\d{2})",
                    r"valeurnettedevotreepargneselevea([0-9]+,\d{2})",
                ],
            )
        if uc_value is None:
            uc_value = _match_amount_normalized(
                text,
                [
                    r"epargneinvestieenunitesdecompte([0-9]+,\d{2})",
                ],
            )
        if euro_value is None:
            euro_value = _match_amount_normalized(
                text,
                [
                    r"epargneinvestiesurlefondseneuros([0-9]+,\d{2})",
                ],
            )
        if euro_interest is None:
            euro_interest = _match_amount_normalized(
                text,
                [
                    r"autitredelannee%s([0-9]+,\d{2})" % coverage_year,
                    r"lannee%s([0-9]+,\d{2})" % coverage_year,
                ],
            )
    else:
        total = _match_amount(
            text,
            [
                r"EPARGNE ATTEINTE DE VOTRE CONTRAT AU 31/12/%s\s*([0-9 \xa0]+,\d{2})\s*€"
                % coverage_year,
            ],
        )
        euro_match = re.search(
            r"Actif Général Generali Vie\s+31/12/%s\s+([0-9 \xa0]+,\d{2})\s*€\s+([0-9 \xa0]+,\d{2})\s*€"
            % coverage_year,
            text,
            re.IGNORECASE | re.MULTILINE,
        )
        euro_interest = _to_amount(euro_match.group(1)) if euro_match else None
        euro_value = _to_amount(euro_match.group(2)) if euro_match else None
        uc_value = round(float(total or 0) - float(euro_value or 0), 2) if total is not None and euro_value is not None else None

    if total is None:
        raise ValueError(f"Impossible d'extraire le snapshot du relevé {pdf_path.name}")

    return {
        "snapshot_id": f"{_slug(doc['contract_name'])}_{coverage_year}",
        "contract_name": doc["contract_name"],
        "coverage_year": coverage_year,
        "reference_date": f"{coverage_year}-12-31",
        "statement_date": doc.get("statement_date") or doc.get("document_date"),
        "source_document_id": doc["document_id"],
        "status": "proposed",
        "official_total_value": total,
        "official_uc_value": uc_value,
        "official_fonds_euro_value": euro_value,
        "official_euro_interest_net": euro_interest,
        "official_notes": f"Snapshot importé depuis le relevé assureur {Path(doc['filepath']).name}.",
    }


def _parse_ddmmyyyy(raw: str) -> str | None:
    match = re.search(r"(\d{2})/(\d{2})/(\d{4})", str(raw or ""))
    if not match:
        return None
    return f"{match.group(3)}-{match.group(2)}-{match.group(1)}"


def _normalize_tokens(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKD", text or "")
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    normalized = normalized.lower()
    normalized = re.sub(r"([a-z])(\d)", r"\1 \2", normalized)
    normalized = re.sub(r"(\d)([a-z])", r"\1 \2", normalized)
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    alias_map = {
        "rd": ["rendement"],
        "rend": ["rendement"],
        "rendt": ["rendement"],
        "distri": ["distribution"],
        "div": ["dividende"],
        "forf": ["coupon"],
        "forfaitaire": ["coupon"],
        "fix": ["coupon"],
        "fixe": ["coupon"],
        "fev": ["fevrier"],
        "dec": ["decembre"],
        "oct": ["octobre"],
        "avr": ["avril"],
        "jan": ["janvier"],
        "jun": ["juin"],
        "jui": ["juillet"],
        "jul": ["juillet"],
        "sep": ["septembre"],
        "nov": ["novembre"],
    }
    month_by_number = {
        "01": "janvier",
        "02": "fevrier",
        "03": "mars",
        "04": "avril",
        "05": "mai",
        "06": "juin",
        "07": "juillet",
        "08": "aout",
        "09": "septembre",
        "10": "octobre",
        "11": "novembre",
        "12": "decembre",
    }
    stop_words = {
        "fcp",
        "sicav",
        "fund",
        "funds",
        "de",
        "des",
        "du",
        "la",
        "le",
        "les",
        "eur",
        "euracc",
        "acc",
        "act",
        "b",
        "c",
        "p",
        "d",
    }
    expanded_tokens: list[str] = []
    for token in normalized.split():
        if not token or token in stop_words:
            continue
        if re.fullmatch(r"\d{4}", token):
            month = month_by_number.get(token[:2])
            year = token[2:]
            if month is not None:
                expanded_tokens.extend([month, f"20{year}"])
                continue
        if re.fullmatch(r"\d{2}", token) and 20 <= int(token) <= 39:
            expanded_tokens.append(f"20{token}")
            continue
        expanded_tokens.extend(alias_map.get(token, [token]))
    return [token for token in expanded_tokens if token and token not in stop_words]


def _match_snapshot_position_mapping(
    runtime: V2Runtime,
    *,
    contract_name: str,
    asset_name_raw: str,
    isin: str | None,
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    by_isin: dict[str, dict[str, Any]] = {}
    for position in runtime.portfolio.list_all_positions():
        if str(position.wrapper.contract_name or "") != contract_name:
            continue
        asset = runtime.portfolio.get_asset(position.asset_id)
        if asset is None:
            continue
        row = {
            "position_id": position.position_id,
            "asset_id": asset.asset_id,
            "asset_type": asset.asset_type.value,
            "asset_name": asset.name,
            "asset_isin": asset.isin,
        }
        candidates.append(row)
        if asset.isin:
            by_isin[str(asset.isin)] = row

    asset_candidates: list[dict[str, Any]] = []
    assets_by_isin: dict[str, dict[str, Any]] = {}
    for asset in runtime.portfolio.list_all_assets():
        row = {
            "position_id": None,
            "asset_id": asset.asset_id,
            "asset_type": asset.asset_type.value,
            "asset_name": asset.name,
            "asset_isin": asset.isin,
        }
        asset_candidates.append(row)
        if asset.isin:
            assets_by_isin[str(asset.isin)] = row

    if isin and isin in by_isin:
        return {
            "position_id": by_isin[isin]["position_id"],
            "asset_id": by_isin[isin]["asset_id"],
            "asset_type": by_isin[isin]["asset_type"],
            "match_source": "isin",
        }
    if isin and isin in assets_by_isin:
        return {
            "position_id": None,
            "asset_id": assets_by_isin[isin]["asset_id"],
            "asset_type": assets_by_isin[isin]["asset_type"],
            "match_source": "asset_isin",
        }

    target_tokens = set(_normalize_tokens(asset_name_raw))
    if not target_tokens:
        return {"position_id": None, "asset_id": None, "asset_type": None, "match_source": "none"}

    best: tuple[float, dict[str, Any]] | None = None
    for candidate in candidates:
        candidate_tokens = set(_normalize_tokens(str(candidate["asset_name"])))
        if not candidate_tokens:
            continue
        intersection = len(target_tokens & candidate_tokens)
        union = len(target_tokens | candidate_tokens)
        score = max(
            (intersection / union) if union else 0.0,
            (intersection / min(len(target_tokens), len(candidate_tokens)))
            if target_tokens and candidate_tokens
            else 0.0,
        )
        if best is None or score > best[0]:
            best = (score, candidate)
    if best is None or best[0] < 0.6:
        best_asset: tuple[float, dict[str, Any]] | None = None
        for candidate in asset_candidates:
            candidate_tokens = set(_normalize_tokens(str(candidate["asset_name"])))
            if not candidate_tokens:
                continue
            intersection = len(target_tokens & candidate_tokens)
            union = len(target_tokens | candidate_tokens)
            score = max(
                (intersection / union) if union else 0.0,
                (intersection / min(len(target_tokens), len(candidate_tokens)))
                if target_tokens and candidate_tokens
                else 0.0,
            )
            if best_asset is None or score > best_asset[0]:
                best_asset = (score, candidate)
        if best_asset is None or best_asset[0] < 0.6:
            return {"position_id": None, "asset_id": None, "asset_type": None, "match_source": "none"}
        return {
            "position_id": None,
            "asset_id": best_asset[1]["asset_id"],
            "asset_type": best_asset[1]["asset_type"],
            "match_source": "asset_name_similarity",
        }
    return {
        "position_id": best[1]["position_id"],
        "asset_id": best[1]["asset_id"],
        "asset_type": best[1]["asset_type"],
        "match_source": "name_similarity",
    }


def _parse_swisslife_snapshot_positions(text: str, *, coverage_year: int) -> list[dict[str, Any]]:
    lines = [str(line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]

    start_idx = next(
        (idx for idx, line in enumerate(lines) if "Information relative à votre épargne investie en unités de compte" in line),
        None,
    )
    if start_idx is None:
        return []

    total_idx = next(
        (idx for idx in range(start_idx, len(lines)) if lines[idx].startswith("TOTAL ")),
        None,
    )
    if total_idx is None:
        return []

    positions: list[dict[str, Any]] = []
    i = start_idx + 1
    while i < total_idx:
        line = lines[i]
        if re.fullmatch(r"[A-Z]{2}[A-Z0-9]{10}", line):
            i += 1
            continue
        if line.startswith("Support") or line.startswith("ISIN") or line.startswith("Date de"):
            i += 1
            continue

        name_parts = [line]
        j = i + 1
        while j < total_idx and not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{10}", lines[j]):
            if re.match(r"\d{2}/\d{2}/\d{4}\s", lines[j]):
                break
            name_parts.append(lines[j])
            j += 1
        if j >= total_idx or not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{10}", lines[j]):
            i += 1
            continue

        isin = lines[j]
        if j + 1 >= total_idx:
            break
        values_line = lines[j + 1]
        values_match = re.match(
            r"(?P<date>\d{2}/\d{2}/\d{4})\s+"
            r"(?P<official_value>[0-9 \xa0]+,\d{2})\s+"
            r"(?P<quantity>[0-9 \xa0]+,\d{2,5})\s+"
            r"(?P<unit_value>[0-9 \xa0]+,\d{2,3})"
            r"(?:\s+(?P<average_purchase_price>[0-9 \xa0]+,\d{2,3}))?",
            values_line,
        )
        if not values_match:
            i = j + 1
            continue

        positions.append(
            {
                "asset_name_raw": " ".join(name_parts).strip(),
                "isin": isin,
                "valuation_date": _parse_ddmmyyyy(values_match.group("date")),
                "quantity": _parse_fr_number(values_match.group("quantity")),
                "unit_value": _parse_fr_number(values_match.group("unit_value")),
                "official_value": _parse_fr_number(values_match.group("official_value")),
                "official_average_purchase_price": (
                    _parse_fr_number(values_match.group("average_purchase_price"))
                    if values_match.group("average_purchase_price")
                    else None
                ),
                "official_profit_sharing_amount": None,
            }
        )
        i = j + 2

    euro_value = _match_amount(
        text,
        [r"Epargne\s*investie\s*sur\s*le\s*fonds\s*en\s*euros\s*([0-9 \xa0]+,\d{2})\s*€"],
    )
    euro_interest = _match_amount(
        text,
        [r"titre\s*de\s*l['’]année\s*%s\s*([0-9 \xa0]+,\d{2})\s*€" % coverage_year],
    )
    if euro_value is None:
        euro_value = _match_amount_normalized(text, [r"epargneinvestiesurlefondseneuros([0-9]+,\d{2})"])
    if euro_interest is None:
        euro_interest = _match_amount_normalized(
            text,
            [r"autitredelannee%s([0-9]+,\d{2})" % coverage_year, r"lannee%s([0-9]+,\d{2})" % coverage_year],
        )
    if euro_value is not None:
        positions.append(
            {
                "asset_name_raw": "Fonds Euros Swisslife",
                "isin": "SW0000000000",
                "valuation_date": f"{coverage_year}-12-31",
                "quantity": None,
                "unit_value": None,
                "official_value": euro_value,
                "official_average_purchase_price": None,
                "official_profit_sharing_amount": euro_interest,
            }
        )
    return positions


def _parse_himalia_snapshot_positions(text: str, *, coverage_year: int) -> list[dict[str, Any]]:
    lines = [str(line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    positions: list[dict[str, Any]] = []

    euro_start = next((idx for idx, line in enumerate(lines) if line.startswith("Supports Fonds en euros")), None)
    uc_start = next((idx for idx, line in enumerate(lines) if line.startswith("Supports Unités de Compte")), None)

    if euro_start is not None:
        euro_pattern = re.compile(
            r"^(?P<name>.+?)\s+(?P<date>\d{2}/\d{2}/\d{4})\s+"
            r"(?P<profit>[0-9 \xa0]+,\d{2})\s+€\s+"
            r"(?P<value>[0-9 \xa0]+,\d{2})\s+€$"
        )
        for idx in range(euro_start + 1, len(lines)):
            line = lines[idx]
            if uc_start is not None and idx >= uc_start:
                break
            match = euro_pattern.match(line)
            if not match:
                continue
            positions.append(
                {
                    "asset_name_raw": match.group("name").strip(),
                    "isin": None,
                    "valuation_date": _parse_ddmmyyyy(match.group("date")),
                    "quantity": None,
                    "unit_value": None,
                    "official_value": _parse_fr_number(match.group("value")),
                    "official_average_purchase_price": None,
                    "official_profit_sharing_amount": _parse_fr_number(match.group("profit")),
                }
            )
            break

    if uc_start is not None:
        uc_pattern = re.compile(
            r"^(?P<name>.+?)\s+(?P<date>\d{2}/\d{2}/\d{4})\s+"
            r"(?P<unit_value>[0-9 \xa0]+,\d{2})\s+€\s+"
            r"(?P<quantity>[0-9 \xa0]+,\d{4})\s+"
            r"(?P<pam>[0-9 \xa0]+,\d{2})\s+€\s+"
            r"(?P<value>[0-9 \xa0]+,\d{2})\s+€$"
        )
        for idx in range(uc_start + 1, len(lines)):
            line = lines[idx]
            if line.startswith("(1) PAM") or "OPERATIONS REALISEES" in line:
                break
            match = uc_pattern.match(line)
            if not match:
                continue
            positions.append(
                {
                    "asset_name_raw": match.group("name").strip(),
                    "isin": None,
                    "valuation_date": _parse_ddmmyyyy(match.group("date")),
                    "quantity": _parse_fr_number(match.group("quantity")),
                    "unit_value": _parse_fr_number(match.group("unit_value")),
                    "official_value": _parse_fr_number(match.group("value")),
                    "official_average_purchase_price": _parse_fr_number(match.group("pam")),
                    "official_profit_sharing_amount": None,
                }
            )

    return positions


def _parse_statement_snapshot_positions(doc: dict[str, Any], data_dir: Path) -> list[dict[str, Any]]:
    pdf_path = Path(data_dir) / str(doc["filepath"])
    text = _pdf_text(pdf_path)
    insurer = str(doc["insurer"] or "").lower()
    coverage_year = int(doc["coverage_year"])
    if insurer.startswith("swiss"):
        return _parse_swisslife_snapshot_positions(text, coverage_year=coverage_year)
    return _parse_himalia_snapshot_positions(text, coverage_year=coverage_year)


def _classify_snapshot_operation(label: str) -> str:
    low = str(label or "").lower()
    if "arbitrage" in low:
        return "arbitration"
    if "versement" in low:
        return "external_contribution"
    if "distribution de dividendes" in low:
        return "dividend_distribution"
    if "frais de gestion" in low:
        return "fee"
    if "remboursement" in low:
        return "structured_redemption"
    if "rachat" in low:
        return "withdrawal"
    return "other"


def _parse_visible_operation_header(line: str) -> dict[str, Any] | None:
    match = re.match(r"^(?P<body>.+?)\s+\(Frais\s*:\s*(?P<fees>[^)]+)\)$", line)
    if match is None:
        return None

    body = match.group("body").strip()
    date_match = re.search(r"\s+du\s+(?P<date>\d{2}/\d{2}/\d{4})(?P<tail>.*)$", body)
    if date_match is None:
        return None

    label = body[: date_match.start()].strip() or body
    headline_amount_match = re.search(r"de\s+(-?[0-9 \xa0]+,\d{2})\s+€", label)
    return {
        "operation_label": label,
        "operation_type": _classify_snapshot_operation(label),
        "effective_date": _parse_ddmmyyyy(date_match.group("date")),
        "headline_amount": (
            _parse_fr_number(headline_amount_match.group(1))
            if headline_amount_match is not None
            else None
        ),
        "fees_info_raw": match.group("fees").strip(),
        "notes": date_match.group("tail").strip() or None,
        "legs": [],
    }


def _parse_visible_operation_leg(line: str) -> dict[str, Any] | None:
    line = str(line or "").strip()
    date_match = re.search(r"\s+(?P<date>\d{2}/\d{2}/\d{4})(?P<tail>.*)$", line)
    if date_match is None:
        return None

    left = line[: date_match.start()].strip()
    right = date_match.group("tail").strip()
    amount_match = re.match(
        r"^(?P<name>.+?)\s+(?P<amount>-?(?:\d{1,3}(?:[ \xa0]\d{3})+|\d+),\d{2})\s+€$",
        left,
    )
    if amount_match is None:
        return None

    quantity = None
    unit_value = None
    if right:
        right_match = re.match(
            r"^(?P<unit_value>-?(?:\d{1,3}(?:[ \xa0]\d{3})+|\d+),\d{2})\s+€\s+"
            r"(?P<quantity>-?(?:\d{1,3}(?:[ \xa0]\d{3})+|\d+),\d{1,6})$",
            right,
        )
        if right_match is None:
            return None
        unit_value = _parse_fr_number(right_match.group("unit_value"))
        quantity = _parse_fr_number(right_match.group("quantity"))

    cash_amount = _parse_fr_number(amount_match.group("amount"))
    return {
        "asset_name_raw": amount_match.group("name").strip(),
        "effective_date": _parse_ddmmyyyy(date_match.group("date")),
        "cash_amount": cash_amount,
        "quantity": quantity,
        "unit_value": unit_value,
        "direction": "credit" if cash_amount > 0 else "debit" if cash_amount < 0 else "neutral",
    }


def _parse_himalia_snapshot_visible_operations(text: str) -> list[dict[str, Any]]:
    lines = [str(line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    start_idx = next((idx for idx, line in enumerate(lines) if "OPERATIONS REALISEES DU" in line), None)
    if start_idx is None:
        return []

    operations: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    def flush() -> None:
        nonlocal current
        if current is not None:
            operations.append(current)
        current = None

    for line in lines[start_idx + 1 :]:
        if line.startswith("ESGBPR") or line.startswith("Contrat n°") or re.fullmatch(r"\d+\s*/\s*\d+", line):
            continue
        if line.startswith("Opérations / Supports"):
            continue

        header = _parse_visible_operation_header(line)
        if header is not None:
            flush()
            current = header
            continue

        if current is None:
            continue
        leg = _parse_visible_operation_leg(line)
        if leg is not None:
            current["legs"].append(leg)

    flush()
    return operations


def _import_contract_seeds(conn) -> int:
    count = 0
    for seed in CONTRACT_SEEDS:
        conn.execute(
            """
            INSERT INTO contracts (
                contract_id, contract_name, insurer, holder_type, fiscal_applicability,
                status, external_contributions_total, external_withdrawals_total, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(contract_id) DO UPDATE SET
                contract_name = excluded.contract_name,
                insurer = excluded.insurer,
                holder_type = excluded.holder_type,
                fiscal_applicability = excluded.fiscal_applicability,
                status = excluded.status,
                notes = excluded.notes
            """,
            (
                seed["contract_id"],
                seed["contract_name"],
                seed["insurer"],
                seed["holder_type"],
                seed["fiscal_applicability"],
                seed["status"],
                0.0,
                0.0,
                seed["notes"],
            ),
        )
        count += 1
    return count


def _import_external_flow_snapshots(conn, data_dir: Path) -> None:
    snapshot_files = [
        Path(data_dir) / "market_data" / "contract_snapshots_himalia.yaml",
        Path(data_dir) / "market_data" / "contract_snapshots_swisslife.yaml",
    ]
    for path in snapshot_files:
        if not path.exists():
            continue
        data = _load_yaml(path)
        contract_id = str(data.get("contract_id") or "")
        conn.execute(
            """
            UPDATE contracts
            SET external_contributions_total = ?, external_withdrawals_total = ?
            WHERE contract_id = ?
            """,
            (
                float(data.get("versements_total") or 0.0),
                float(data.get("retraits_total") or 0.0),
                contract_id,
            ),
        )
        conn.execute("DELETE FROM contract_external_flows WHERE contract_id = ?", (contract_id,))
        flows = data.get("flux_externes_par_annee") or {}
        for year, contributions in flows.items():
            conn.execute(
                """
                INSERT INTO contract_external_flows (
                    contract_id, flow_year, contributions_total, withdrawals_total
                ) VALUES (?, ?, ?, ?)
                """,
                (contract_id, int(year), float(contributions or 0.0), 0.0),
            )


def _import_documents_from_index(conn, data_dir: Path, index_path: Path) -> int:
    if not index_path.exists():
        return 0
    payload = _load_yaml(index_path)
    imported_at = datetime.now().isoformat(timespec="seconds")
    count = 0
    for doc in payload.get("documents", []):
        document_date = doc.get("document_date") or doc.get("statement_date")
        conn.execute(
            """
            INSERT INTO documents (
                document_id, document_type, insurer, contract_name, asset_id, document_date,
                coverage_year, status, filepath, original_filename, sha256, notes, imported_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(document_id) DO UPDATE SET
                document_type = excluded.document_type,
                insurer = excluded.insurer,
                contract_name = excluded.contract_name,
                asset_id = excluded.asset_id,
                document_date = excluded.document_date,
                coverage_year = excluded.coverage_year,
                status = excluded.status,
                filepath = excluded.filepath,
                original_filename = excluded.original_filename,
                sha256 = excluded.sha256,
                notes = excluded.notes,
                imported_at = excluded.imported_at
            """,
            (
                doc["document_id"],
                doc["document_type"],
                doc["insurer"],
                doc.get("contract_name"),
                doc.get("asset_id"),
                document_date,
                doc.get("coverage_year"),
                doc.get("status", "active"),
                str(doc["filepath"]),
                doc.get("original_filename"),
                doc.get("sha256"),
                doc.get("notes"),
                imported_at,
            ),
        )
        count += 1
    return count


def _import_brochures(conn, data_dir: Path) -> int:
    brochure_dir = Path(data_dir) / "product_brochure"
    brochure_index = brochure_dir / "index.yaml"
    imported_at = datetime.now().isoformat(timespec="seconds")
    count = 0
    indexed_files: set[str] = set()

    if brochure_index.exists():
        payload = _load_yaml(brochure_index)
        for doc in payload.get("documents", []):
            doc_id = str(doc.get("document_id") or f"structured_brochure_{_slug(Path(str(doc.get('filepath') or '')).stem)}")
            filepath = str(doc.get("filepath") or "")
            if filepath:
                indexed_files.add(Path(filepath).name)
            conn.execute(
                """
                INSERT INTO documents (
                    document_id, document_type, insurer, contract_name, asset_id, document_date,
                    coverage_year, status, filepath, original_filename, sha256, notes, imported_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(document_id) DO UPDATE SET
                    asset_id = excluded.asset_id,
                    filepath = excluded.filepath,
                    original_filename = excluded.original_filename,
                    sha256 = excluded.sha256,
                    notes = excluded.notes,
                    imported_at = excluded.imported_at
                """,
                (
                    doc_id,
                    str(doc.get("document_type") or "structured_brochure"),
                    str(doc.get("insurer") or "Mixed"),
                    doc.get("contract_name"),
                    doc.get("asset_id"),
                    doc.get("document_date"),
                    doc.get("coverage_year"),
                    str(doc.get("status") or "active"),
                    filepath,
                    doc.get("original_filename"),
                    doc.get("sha256"),
                    doc.get("notes") or "Brochure de produit structuré importée depuis index.yaml.",
                    imported_at,
                ),
            )
            count += 1

    for brochure in sorted(brochure_dir.glob("*.pdf")):
        if brochure.name in indexed_files:
            continue
        doc_id = f"structured_brochure_{_slug(brochure.stem)}"
        isin_match = re.search(r"(FR[A-Z0-9]{10}|LU[A-Z0-9]{10})", brochure.name)
        asset_id = isin_match.group(1) if isin_match else None
        conn.execute(
            """
            INSERT INTO documents (
                document_id, document_type, insurer, contract_name, asset_id, document_date,
                coverage_year, status, filepath, original_filename, sha256, notes, imported_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(document_id) DO UPDATE SET
                asset_id = excluded.asset_id,
                filepath = excluded.filepath,
                original_filename = excluded.original_filename,
                imported_at = excluded.imported_at
            """,
            (
                doc_id,
                "structured_brochure",
                "Mixed",
                None,
                asset_id,
                None,
                None,
                "active",
                str(Path("product_brochure") / brochure.name),
                brochure.name,
                None,
                "Brochure de produit structuré importée automatiquement.",
                imported_at,
            ),
        )
        count += 1
    return count


def _import_statement_snapshots(conn, data_dir: Path) -> tuple[int, list[str]]:
    rows = conn.execute(
        """
        SELECT document_id, insurer, contract_name, document_date, coverage_year, filepath
        FROM documents
        WHERE document_type = 'insurer_statement'
          AND coverage_year IS NOT NULL
        ORDER BY contract_name, coverage_year
        """
    ).fetchall()
    imported_at = datetime.now().isoformat(timespec="seconds")
    count = 0
    warnings: list[str] = []
    for row in rows:
        try:
            snapshot = _parse_statement_snapshot(dict(row), data_dir)
        except Exception as exc:
            warnings.append(f"{row['document_id']}: {exc}")
            continue
        contract_id_row = conn.execute(
            "SELECT contract_id FROM contracts WHERE contract_name = ?",
            (snapshot["contract_name"],),
        ).fetchone()
        if contract_id_row is None:
            continue
        conn.execute(
            """
            INSERT INTO annual_snapshots (
                snapshot_id, contract_id, contract_name, reference_date, statement_date,
                source_document_id, status, official_total_value, official_uc_value,
                official_fonds_euro_value, official_euro_interest_net, official_notes, imported_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(snapshot_id) DO UPDATE SET
                statement_date = excluded.statement_date,
                source_document_id = excluded.source_document_id,
                status = CASE
                    WHEN annual_snapshots.status IN ('validated', 'rejected') THEN annual_snapshots.status
                    ELSE excluded.status
                END,
                official_total_value = excluded.official_total_value,
                official_uc_value = excluded.official_uc_value,
                official_fonds_euro_value = excluded.official_fonds_euro_value,
                official_euro_interest_net = excluded.official_euro_interest_net,
                official_notes = excluded.official_notes,
                imported_at = excluded.imported_at
            """,
            (
                snapshot["snapshot_id"],
                contract_id_row["contract_id"],
                snapshot["contract_name"],
                snapshot["reference_date"],
                snapshot["statement_date"],
                snapshot["source_document_id"],
                snapshot["status"],
                snapshot["official_total_value"],
                snapshot["official_uc_value"],
                snapshot["official_fonds_euro_value"],
                snapshot["official_euro_interest_net"],
                snapshot["official_notes"],
                imported_at,
            ),
        )
        count += 1
    return count, warnings


def _looks_like_isin(value: str | None) -> bool:
    return bool(re.fullmatch(r"[A-Z]{2}[A-Z0-9]{10}", str(value or "").strip()))


def _comment_blocks_by_key(path: Path, *, key_indent: int = 2) -> dict[str, list[str]]:
    if not path.exists():
        return {}
    key_pattern = re.compile(rf"^\s{{{key_indent}}}([a-zA-Z0-9_]+):\s*$")
    comments: dict[str, list[str]] = {}
    pending: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("#"):
            pending.append(stripped[1:].strip())
            continue
        match = key_pattern.match(raw_line)
        if match is not None:
            comments[match.group(1)] = list(pending)
            pending = []
            continue
        if stripped:
            pending = []
    return comments


def _parse_name_and_code_from_comments(comment_lines: list[str]) -> tuple[str | None, str | None]:
    for line in comment_lines:
        line = str(line or "").strip()
        if line.startswith("#"):
            line = line[1:].strip()
        if not line or line.lower().startswith("source:"):
            continue
        match = re.search(
            r'"?(?P<name>.+?)"?\s+\((?P<code>[A-Z]{2}[A-Z0-9]{10}|[A-Z0-9]{6,12})\)',
            line,
        )
        if match is None:
            continue
        name = str(match.group("name")).strip().strip('"')
        name = re.sub(r"^Taux déclarés pour le Fonds Euros?\s+", "", name, flags=re.IGNORECASE).strip()
        return name or None, str(match.group("code")).strip()
    return None, None


def _clean_asset_name(raw_name: str) -> str:
    name = str(raw_name or "").strip()
    name = re.sub(
        r"^\d+/\d+\s+Support\s+ISIN.*?Prixmoyen\s+d['’]investissement\s*",
        "",
        name,
        flags=re.IGNORECASE,
    )
    name = re.sub(r"([A-Z]{2,})([A-Z][a-z])", r"\1 \2", name)
    name = re.sub(r"([A-Z])([A-Z][a-z])", r"\1 \2", name)
    name = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
    name = re.sub(r"([A-Za-z])(\d)", r"\1 \2", name)
    name = re.sub(r"(\d)([A-Za-z])", r"\1 \2", name)
    name = re.sub(r"\s+", " ", name).strip(" -")
    return name


def _infer_asset_type(name: str, *, insurer: str = "", isin: str | None = None) -> str:
    cleaned_name = _clean_asset_name(name)
    normalized = unicodedata.normalize("NFKD", cleaned_name)
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    normalized = normalized.lower()
    if isin == "SW0000000000":
        return "fonds_euro"
    if "actif general generali vie" in normalized or "fonds euro" in normalized or "fonds euros" in normalized:
        return "fonds_euro"
    if re.search(r"\b(callable|coupon|rendement|autocall|emtn)\b", normalized):
        return "structured_product"
    if re.search(r"\b(sci|scpi|sc)\b", normalized):
        return "uc_illiquid"
    if str(insurer or "").lower().startswith("swiss") and cleaned_name.startswith("D "):
        return "structured_product"
    return "uc_fund"


def _infer_uc_fund_type(name: str, asset_id: str, quantalys_category: str | None = None) -> str:
    normalized = unicodedata.normalize("NFKD", f"{name} {asset_id} {quantalys_category or ''}")
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    normalized = normalized.lower()
    if "money market" in normalized or "short term" in normalized or "monetaire" in normalized or "tresorerie" in normalized:
        return "money_market"
    if re.search(r"\bsci\b", normalized):
        return "sci"
    if re.search(r"\bscpi\b|\bsc\b", normalized):
        return "sc"
    return "mutual_fund"


def _derive_asset_id_from_name(name: str, *, asset_type: str) -> str:
    normalized = _clean_asset_name(name)
    normalized = unicodedata.normalize("NFKD", normalized)
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    normalized = normalized.lower()
    normalized = re.sub(r"(\d)[.,](\d)", r"\1\2", normalized)
    replacements = [
        (r"\bdividende forfaitaire\b", "div_forf"),
        (r"\bdividende fixe\b", "div_fix"),
        (r"\bdividende\b", "div"),
        (r"\bdecembre\b", "dec"),
        (r"\bfevrier\b", "fev"),
        (r"\bjanvier\b", "jan"),
        (r"\bavril\b", "avr"),
        (r"\bjuillet\b", "jul"),
        (r"\bseptembre\b", "sep"),
        (r"\boctobre\b", "oct"),
        (r"\bnovembre\b", "nov"),
        (r"\bmars\b", "mar"),
    ]
    for pattern, replacement in replacements:
        normalized = re.sub(pattern, replacement, normalized)
    normalized = re.sub(
        r"\b(\d{2})\b",
        lambda match: f"20{match.group(1)}" if 20 <= int(match.group(1)) <= 39 else match.group(1),
        normalized,
    )
    normalized = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")
    prefix = {
        "structured_product": "struct",
        "uc_fund": "uc",
        "uc_illiquid": "uc",
        "fonds_euro": "fonds_euro",
    }.get(asset_type, "asset")
    if normalized.startswith(f"{prefix}_"):
        return normalized
    return f"{prefix}_{normalized}"


def _merge_asset_seed(asset_seeds: dict[str, dict[str, Any]], seed: dict[str, Any], *, priority: int) -> None:
    asset_id = str(seed["asset_id"])
    normalized_seed = {
        "asset_id": asset_id,
        "asset_type": str(seed["asset_type"]),
        "name": str(seed["name"]),
        "valuation_engine": str(seed["valuation_engine"]),
        "isin": str(seed["isin"]) if seed.get("isin") is not None else None,
        "metadata": dict(seed.get("metadata") or {}),
        "_priority": priority,
    }
    existing = asset_seeds.get(asset_id)
    if existing is None:
        asset_seeds[asset_id] = normalized_seed
        return

    existing_priority = int(existing.get("_priority") or 0)
    if priority >= existing_priority:
        for field in ("asset_type", "name", "valuation_engine", "isin"):
            value = normalized_seed.get(field)
            if value:
                existing[field] = value
        metadata = dict(existing.get("metadata") or {})
        metadata.update(normalized_seed["metadata"])
        existing["metadata"] = metadata
        existing["_priority"] = priority
        return

    for field in ("asset_type", "name", "valuation_engine", "isin"):
        if not existing.get(field) and normalized_seed.get(field):
            existing[field] = normalized_seed[field]
    metadata = dict(normalized_seed["metadata"])
    metadata.update(existing.get("metadata") or {})
    existing["metadata"] = metadata


def _extract_brochure_paths_by_isin(data_dir: Path) -> dict[str, str]:
    brochure_dir = Path(data_dir) / "product_brochure"
    mappings: dict[str, str] = {}
    if not brochure_dir.exists():
        return mappings
    for brochure in brochure_dir.glob("*.pdf"):
        match = re.search(r"(FR[A-Z0-9]{10}|LU[A-Z0-9]{10})", brochure.name)
        if match is None:
            continue
        mappings[match.group(1)] = str(Path("product_brochure") / brochure.name)
    return mappings


def _load_quantalys_ratings(data_dir: Path) -> dict[str, dict[str, Any]]:
    path = Path(data_dir) / "market_data" / "quantalys_ratings.yaml"
    if not path.exists():
        return {}
    payload = _load_yaml(path)
    ratings: dict[str, dict[str, Any]] = {}
    for raw in payload.get("ratings") or []:
        isin = str(raw.get("isin") or "").strip()
        if isin:
            ratings[isin] = dict(raw)
    return ratings


def _asset_seeds_from_nav_sources(data_dir: Path) -> list[dict[str, Any]]:
    path = Path(data_dir) / "market_data" / "nav_sources.yaml"
    if not path.exists():
        return []
    payload = _load_yaml(path).get("nav_sources") or {}
    comments = _comment_blocks_by_key(path, key_indent=2)
    ratings_by_isin = _load_quantalys_ratings(data_dir)
    seeds: list[dict[str, Any]] = []
    for asset_id, raw in payload.items():
        comment_name, comment_code = _parse_name_and_code_from_comments(comments.get(str(asset_id), []))
        isin = comment_code if _looks_like_isin(comment_code) else None
        rating = ratings_by_isin.get(str(isin)) if isin else None
        name = comment_name or str((rating or {}).get("name") or asset_id)
        quantalys_category = (rating or {}).get("quantalys_category")
        asset_type = "uc_illiquid" if _infer_uc_fund_type(name, str(asset_id), quantalys_category) in {"sci", "sc"} else "uc_fund"
        fund_type = _infer_uc_fund_type(name, str(asset_id), quantalys_category)
        metadata = {"fund_type": fund_type, "nav_source_kind": str((raw or {}).get("kind") or "")}
        if quantalys_category:
            metadata["quantalys_category"] = str(quantalys_category)
        seeds.append(
            {
                "asset_id": str(asset_id),
                "asset_type": asset_type,
                "name": name,
                "valuation_engine": "hybrid" if asset_type == "uc_illiquid" else "mark_to_market",
                "isin": isin,
                "metadata": metadata,
            }
        )
    return seeds


def _asset_seeds_from_fonds_euro_files(data_dir: Path) -> list[dict[str, Any]]:
    market_data_dir = Path(data_dir) / "market_data"
    seeds: list[dict[str, Any]] = []
    for path in sorted(market_data_dir.glob("fonds_euro_*.yaml")):
        asset_id = path.stem.removeprefix("fonds_euro_")
        comment_name, comment_code = _parse_name_and_code_from_comments(path.read_text(encoding="utf-8").splitlines()[:4])
        name = comment_name or asset_id.replace("_", " ").title()
        metadata: dict[str, Any] = {}
        if _looks_like_isin(comment_code):
            isin = str(comment_code)
        else:
            isin = None
            if comment_code:
                metadata["identifier"] = str(comment_code)
        if "swiss" in asset_id.lower() or "swiss" in name.lower():
            metadata["insurer"] = "Swiss Life"
        elif "generali" in asset_id.lower() or "generali" in name.lower():
            metadata["insurer"] = "Generali"
        metadata["category"] = "fonds_euro"
        seeds.append(
            {
                "asset_id": asset_id,
                "asset_type": "fonds_euro",
                "name": name,
                "valuation_engine": "declarative",
                "isin": isin,
                "metadata": metadata,
            }
        )
    return seeds


def _estimate_period_months(expected_events: list[dict[str, Any]]) -> int | None:
    dates: list[datetime] = []
    for event in expected_events:
        try:
            dates.append(datetime.fromisoformat(str(event["date"])))
        except Exception:
            continue
    dates = sorted(dates)
    for first, second in zip(dates, dates[1:]):
        months = (second.year - first.year) * 12 + (second.month - first.month)
        if months > 0:
            return months
    return None


def _asset_seeds_from_structured_event_files(data_dir: Path, *, brochures_by_isin: dict[str, str]) -> list[dict[str, Any]]:
    market_data_dir = Path(data_dir) / "market_data"
    seeds: list[dict[str, Any]] = []
    for path in sorted(market_data_dir.glob("events_*.yaml")):
        asset_id = path.stem.removeprefix("events_")
        text = path.read_text(encoding="utf-8")
        comment_name, comment_code = _parse_name_and_code_from_comments(text.splitlines()[:4])
        isin = comment_code if _looks_like_isin(comment_code) else None
        payload = _load_yaml(path)
        expected_events = list(payload.get("expected_events") or [])
        metadata: dict[str, Any] = {}
        period_months = _estimate_period_months(expected_events)
        if period_months is not None:
            metadata["period_months"] = period_months
        for event in expected_events:
            event_metadata = dict(event.get("metadata") or {})
            for source_key, target_key in (
                ("underlying", "underlying"),
                ("coupon_rate", "coupon_rate"),
                ("gain_per_semester", "gain_per_semester"),
                ("initial_observation_date", "initial_observation_date"),
            ):
                if target_key not in metadata and event_metadata.get(source_key) is not None:
                    metadata[target_key] = event_metadata.get(source_key)
        extra_metadata = dict(payload.get("asset_metadata") or {})
        if extra_metadata:
            metadata.update({key: value for key, value in extra_metadata.items() if value is not None})
        if isin and isin in brochures_by_isin:
            metadata["documentation"] = brochures_by_isin[isin]
        seeds.append(
            {
                "asset_id": asset_id,
                "asset_type": "structured_product",
                "name": comment_name or asset_id.replace("_", " "),
                "valuation_engine": "event_based",
                "isin": isin,
                "metadata": metadata,
            }
        )
    return seeds


def _asset_snapshot_discoveries(conn, data_dir: Path) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT s.snapshot_id, s.contract_id, s.contract_name, s.reference_date, d.document_id, d.insurer, d.coverage_year, d.filepath
        FROM annual_snapshots s
        JOIN documents d ON d.document_id = s.source_document_id
        ORDER BY s.contract_name, s.reference_date
        """
    ).fetchall()
    discoveries: list[dict[str, Any]] = []
    seen: set[tuple[str, str | None]] = set()
    for row in rows:
        try:
            parsed_positions = _parse_statement_snapshot_positions(dict(row), data_dir)
        except Exception:
            continue
        for snapshot_position in parsed_positions:
            name = _clean_asset_name(str(snapshot_position.get("asset_name_raw") or ""))
            isin = str(snapshot_position.get("isin")) if snapshot_position.get("isin") else None
            key = (name, isin)
            if not name or key in seen:
                continue
            seen.add(key)
            discoveries.append(
                {
                    "name": name,
                    "isin": isin,
                    "asset_type": _infer_asset_type(name, insurer=str(row["insurer"] or ""), isin=isin),
                }
            )
    return discoveries


def _load_arbitration_document_payloads(conn, data_dir: Path) -> list[dict[str, Any]]:
    from .arbitration import parse_arbitration_text

    rows = conn.execute(
        """
        SELECT d.document_id, d.contract_name, d.document_date, d.filepath, d.insurer, c.contract_id
        FROM documents d
        LEFT JOIN contracts c ON c.contract_name = d.contract_name
        WHERE d.document_type = 'arbitration_letter'
        ORDER BY COALESCE(d.document_date, ''), d.document_id
        """
    ).fetchall()
    payloads: list[dict[str, Any]] = []
    for row in rows:
        filepath = str(row["filepath"] or "")
        if not filepath:
            continue
        pdf_path = Path(data_dir) / filepath
        try:
            proposal = parse_arbitration_text(
                _pdf_text(pdf_path),
                fallback_date=str(row["document_date"] or "") or None,
            )
        except Exception:
            continue
        payloads.append(
            {
                "document_id": str(row["document_id"]),
                "contract_id": str(row["contract_id"] or ""),
                "contract_name": str(row["contract_name"] or ""),
                "document_date": str(row["document_date"] or "") or None,
                "filepath": filepath,
                "insurer": str(row["insurer"] or ""),
                "proposal": proposal,
            }
        )
    return payloads


def _asset_arbitration_discoveries(arbitration_documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    discoveries: list[dict[str, Any]] = []
    seen: set[tuple[str, str | None]] = set()
    for document in arbitration_documents:
        proposal = dict(document.get("proposal") or {})
        for leg in list(proposal.get("from_legs") or []) + list(proposal.get("to_legs") or []):
            name = _clean_asset_name(str(leg.get("name") or ""))
            isin = str(leg.get("isin")) if leg.get("isin") else None
            if not name and not isin:
                continue
            key = (name, isin)
            if key in seen:
                continue
            seen.add(key)
            asset_type = _infer_asset_type(name or str(isin or ""), insurer=str(document.get("insurer") or ""), isin=isin)
            metadata: dict[str, Any] = {"source_kind": "arbitration_letter"}
            if name:
                derived_asset_id = _derive_asset_id_from_name(name, asset_type=asset_type)
                if asset_type in {"uc_fund", "uc_illiquid"}:
                    metadata["fund_type"] = _infer_uc_fund_type(name, derived_asset_id)
            discoveries.append(
                {
                    "name": name or str(isin or ""),
                    "isin": isin,
                    "asset_type": asset_type,
                    "metadata": metadata,
                }
            )
    return discoveries


def _refresh_asset_lifecycle_statuses(conn, data_dir: Path, *, db_path: Path | None = None) -> int:
    runtime = V2Runtime(data_dir, db_path=db_path)
    latest_snapshot_by_contract = {
        str(row["contract_name"]): datetime.fromisoformat(str(row["reference_date"])).date()
        for row in conn.execute(
            """
            SELECT contract_name, MAX(reference_date) AS reference_date
            FROM annual_snapshots
            GROUP BY contract_name
            """
        ).fetchall()
        if row["reference_date"] is not None
    }
    positions_open_on_latest_snapshot = {
        str(row["position_id"])
        for row in conn.execute(
            """
            WITH latest_snapshots AS (
                SELECT contract_name, MAX(reference_date) AS reference_date
                FROM annual_snapshots
                GROUP BY contract_name
            )
            SELECT DISTINCT sp.position_id
            FROM snapshot_positions sp
            JOIN annual_snapshots s ON s.snapshot_id = sp.snapshot_id
            JOIN latest_snapshots ls
              ON ls.contract_name = s.contract_name
             AND ls.reference_date = s.reference_date
            WHERE sp.position_id IS NOT NULL
            """
        ).fetchall()
    }

    states_by_asset: dict[str, dict[str, bool]] = {}
    for position in runtime.portfolio.list_all_positions():
        state = states_by_asset.setdefault(
            str(position.asset_id),
            {"has_positions": False, "has_open_position": False},
        )
        state["has_positions"] = True
        latest_snapshot_date = latest_snapshot_by_contract.get(str(position.wrapper.contract_name or ""))
        is_after_latest_snapshot = (
            latest_snapshot_date is None
            or position.investment.subscription_date > latest_snapshot_date
        )
        if is_after_latest_snapshot or position.position_id in positions_open_on_latest_snapshot:
            state["has_open_position"] = True

    updates = 0
    for asset in runtime.portfolio.list_all_assets():
        metadata = dict(asset.metadata or {})
        previous_status = str(metadata.get("status") or "").lower()
        state = states_by_asset.get(str(asset.asset_id))
        next_status = "historical" if state and not state["has_open_position"] else None
        if next_status == "historical":
            if previous_status != "historical":
                metadata["status"] = "historical"
            else:
                continue
        else:
            if previous_status != "historical":
                continue
            metadata.pop("status", None)
        conn.execute(
            "UPDATE assets SET metadata_json = ? WHERE asset_id = ?",
            (
                json.dumps(metadata, ensure_ascii=False, sort_keys=True, default=str),
                asset.asset_id,
            ),
        )
        updates += 1
    return updates


def _match_asset_seed_by_name(asset_seeds: dict[str, dict[str, Any]], name: str) -> str | None:
    target_tokens = set(_normalize_tokens(_clean_asset_name(name)))
    if not target_tokens:
        return None
    best: tuple[float, str] | None = None
    for asset_id, seed in asset_seeds.items():
        candidate_tokens = set(_normalize_tokens(str(seed.get("name") or "")))
        if not candidate_tokens:
            continue
        intersection = len(target_tokens & candidate_tokens)
        union = len(target_tokens | candidate_tokens)
        score = max(
            (intersection / union) if union else 0.0,
            (intersection / min(len(target_tokens), len(candidate_tokens)))
            if target_tokens and candidate_tokens
            else 0.0,
        )
        if best is None or score > best[0]:
            best = (score, asset_id)
    if best is None or best[0] < 0.75:
        return None
    return best[1]


def _import_portfolio_seed_state(
    conn,
    data_dir: Path,
    *,
    arbitration_documents: list[dict[str, Any]] | None = None,
) -> dict[str, int]:
    imported_at = datetime.now().isoformat(timespec="seconds")

    asset_seeds: dict[str, dict[str, Any]] = {}
    brochures_by_isin = _extract_brochure_paths_by_isin(data_dir)
    for seed in _asset_seeds_from_nav_sources(data_dir):
        _merge_asset_seed(asset_seeds, seed, priority=80)
    for seed in _asset_seeds_from_fonds_euro_files(data_dir):
        _merge_asset_seed(asset_seeds, seed, priority=80)
    for seed in _asset_seeds_from_structured_event_files(data_dir, brochures_by_isin=brochures_by_isin):
        _merge_asset_seed(asset_seeds, seed, priority=80)
    for discovery in _asset_snapshot_discoveries(conn, data_dir):
        asset_id = None
        if discovery.get("isin"):
            for existing_asset_id, seed in asset_seeds.items():
                if str(seed.get("isin") or "") == str(discovery["isin"]):
                    asset_id = existing_asset_id
                    break
        if asset_id is None and not discovery.get("isin"):
            asset_id = _match_asset_seed_by_name(asset_seeds, str(discovery["name"]))
        if asset_id is None:
            asset_id = _derive_asset_id_from_name(str(discovery["name"]), asset_type=str(discovery["asset_type"]))
        metadata: dict[str, Any] = {}
        if discovery.get("isin") and str(discovery["isin"]) in brochures_by_isin:
            metadata["documentation"] = brochures_by_isin[str(discovery["isin"])]
        _merge_asset_seed(
            asset_seeds,
            {
                "asset_id": asset_id,
                "asset_type": str(discovery["asset_type"]),
                "name": str(discovery["name"]),
                "valuation_engine": "event_based"
                if str(discovery["asset_type"]) == "structured_product"
                else "declarative"
                if str(discovery["asset_type"]) == "fonds_euro"
                else "hybrid"
                if str(discovery["asset_type"]) == "uc_illiquid"
                else "mark_to_market",
                "isin": discovery.get("isin"),
                "metadata": metadata,
            },
            priority=40,
        )
    for discovery in _asset_arbitration_discoveries(arbitration_documents or []):
        asset_id = None
        if discovery.get("isin"):
            for existing_asset_id, seed in asset_seeds.items():
                if str(seed.get("isin") or "") == str(discovery["isin"]):
                    asset_id = existing_asset_id
                    break
        if asset_id is None and not discovery.get("isin"):
            asset_id = _match_asset_seed_by_name(asset_seeds, str(discovery["name"]))
        if asset_id is None:
            asset_id = _derive_asset_id_from_name(str(discovery["name"]), asset_type=str(discovery["asset_type"]))
        _merge_asset_seed(
            asset_seeds,
            {
                "asset_id": asset_id,
                "asset_type": str(discovery["asset_type"]),
                "name": str(discovery["name"]),
                "valuation_engine": "event_based"
                if str(discovery["asset_type"]) == "structured_product"
                else "declarative"
                if str(discovery["asset_type"]) == "fonds_euro"
                else "hybrid"
                if str(discovery["asset_type"]) == "uc_illiquid"
                else "mark_to_market",
                "isin": discovery.get("isin"),
                "metadata": dict(discovery.get("metadata") or {}),
            },
            priority=35,
        )
    if not asset_seeds:
        raise FileNotFoundError("Aucune source d'actifs disponible: market_data et PDF absents")

    conn.execute("DELETE FROM position_lots")
    conn.execute("DELETE FROM positions")
    conn.execute("DELETE FROM assets")

    assets_count = 0
    positions_count = 0
    lots_count = 0

    for raw in sorted(asset_seeds.values(), key=lambda item: str(item["asset_id"])):
        conn.execute(
            """
            INSERT INTO assets (
                asset_id, asset_type, name, valuation_engine, isin, metadata_json, imported_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(raw["asset_id"]),
                str(raw["asset_type"]),
                str(raw["name"]),
                str(raw["valuation_engine"]),
                str(raw["isin"]) if raw.get("isin") is not None else None,
                json.dumps(raw.get("metadata") or {}, ensure_ascii=False, sort_keys=True, default=str),
                imported_at,
            ),
        )
        assets_count += 1

    return {"assets": assets_count, "positions": positions_count, "position_lots": lots_count}


def _import_statement_snapshot_positions(conn, data_dir: Path, *, db_path: Path | None = None) -> int:
    rows = conn.execute(
        """
        SELECT s.snapshot_id, s.contract_id, s.contract_name, s.reference_date, d.document_id, d.insurer, d.coverage_year, d.filepath
        FROM annual_snapshots s
        JOIN documents d ON d.document_id = s.source_document_id
        ORDER BY s.contract_name, s.reference_date
        """
    ).fetchall()
    imported_at = datetime.now().isoformat(timespec="seconds")
    conn.execute("DELETE FROM snapshot_positions")

    runtime = V2Runtime(data_dir, db_path=db_path, include_db_overlay=False)
    count = 0
    for row in rows:
        try:
            parsed_positions = _parse_statement_snapshot_positions(dict(row), data_dir)
        except Exception:
            continue
        for index, snapshot_position in enumerate(parsed_positions):
            mapping = _match_snapshot_position_mapping(
                runtime,
                contract_name=str(row["contract_name"]),
                asset_name_raw=str(snapshot_position["asset_name_raw"]),
                isin=str(snapshot_position["isin"]) if snapshot_position.get("isin") else None,
            )
            conn.execute(
                """
                INSERT INTO snapshot_positions (
                    snapshot_position_id, snapshot_id, contract_id, contract_name, position_id, asset_id, asset_type,
                    asset_name_raw, isin, valuation_date, quantity, unit_value, official_value, official_cost_basis,
                    official_profit_sharing_amount, official_average_purchase_price, status, notes, imported_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"{row['snapshot_id']}::{index + 1}",
                    row["snapshot_id"],
                    row["contract_id"],
                    row["contract_name"],
                    mapping["position_id"],
                    mapping["asset_id"],
                    mapping["asset_type"],
                    snapshot_position["asset_name_raw"],
                    snapshot_position.get("isin"),
                    snapshot_position.get("valuation_date"),
                    snapshot_position.get("quantity"),
                    snapshot_position.get("unit_value"),
                    snapshot_position["official_value"],
                    snapshot_position.get("official_cost_basis"),
                    snapshot_position.get("official_profit_sharing_amount"),
                    snapshot_position.get("official_average_purchase_price"),
                    "proposed",
                    f"Position visible sur le relevé assureur ({mapping['match_source']}).",
                    imported_at,
                ),
            )
            count += 1
    return count


def _import_snapshot_visible_operations(conn, data_dir: Path, *, db_path: Path | None = None) -> tuple[int, int]:
    rows = conn.execute(
        """
        SELECT s.snapshot_id, s.contract_id, s.contract_name, s.reference_date, d.document_id, d.insurer, d.coverage_year, d.filepath
        FROM annual_snapshots s
        JOIN documents d ON d.document_id = s.source_document_id
        ORDER BY s.contract_name, s.reference_date
        """
    ).fetchall()
    imported_at = datetime.now().isoformat(timespec="seconds")
    conn.execute("DELETE FROM snapshot_operation_legs_visible")
    conn.execute("DELETE FROM snapshot_operations_visible")

    runtime = V2Runtime(data_dir, db_path=db_path, include_db_overlay=False)
    operations_count = 0
    legs_count = 0
    for row in rows:
        if str(row["insurer"] or "").lower().startswith("swiss"):
            continue
        pdf_path = Path(data_dir) / str(row["filepath"])
        try:
            operations = _parse_himalia_snapshot_visible_operations(_pdf_text(pdf_path))
        except Exception:
            continue

        for index, operation in enumerate(operations):
            snapshot_operation_id = f"{row['snapshot_id']}::op::{index + 1}"
            conn.execute(
                """
                INSERT INTO snapshot_operations_visible (
                    snapshot_operation_id, snapshot_id, contract_id, contract_name, operation_label, operation_type,
                    effective_date, headline_amount, fees_info_raw, status, notes, imported_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_operation_id,
                    row["snapshot_id"],
                    row["contract_id"],
                    row["contract_name"],
                    operation["operation_label"],
                    operation["operation_type"],
                    operation["effective_date"],
                    operation.get("headline_amount"),
                    operation.get("fees_info_raw"),
                    "proposed",
                    " ".join(
                        part
                        for part in (
                            "Opération visible extraite depuis le relevé assureur.",
                            str(operation.get("notes") or "").strip() or None,
                        )
                        if part
                    ),
                    imported_at,
                ),
            )
            operations_count += 1

            for leg_index, leg in enumerate(operation.get("legs") or []):
                mapping = _match_snapshot_position_mapping(
                    runtime,
                    contract_name=str(row["contract_name"]),
                    asset_name_raw=str(leg["asset_name_raw"]),
                    isin=None,
                )
                conn.execute(
                    """
                    INSERT INTO snapshot_operation_legs_visible (
                        snapshot_operation_leg_id, snapshot_operation_id, snapshot_id, contract_id, contract_name,
                        position_id, asset_id, asset_type, asset_name_raw, effective_date, cash_amount,
                        quantity, unit_value, direction, notes, imported_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"{snapshot_operation_id}::leg::{leg_index + 1}",
                        snapshot_operation_id,
                        row["snapshot_id"],
                        row["contract_id"],
                        row["contract_name"],
                        mapping["position_id"],
                        mapping["asset_id"],
                        mapping["asset_type"],
                        leg["asset_name_raw"],
                        leg.get("effective_date"),
                        leg["cash_amount"],
                        leg.get("quantity"),
                        leg.get("unit_value"),
                        leg["direction"],
                        f"Ligne visible sur relevé ({mapping['match_source']}).",
                        imported_at,
                    ),
                )
                legs_count += 1
    return operations_count, legs_count


def _derive_reconstructed_position_id(contract_name: str, asset_id: str) -> str:
    return f"pdf_{_slug(contract_name)}_{asset_id}"


def _visible_operation_lot_type(operation_type: str, direction: str) -> str:
    operation_type = str(operation_type or "")
    direction = str(direction or "")
    if operation_type == "fee":
        return "fee"
    if operation_type == "tax":
        return "tax"
    if direction == "credit":
        return "buy"
    if direction == "debit" and operation_type in {"arbitration", "structured_redemption", "withdrawal"}:
        return "sell"
    return "other"


def _visible_operation_external_flag(operation_type: str) -> bool | None:
    operation_type = str(operation_type or "")
    if operation_type == "external_contribution":
        return True
    if operation_type in {"arbitration", "structured_redemption", "dividend_distribution"}:
        return False
    if operation_type == "withdrawal":
        return True
    return None


def _visible_operation_lot_payload(row) -> dict[str, Any] | None:
    effective_date = str(row["effective_date"] or "").strip()
    if not effective_date:
        return None
    operation_type = str(row["operation_type"] or "")
    return {
        "date": effective_date,
        "type": _visible_operation_lot_type(operation_type, str(row["direction"] or "")),
        "net_amount": float(row["cash_amount"] or 0.0),
        "external": _visible_operation_external_flag(operation_type),
        "source": "snapshot_pdf_visible",
        "model_anchor": True,
        "snapshot_id": row["snapshot_id"],
        "snapshot_operation_id": row["snapshot_operation_id"],
        "operation_type": operation_type,
        "operation_label": row["operation_label"],
        "observed_units": float(row["quantity"]) if row["quantity"] is not None else None,
        "observed_unit_value": float(row["unit_value"]) if row["unit_value"] is not None else None,
        "notes": row["notes"],
    }


def _reconstruct_positions_from_pdf_sources(
    conn,
    data_dir: Path,
    *,
    db_path: Path | None = None,
    arbitration_documents: list[dict[str, Any]] | None = None,
) -> dict[str, int]:
    imported_at = datetime.now().isoformat(timespec="seconds")
    runtime = V2Runtime(data_dir, db_path=db_path, include_db_overlay=False)
    contract_rows = {
        str(row["contract_id"]): dict(row)
        for row in conn.execute(
            """
            SELECT contract_id, contract_name, insurer, holder_type
            FROM contracts
            """
        ).fetchall()
    }
    latest_reference_dates = {
        str(row["contract_id"]): str(row["reference_date"])
        for row in conn.execute(
            """
            SELECT contract_id, MAX(reference_date) AS reference_date
            FROM annual_snapshots
            GROUP BY contract_id
            """
        ).fetchall()
        if row["reference_date"] is not None
    }
    existing_positions = {
        (str(row["contract_name"]), str(row["asset_id"])): str(row["position_id"])
        for row in conn.execute(
            """
            SELECT position_id, contract_name, asset_id
            FROM positions
            """
        ).fetchall()
    }
    existing_lot_counts = {
        str(row["position_id"]): int(row["c"])
        for row in conn.execute(
            """
            SELECT position_id, COUNT(*) AS c
            FROM position_lots
            GROUP BY position_id
            """
        ).fetchall()
    }

    snapshot_rows = conn.execute(
        """
        SELECT sp.snapshot_id, sp.contract_id, sp.contract_name, sp.position_id, sp.asset_id, sp.asset_type,
               sp.valuation_date, sp.quantity, sp.unit_value, sp.official_value, sp.official_cost_basis,
               sp.official_profit_sharing_amount, sp.official_average_purchase_price, s.reference_date
        FROM snapshot_positions sp
        JOIN annual_snapshots s ON s.snapshot_id = sp.snapshot_id
        WHERE sp.asset_id IS NOT NULL
        ORDER BY sp.contract_name, sp.asset_id, s.reference_date
        """
    ).fetchall()
    visible_leg_rows = conn.execute(
        """
        SELECT sol.snapshot_operation_leg_id, sol.snapshot_operation_id, sol.snapshot_id, sol.contract_id,
               sol.contract_name, sol.position_id, sol.asset_id, sol.asset_type, sol.effective_date,
               sol.cash_amount, sol.quantity, sol.unit_value, sol.direction, sol.notes,
               so.operation_type, so.operation_label
        FROM snapshot_operation_legs_visible sol
        JOIN snapshot_operations_visible so ON so.snapshot_operation_id = sol.snapshot_operation_id
        WHERE sol.asset_id IS NOT NULL
        ORDER BY sol.contract_name, sol.asset_id, sol.effective_date, sol.snapshot_operation_leg_id
        """
    ).fetchall()

    states: dict[tuple[str, str], dict[str, Any]] = {}
    for row in snapshot_rows:
        key = (str(row["contract_id"]), str(row["asset_id"]))
        state = states.setdefault(
            key,
            {
                "contract_id": str(row["contract_id"]),
                "contract_name": str(row["contract_name"]),
                "asset_id": str(row["asset_id"]),
                "asset_type": row["asset_type"],
                "snapshot_rows": [],
                "visible_legs": [],
                "arbitration_legs": [],
            },
        )
        state["asset_type"] = state["asset_type"] or row["asset_type"]
        state["snapshot_rows"].append(dict(row))

    for row in visible_leg_rows:
        key = (str(row["contract_id"]), str(row["asset_id"]))
        state = states.setdefault(
            key,
            {
                "contract_id": str(row["contract_id"]),
                "contract_name": str(row["contract_name"]),
                "asset_id": str(row["asset_id"]),
                "asset_type": row["asset_type"],
                "snapshot_rows": [],
                "visible_legs": [],
                "arbitration_legs": [],
            },
        )
        state["asset_type"] = state["asset_type"] or row["asset_type"]
        state["visible_legs"].append(dict(row))

    for document in arbitration_documents or []:
        contract_id = str(document.get("contract_id") or "").strip()
        contract_name = str(document.get("contract_name") or "").strip()
        if not contract_id or not contract_name:
            continue
        proposal = dict(document.get("proposal") or {})
        effective_date = str(proposal.get("effective_date") or document.get("document_date") or "").strip()
        if not effective_date:
            continue
        for direction, legs in (("from", proposal.get("from_legs") or []), ("to", proposal.get("to_legs") or [])):
            for leg in legs:
                mapping = _match_snapshot_position_mapping(
                    runtime,
                    contract_name=contract_name,
                    asset_name_raw=str(leg.get("name") or leg.get("isin") or ""),
                    isin=str(leg.get("isin")) if leg.get("isin") else None,
                )
                if not mapping.get("asset_id"):
                    continue
                key = (contract_id, str(mapping["asset_id"]))
                state = states.setdefault(
                    key,
                    {
                        "contract_id": contract_id,
                        "contract_name": contract_name,
                        "asset_id": str(mapping["asset_id"]),
                        "asset_type": mapping.get("asset_type"),
                        "snapshot_rows": [],
                        "visible_legs": [],
                        "arbitration_legs": [],
                    },
                )
                state["asset_type"] = state["asset_type"] or mapping.get("asset_type")
                state["arbitration_legs"].append(
                    {
                        "document_id": document["document_id"],
                        "effective_date": effective_date,
                        "direction": direction,
                        "cash_amount": float(leg.get("amount") or 0.0),
                        "quantity": float(leg["units"]) if leg.get("units") is not None else None,
                        "unit_value": float(leg["nav"]) if leg.get("nav") is not None else None,
                        "position_id": mapping.get("position_id"),
                        "asset_id": mapping.get("asset_id"),
                        "asset_type": mapping.get("asset_type"),
                    }
                )

    positions_created = 0
    lots_created = 0
    snapshot_links_updated = 0
    for state in states.values():
        contract_row = contract_rows.get(str(state["contract_id"]))
        if contract_row is None:
            continue
        latest_snapshot = max(
            state["snapshot_rows"],
            key=lambda row: (str(row["reference_date"]), str(row["snapshot_id"])),
            default=None,
        )
        earliest_snapshot_date = min(
            (
                str(row["valuation_date"] or row["reference_date"])
                for row in state["snapshot_rows"]
                if row["valuation_date"] or row["reference_date"]
            ),
            default=None,
        )
        earliest_operation_date = min(
            (str(row["effective_date"]) for row in state["visible_legs"] if row["effective_date"]),
            default=None,
        )
        earliest_arbitration_date = min(
            (str(row["effective_date"]) for row in state["arbitration_legs"] if row["effective_date"]),
            default=None,
        )
        subscription_date = earliest_operation_date or earliest_arbitration_date or earliest_snapshot_date
        if subscription_date is None:
            continue

        position_id = (
            existing_positions.get((str(state["contract_name"]), str(state["asset_id"])))
            or next(
                (
                    str(row["position_id"])
                    for row in state["snapshot_rows"]
                    if row.get("position_id")
                ),
                None,
            )
            or next(
                (
                    str(row["position_id"])
                    for row in state["visible_legs"]
                    if row.get("position_id")
                ),
                None,
            )
            or next(
                (
                    str(row["position_id"])
                    for row in state["arbitration_legs"]
                    if row.get("position_id")
                ),
                None,
            )
            or _derive_reconstructed_position_id(str(state["contract_name"]), str(state["asset_id"]))
        )

        latest_contract_reference = latest_reference_dates.get(str(state["contract_id"]))
        is_open_on_latest_snapshot = (
            latest_snapshot is not None
            and latest_contract_reference is not None
            and str(latest_snapshot["reference_date"]) == str(latest_contract_reference)
        )
        units_held = 0.0
        if latest_snapshot is not None and is_open_on_latest_snapshot:
            if latest_snapshot["quantity"] is not None:
                units_held = float(latest_snapshot["quantity"])
            elif str(state["asset_type"] or "") == "fonds_euro":
                units_held = float(latest_snapshot["official_value"] or 0.0)

        invested_amount = None
        if latest_snapshot is not None:
            if latest_snapshot["official_cost_basis"] is not None:
                invested_amount = float(latest_snapshot["official_cost_basis"])
            elif (
                latest_snapshot["official_average_purchase_price"] is not None
                and latest_snapshot["quantity"] is not None
            ):
                invested_amount = round(
                    float(latest_snapshot["official_average_purchase_price"])
                    * float(latest_snapshot["quantity"]),
                    2,
                )
            elif (
                str(state["asset_type"] or "") == "fonds_euro"
                and latest_snapshot["official_profit_sharing_amount"] is not None
            ):
                invested_amount = round(
                    max(
                        0.0,
                        float(latest_snapshot["official_value"] or 0.0)
                        - float(latest_snapshot["official_profit_sharing_amount"] or 0.0),
                    ),
                    2,
                )
            else:
                invested_amount = float(latest_snapshot["official_value"] or 0.0)
        elif state["arbitration_legs"]:
            invested_amount = round(
                sum(
                    abs(float(row["cash_amount"] or 0.0))
                    for row in state["arbitration_legs"]
                    if str(row.get("direction") or "") == "to"
                ),
                2,
            ) or None

        if position_id not in existing_lot_counts and (str(state["contract_name"]), str(state["asset_id"])) not in existing_positions:
            conn.execute(
                """
                INSERT INTO positions (
                    position_id, asset_id, holder_type, wrapper_type, insurer, contract_name,
                    subscription_date, invested_amount, units_held, purchase_nav,
                    purchase_nav_currency, purchase_nav_source, imported_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    position_id,
                    state["asset_id"],
                    _position_holder_type(str(contract_row["holder_type"])),
                    _contract_wrapper_type(str(state["contract_name"])),
                    contract_row["insurer"],
                    state["contract_name"],
                    subscription_date,
                    invested_amount,
                    units_held,
                    float(latest_snapshot["official_average_purchase_price"])
                    if latest_snapshot is not None and latest_snapshot["official_average_purchase_price"] is not None
                    else None,
                    "EUR",
                    "pdf_snapshot",
                    imported_at,
                ),
            )
            existing_positions[(str(state["contract_name"]), str(state["asset_id"]))] = position_id
            existing_lot_counts.setdefault(position_id, 0)
            positions_created += 1

        if existing_lot_counts.get(position_id, 0) == 0 and state["visible_legs"]:
            lot_index = 0
            for row in sorted(
                state["visible_legs"],
                key=lambda item: (
                    str(item["effective_date"] or ""),
                    str(item["snapshot_operation_id"] or ""),
                    str(item["snapshot_operation_leg_id"] or ""),
                ),
            ):
                lot = _visible_operation_lot_payload(row)
                if lot is None:
                    continue
                conn.execute(
                    """
                    INSERT INTO position_lots (
                        position_id, lot_index, lot_date, raw_lot_type, raw_lot_json, imported_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        position_id,
                        lot_index,
                        lot["date"],
                        lot["type"],
                        json.dumps(lot, ensure_ascii=False, sort_keys=True, default=str),
                        imported_at,
                    ),
                )
                lot_index += 1
                lots_created += 1
            existing_lot_counts[position_id] = lot_index

        snapshot_links_updated += conn.execute(
            """
            UPDATE snapshot_positions
            SET position_id = ?
            WHERE contract_id = ?
              AND asset_id = ?
              AND (position_id IS NULL OR position_id = '')
            """,
            (
                position_id,
                state["contract_id"],
                state["asset_id"],
            ),
        ).rowcount
        snapshot_links_updated += conn.execute(
            """
            UPDATE snapshot_operation_legs_visible
            SET position_id = ?
            WHERE contract_id = ?
              AND asset_id = ?
              AND (position_id IS NULL OR position_id = '')
            """,
            (
                position_id,
                state["contract_id"],
                state["asset_id"],
            ),
        ).rowcount

    return {
        "positions": positions_created,
        "position_lots": lots_created,
        "snapshot_links_updated": snapshot_links_updated,
    }


def _apply_arbitration_document_movements(
    conn,
    data_dir: Path,
    *,
    db_path: Path | None = None,
    arbitration_documents: list[dict[str, Any]] | None = None,
) -> tuple[int, int]:
    from .arbitration import (
        _build_document_movement_rows,
        _map_legs_to_positions,
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS document_arbitration_proposals (
            document_id TEXT PRIMARY KEY,
            proposal_json TEXT NOT NULL,
            extraction_status TEXT NOT NULL,
            application_status TEXT NOT NULL,
            notes TEXT,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (document_id) REFERENCES documents(document_id)
        )
        """
    )
    runtime = V2Runtime(data_dir, db_path=db_path)
    imported_at = datetime.now().isoformat(timespec="seconds")
    conn.execute("DELETE FROM document_movements")

    created_rows = 0
    applied_documents = 0
    for document in arbitration_documents or []:
        proposal = _map_legs_to_positions(
            data_dir=data_dir,
            contract_name=str(document.get("contract_name") or "") or None,
            proposal=dict(document.get("proposal") or {}),
            db_path=db_path,
        )
        extraction_status = "ok" if proposal.get("from_legs") and proposal.get("to_legs") else "partial"
        blocking_legs = [
            str(leg.get("isin") or leg.get("name") or "unknown")
            for leg in list(proposal.get("from_legs") or []) + list(proposal.get("to_legs") or [])
            if str(leg.get("mapping_status") or "") != "matched"
            and (leg.get("amount") is not None or leg.get("units") is not None)
        ]
        application_status = "pending"
        note = "Proposition d'arbitrage extraite automatiquement."

        desired_rows: list[dict[str, Any]] = []
        if not blocking_legs and document.get("contract_id") and document.get("contract_name"):
            desired_rows = _build_document_movement_rows(
                proposal=proposal,
                contract_id=str(document["contract_id"]),
                contract_name=str(document["contract_name"]),
                document_id=str(document["document_id"]),
                runtime=runtime,
            )
            for row in desired_rows:
                conn.execute(
                    """
                    INSERT INTO document_movements (
                        document_movement_id, document_id, contract_id, contract_name, position_id,
                        asset_id, asset_name, bucket, effective_date, raw_lot_type, movement_kind,
                        cash_amount, units_delta, unit_price, external_flag, notes, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["document_movement_id"],
                        row["document_id"],
                        row["contract_id"],
                        row["contract_name"],
                        row["position_id"],
                        row["asset_id"],
                        row["asset_name"],
                        row["bucket"],
                        row["effective_date"],
                        row["raw_lot_type"],
                        row["movement_kind"],
                        row["cash_amount"],
                        row["units_delta"],
                        row["unit_price"],
                        row["external_flag"],
                        row["notes"],
                        row["created_at"],
                    ),
                )
            if desired_rows:
                application_status = "applied"
                note = f"Mouvements PDF persistés en base: created={len(desired_rows)}, skipped=0"
                created_rows += len(desired_rows)
                applied_documents += 1
        elif blocking_legs:
            note = "Arbitrage non appliqué automatiquement: supports non mappés " + ", ".join(blocking_legs)

        conn.execute(
            """
            INSERT INTO document_arbitration_proposals (
                document_id, proposal_json, extraction_status, application_status, notes, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(document_id) DO UPDATE SET
                proposal_json = excluded.proposal_json,
                extraction_status = excluded.extraction_status,
                application_status = excluded.application_status,
                notes = excluded.notes,
                updated_at = excluded.updated_at
            """,
            (
                str(document["document_id"]),
                json.dumps(proposal, ensure_ascii=False, sort_keys=True),
                extraction_status,
                application_status,
                note,
                imported_at,
            ),
        )

    return created_rows, applied_documents


def _bucket_for_asset_type(asset_type: str) -> str | None:
    if asset_type == "fonds_euro":
        return "fonds_euro"
    if asset_type in {"uc_fund", "uc_illiquid"}:
        return "uc"
    if asset_type == "structured_product":
        return "structured"
    return None


def _ledger_entry_kind(*, bucket: str, movement) -> str:
    return _ledger_entry_kind_from_values(
        bucket=bucket,
        movement_kind=movement.movement_kind.value,
        units_delta=movement.units_delta,
    )


def _ledger_entry_kind_from_values(*, bucket: str, movement_kind: str, units_delta: float | None = None) -> str:
    if movement_kind == "external_contribution":
        return "external_contribution"
    if movement_kind == "internal_capitalization":
        return "internal_credit"
    if movement_kind == "fee":
        return "fee"
    if movement_kind == "tax":
        if bucket == "structured" and (units_delta or 0) < -1:
            return "structured_redemption"
        return "tax"
    if movement_kind == "withdrawal":
        if bucket == "structured":
            return "structured_redemption"
        return "withdrawal"
    return "other"


def _is_transfer_candidate(entry: dict[str, Any]) -> bool:
    return entry["direction"] in {"credit", "debit"} and entry["entry_kind"] in {
        "external_contribution",
        "withdrawal",
        "structured_redemption",
        "other",
    }


def _copy_entry(entry: dict[str, Any], *, suffix: str, amount: float, entry_kind: str) -> dict[str, Any]:
    copied = dict(entry)
    copied["entry_id"] = f"{entry['entry_id']}::{suffix}"
    copied["amount"] = round(float(amount), 2)
    copied["entry_kind"] = entry_kind
    return copied


def _reconcile_internal_transfers(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for entry in entries:
        grouped.setdefault((entry["contract_id"], entry["entry_date"]), []).append(entry)

    reconciled: list[dict[str, Any]] = []
    for (_contract_id, _entry_date), group in grouped.items():
        candidates = [dict(entry, remaining=float(entry["amount"])) for entry in group if _is_transfer_candidate(entry)]
        others = [entry for entry in group if not _is_transfer_candidate(entry)]

        debits = [entry for entry in candidates if entry["direction"] == "debit"]
        credits = [entry for entry in candidates if entry["direction"] == "credit"]

        split_index = 0
        for debit in debits:
            for credit in credits:
                if debit["remaining"] <= 0.005 or credit["remaining"] <= 0.005:
                    continue
                if debit["bucket"] == credit["bucket"]:
                    continue
                matched = min(float(debit["remaining"]), float(credit["remaining"]))
                if matched <= 0.005:
                    continue
                split_index += 1
                reconciled.append(
                    _copy_entry(
                        debit,
                        suffix=f"transfer_out_{split_index}",
                        amount=matched,
                        entry_kind="internal_transfer_out",
                    )
                )
                reconciled.append(
                    _copy_entry(
                        credit,
                        suffix=f"transfer_in_{split_index}",
                        amount=matched,
                        entry_kind="internal_transfer_in",
                    )
                )
                debit["remaining"] -= matched
                credit["remaining"] -= matched

        for entry in candidates:
            if entry["remaining"] > 0.005:
                reconciled.append(
                    _copy_entry(
                        entry,
                        suffix="residual",
                        amount=entry["remaining"],
                        entry_kind=entry["entry_kind"],
                    )
                )

        reconciled.extend(others)

    return reconciled


def _import_contract_ledger_entries(conn, data_dir: Path, *, db_path: Path | None = None) -> int:
    runtime = V2Runtime(data_dir, db_path=db_path, include_db_overlay=False)
    normalizer = MovementNormalizer()
    imported_at = datetime.now().isoformat(timespec="seconds")
    count = 0
    raw_entries: list[dict[str, Any]] = []

    contract_ids = {
        str(row["contract_name"]): str(row["contract_id"])
        for row in conn.execute("SELECT contract_id, contract_name FROM contracts").fetchall()
    }

    for position in runtime.portfolio.list_all_positions():
        asset = runtime.portfolio.get_asset(position.asset_id)
        if asset is None:
            continue
        bucket = _bucket_for_asset_type(asset.asset_type.value)
        if bucket is None:
            continue

        contract_name = str(position.wrapper.contract_name or "")
        contract_id = contract_ids.get(contract_name)
        if not contract_id:
            continue

        movements = normalizer.normalize_lots(
            position_id=position.position_id,
            asset_id=position.asset_id,
            lots=position.investment.lots or [],
        )
        for movement in movements:
            if abs(float(movement.cash_amount or 0.0)) < 0.00001 and movement.units_delta is None:
                continue

            direction = "credit" if movement.cash_amount > 0 else "debit" if movement.cash_amount < 0 else "neutral"
            amount = abs(float(movement.cash_amount or 0.0))
            raw_entries.append(
                {
                    "entry_id": f"entry_{movement.movement_id}",
                    "contract_id": contract_id,
                    "contract_name": contract_name,
                    "position_id": position.position_id,
                    "asset_id": position.asset_id,
                    "asset_name": asset.name,
                    "bucket": bucket,
                    "entry_date": movement.effective_date.isoformat(),
                    "fiscal_year": movement.effective_date.year,
                    "direction": direction,
                    "amount": amount,
                    "units_delta": movement.units_delta,
                    "movement_kind": movement.movement_kind.value,
                    "entry_kind": _ledger_entry_kind(bucket=bucket, movement=movement),
                    "raw_lot_type": movement.raw_lot_type,
                    "external_flag": 1 if movement.external is True else 0 if movement.external is False else None,
                    "source_movement_id": movement.movement_id,
                    "raw_lot_json": json.dumps(movement.raw_lot, ensure_ascii=False, sort_keys=True, default=str),
                    "imported_at": imported_at,
                }
            )

    manual_rows = conn.execute(
        """
        SELECT manual_movement_id, contract_id, contract_name, position_id, asset_id, asset_name, bucket,
               effective_date, raw_lot_type, movement_kind, cash_amount, units_delta, unit_price,
               external_flag, linked_document_id, reason, notes, created_at
        FROM manual_movements
        ORDER BY effective_date, manual_movement_id
        """
    ).fetchall()
    for row in manual_rows:
        cash_amount = float(row["cash_amount"] or 0.0)
        units_delta = float(row["units_delta"]) if row["units_delta"] is not None else None
        if abs(cash_amount) < 0.00001 and units_delta is None:
            continue

        direction = "credit" if cash_amount > 0 else "debit" if cash_amount < 0 else "neutral"
        external_flag = row["external_flag"]
        raw_lot = {
            "date": row["effective_date"],
            "type": row["raw_lot_type"],
            "units": units_delta,
            "nav": float(row["unit_price"]) if row["unit_price"] is not None else None,
            "net_amount": cash_amount,
            "external": True if external_flag == 1 else False if external_flag == 0 else None,
            "source": "manual_v2",
            "model_anchor": True,
            "reason": row["reason"],
            "notes": row["notes"],
            "linked_document_id": row["linked_document_id"],
        }
        raw_entries.append(
            {
                "entry_id": f"entry_manual_{row['manual_movement_id']}",
                "contract_id": row["contract_id"],
                "contract_name": row["contract_name"],
                "position_id": row["position_id"],
                "asset_id": row["asset_id"],
                "asset_name": row["asset_name"],
                "bucket": row["bucket"],
                "entry_date": row["effective_date"],
                "fiscal_year": int(str(row["effective_date"])[:4]),
                "direction": direction,
                "amount": abs(cash_amount),
                "units_delta": units_delta,
                "movement_kind": str(row["movement_kind"]),
                "entry_kind": _ledger_entry_kind_from_values(
                    bucket=str(row["bucket"]),
                    movement_kind=str(row["movement_kind"]),
                    units_delta=units_delta,
                ),
                "raw_lot_type": row["raw_lot_type"],
                "external_flag": external_flag,
                "source_movement_id": f"manual:{row['manual_movement_id']}",
                "raw_lot_json": json.dumps(raw_lot, ensure_ascii=False, sort_keys=True, default=str),
                "imported_at": imported_at,
            }
        )

    document_rows = conn.execute(
        """
        SELECT document_movement_id, document_id, contract_id, contract_name, position_id, asset_id,
               asset_name, bucket, effective_date, raw_lot_type, movement_kind, cash_amount,
               units_delta, unit_price, external_flag, notes, created_at
        FROM document_movements
        ORDER BY effective_date, document_movement_id
        """
    ).fetchall()
    for row in document_rows:
        cash_amount = float(row["cash_amount"] or 0.0)
        units_delta = float(row["units_delta"]) if row["units_delta"] is not None else None
        if abs(cash_amount) < 0.00001 and units_delta is None:
            continue

        direction = "credit" if cash_amount > 0 else "debit" if cash_amount < 0 else "neutral"
        external_flag = row["external_flag"]
        raw_lot = {
            "date": row["effective_date"],
            "type": row["raw_lot_type"],
            "units": units_delta,
            "nav": float(row["unit_price"]) if row["unit_price"] is not None else None,
            "net_amount": cash_amount,
            "external": True if external_flag == 1 else False if external_flag == 0 else None,
            "source": "document_pdf",
            "model_anchor": True,
            "document_id": row["document_id"],
            "notes": row["notes"],
        }
        raw_entries.append(
            {
                "entry_id": f"entry_document_{row['document_movement_id']}",
                "contract_id": row["contract_id"],
                "contract_name": row["contract_name"],
                "position_id": row["position_id"],
                "asset_id": row["asset_id"],
                "asset_name": row["asset_name"],
                "bucket": row["bucket"],
                "entry_date": row["effective_date"],
                "fiscal_year": int(str(row["effective_date"])[:4]),
                "direction": direction,
                "amount": abs(cash_amount),
                "units_delta": units_delta,
                "movement_kind": str(row["movement_kind"]),
                "entry_kind": _ledger_entry_kind_from_values(
                    bucket=str(row["bucket"]),
                    movement_kind=str(row["movement_kind"]),
                    units_delta=units_delta,
                ),
                "raw_lot_type": row["raw_lot_type"],
                "external_flag": external_flag,
                "source_movement_id": f"document:{row['document_movement_id']}",
                "raw_lot_json": json.dumps(raw_lot, ensure_ascii=False, sort_keys=True, default=str),
                "imported_at": imported_at,
            }
        )

    interest_rows = conn.execute(
        """
        SELECT s.snapshot_id, s.contract_id, s.contract_name, s.reference_date,
               sp.position_id, sp.asset_id, a.name AS asset_name, s.official_euro_interest_net
        FROM annual_snapshots s
        JOIN snapshot_positions sp ON sp.snapshot_id = s.snapshot_id
        LEFT JOIN assets a ON a.asset_id = sp.asset_id
        WHERE sp.asset_type = 'fonds_euro'
          AND s.official_euro_interest_net IS NOT NULL
          AND ABS(s.official_euro_interest_net) > 0.005
        ORDER BY s.reference_date, s.snapshot_id
        """
    ).fetchall()
    for row in interest_rows:
        amount = abs(float(row["official_euro_interest_net"] or 0.0))
        if amount <= 0.005:
            continue
        raw_lot = {
            "date": row["reference_date"],
            "type": "buy",
            "net_amount": amount,
            "external": False,
            "source": "snapshot_pdf_interest",
            "model_anchor": True,
            "snapshot_id": row["snapshot_id"],
            "notes": "Crédit fonds euro constaté sur relevé annuel.",
        }
        raw_entries.append(
            {
                "entry_id": f"entry_snapshot_interest_{row['snapshot_id']}",
                "contract_id": row["contract_id"],
                "contract_name": row["contract_name"],
                "position_id": row["position_id"],
                "asset_id": row["asset_id"],
                "asset_name": row["asset_name"] or "Fonds euro",
                "bucket": "fonds_euro",
                "entry_date": row["reference_date"],
                "fiscal_year": int(str(row["reference_date"])[:4]),
                "direction": "credit",
                "amount": amount,
                "units_delta": None,
                "movement_kind": "internal_capitalization",
                "entry_kind": "internal_credit",
                "raw_lot_type": "buy",
                "external_flag": 0,
                "source_movement_id": f"snapshot_interest:{row['snapshot_id']}",
                "raw_lot_json": json.dumps(raw_lot, ensure_ascii=False, sort_keys=True, default=str),
                "imported_at": imported_at,
            }
        )

    conn.execute("DELETE FROM contract_ledger_entries")
    for entry in _reconcile_internal_transfers(raw_entries):
        conn.execute(
            """
            INSERT INTO contract_ledger_entries (
                entry_id, contract_id, contract_name, position_id, asset_id, asset_name, bucket,
                entry_date, fiscal_year, direction, amount, units_delta, movement_kind, entry_kind,
                raw_lot_type, external_flag, source_movement_id, raw_lot_json, imported_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry["entry_id"],
                entry["contract_id"],
                entry["contract_name"],
                entry["position_id"],
                entry["asset_id"],
                entry["asset_name"],
                entry["bucket"],
                entry["entry_date"],
                entry["fiscal_year"],
                entry["direction"],
                entry["amount"],
                entry["units_delta"],
                entry["movement_kind"],
                entry["entry_kind"],
                entry["raw_lot_type"],
                entry["external_flag"],
                entry["source_movement_id"],
                entry["raw_lot_json"],
                entry["imported_at"],
            ),
        )
        count += 1
    return count


def _collect_v2_totals(conn) -> dict[str, int]:
    return {
        "contracts": conn.execute("SELECT COUNT(*) AS c FROM contracts").fetchone()["c"],
        "documents": conn.execute("SELECT COUNT(*) AS c FROM documents").fetchone()["c"],
        "assets": conn.execute("SELECT COUNT(*) AS c FROM assets").fetchone()["c"],
        "positions": conn.execute("SELECT COUNT(*) AS c FROM positions").fetchone()["c"],
        "position_lots": conn.execute("SELECT COUNT(*) AS c FROM position_lots").fetchone()["c"],
        "snapshots": conn.execute("SELECT COUNT(*) AS c FROM annual_snapshots").fetchone()["c"],
        "snapshot_positions": conn.execute("SELECT COUNT(*) AS c FROM snapshot_positions").fetchone()["c"],
        "snapshot_operations": conn.execute("SELECT COUNT(*) AS c FROM snapshot_operations_visible").fetchone()["c"],
        "snapshot_operation_legs": conn.execute("SELECT COUNT(*) AS c FROM snapshot_operation_legs_visible").fetchone()["c"],
        "ledger_entries": conn.execute("SELECT COUNT(*) AS c FROM contract_ledger_entries").fetchone()["c"],
    }


def refresh_v2_derived_state(data_dir: Path, db_path: Path | None = None) -> dict[str, Any]:
    data_dir = Path(data_dir)
    db_path = Path(db_path or default_db_path(data_dir))
    ensure_v2_db(data_dir, db_path=db_path)

    with connect(db_path) as conn:
        asset_status_updates = _refresh_asset_lifecycle_statuses(conn, data_dir, db_path=db_path)
        ledger_entries_count = _import_contract_ledger_entries(conn, data_dir, db_path=db_path)
        totals = _collect_v2_totals(conn)

    return {
        "ok": True,
        "db_path": str(db_path),
        "imported": {
            "asset_statuses_refreshed": asset_status_updates,
            "ledger_entries_imported": ledger_entries_count,
        },
        "totals": totals,
        "imported_at": datetime.now().isoformat(timespec="seconds"),
    }


def bootstrap_v2_data(data_dir: Path, db_path: Path | None = None) -> dict[str, Any]:
    data_dir = Path(data_dir)
    db_path = Path(db_path or default_db_path(data_dir))
    init_db(db_path)

    indexes = sorted((data_dir / "documents" / "insurer").glob("**/index.yaml"))

    with connect(db_path) as conn:
        contracts_count = _import_contract_seeds(conn)
        _import_external_flow_snapshots(conn, data_dir)
        documents_count = 0
        for index_path in indexes:
            documents_count += _import_documents_from_index(conn, data_dir, index_path)
        brochures_count = _import_brochures(conn, data_dir)
        snapshots_count, snapshot_warnings = _import_statement_snapshots(conn, data_dir)
        arbitration_documents = _load_arbitration_document_payloads(conn, data_dir)
        portfolio_seed_counts = _import_portfolio_seed_state(
            conn,
            data_dir,
            arbitration_documents=arbitration_documents,
        )
        conn.commit()
        snapshot_positions_count = _import_statement_snapshot_positions(conn, data_dir, db_path=db_path)
        snapshot_operations_count, snapshot_operation_legs_count = _import_snapshot_visible_operations(
            conn,
            data_dir,
            db_path=db_path,
        )
        reconstructed_portfolio_counts = _reconstruct_positions_from_pdf_sources(
            conn,
            data_dir,
            db_path=db_path,
            arbitration_documents=arbitration_documents,
        )
        conn.commit()
        document_movements_count, arbitration_documents_applied = _apply_arbitration_document_movements(
            conn,
            data_dir,
            db_path=db_path,
            arbitration_documents=arbitration_documents,
        )
        conn.commit()
        asset_status_updates = _refresh_asset_lifecycle_statuses(conn, data_dir, db_path=db_path)
        ledger_entries_count = _import_contract_ledger_entries(conn, data_dir, db_path=db_path)
        conn.execute(
            """
            INSERT INTO app_meta (meta_key, meta_value)
            VALUES ('bootstrap_version', ?)
            ON CONFLICT(meta_key) DO UPDATE SET
                meta_value = excluded.meta_value
            """,
            (BOOTSTRAP_DB_VERSION,),
        )
        totals = _collect_v2_totals(conn)

    return {
        "ok": True,
        "db_path": str(db_path),
        "imported": {
            "contracts_seeded": contracts_count,
            "documents_indexed": documents_count,
            "brochures_indexed": brochures_count,
            "snapshots_imported": snapshots_count,
            "portfolio_assets_seeded": portfolio_seed_counts["assets"],
            "portfolio_positions_seeded": portfolio_seed_counts["positions"],
            "portfolio_lots_seeded": portfolio_seed_counts["position_lots"],
            "portfolio_positions_reconstructed_from_pdf": reconstructed_portfolio_counts["positions"],
            "portfolio_lots_reconstructed_from_pdf": reconstructed_portfolio_counts["position_lots"],
            "document_movements_applied": document_movements_count,
            "arbitration_documents_applied": arbitration_documents_applied,
            "snapshot_position_links_backfilled": reconstructed_portfolio_counts["snapshot_links_updated"],
            "asset_statuses_refreshed": asset_status_updates,
            "snapshot_positions_imported": snapshot_positions_count,
            "snapshot_operations_imported": snapshot_operations_count,
            "snapshot_operation_legs_imported": snapshot_operation_legs_count,
            "ledger_entries_imported": ledger_entries_count,
        },
        "totals": totals,
        "warnings": {
            "statement_snapshots": snapshot_warnings,
        },
        "imported_at": datetime.now().isoformat(timespec="seconds"),
    }
