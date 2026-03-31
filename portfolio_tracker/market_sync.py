"""Synchronisation marché V2 autonome."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional
import csv
import json
import unicodedata
import re
import ssl
import subprocess
import urllib.request
from urllib.parse import urlencode, urlsplit

from .headless import headless_get_response_text, headless_get_text
from .models import PortfolioData
from .providers import load_nav_sources_cfg
from .storage import default_db_path, upsert_market_series_points


@dataclass(frozen=True)
class NavPoint:
    point_date: date
    value: float
    currency: str = "EUR"
    source: str | None = None


@dataclass(frozen=True)
class FetchedNav:
    value: float
    nav_date: date
    currency: str = "EUR"
    source: str = "auto"
    quantalys_rating: int | None = None
    quantalys_category: str | None = None


@dataclass(frozen=True)
class FetchResult:
    source: str
    identifier: str
    points: list[tuple[date, float]]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class NavUpdateResult:
    asset_id: str
    target_date: date
    status: str
    message: str
    changed: bool = False


@dataclass(frozen=True)
class NavBackfillResult:
    asset_id: str
    status: str
    message: str
    points_fetched: int = 0
    points_changed: int = 0


def _http_get_text(url: str, headers: dict[str, str] | None = None, timeout_s: int = 30, *, headless: bool = False) -> str:
    req = urllib.request.Request(url, headers=headers or {})
    cafile = None
    try:
        ctx = ssl.create_default_context()
        try:
            import certifi  # type: ignore

            cafile = certifi.where()
            ctx = ssl.create_default_context(cafile=cafile)
        except Exception:
            pass
        with urllib.request.urlopen(req, timeout=timeout_s, context=ctx) as resp:
            raw = resp.read()
            try:
                return raw.decode("utf-8")
            except Exception:
                return raw.decode("latin-1", errors="ignore")
    except Exception:
        try:
            cmd = ["curl", "-fsSL", "--max-time", str(timeout_s)]
            if cafile:
                cmd += ["--cacert", cafile]
            for key, value in (headers or {}).items():
                cmd += ["-H", f"{key}: {value}"]
            cmd.append(url)
            raw = subprocess.check_output(cmd)
            try:
                return raw.decode("utf-8")
            except Exception:
                return raw.decode("latin-1", errors="ignore")
        except Exception:
            if not headless:
                raise
        return headless_get_text(url)


def _http_get_bytes(url: str, headers: dict[str, str] | None = None, timeout_s: int = 30, *, headless: bool = False) -> bytes:
    req = urllib.request.Request(url, headers=headers or {})
    try:
        import certifi  # type: ignore

        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout_s, context=ctx) as resp:
            return resp.read()
    except Exception:
        if not headless:
            raise
        return headless_get_response_text(url).encode("utf-8", errors="replace")


def _http_post_text(
    url: str,
    *,
    body: dict[str, Any],
    headers: dict[str, str] | None = None,
    timeout_s: int = 30,
) -> str:
    raw_body = json.dumps(body).encode("utf-8")
    req_headers = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=raw_body, headers=req_headers, method="POST")
    cafile = None
    try:
        ctx = ssl.create_default_context()
        try:
            import certifi  # type: ignore

            cafile = certifi.where()
            ctx = ssl.create_default_context(cafile=cafile)
        except Exception:
            pass
        with urllib.request.urlopen(req, timeout=timeout_s, context=ctx) as resp:
            raw = resp.read()
            try:
                return raw.decode("utf-8")
            except Exception:
                return raw.decode("latin-1", errors="ignore")
    except Exception:
        cmd = ["curl", "-fsSL", "--max-time", str(timeout_s), "-X", "POST", "-d", raw_body.decode("utf-8")]
        if cafile:
            cmd += ["--cacert", cafile]
        for key, value in req_headers.items():
            cmd += ["-H", f"{key}: {value}"]
        cmd.append(url)
        raw = subprocess.check_output(cmd)
        try:
            return raw.decode("utf-8")
        except Exception:
            return raw.decode("latin-1", errors="ignore")


def _get_path(obj: Any, path: str) -> Any:
    cur = obj
    for part in str(path).split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list):
            try:
                idx = int(part)
            except Exception as exc:
                raise KeyError(f"Index list invalide: {part}") from exc
            cur = cur[idx] if 0 <= idx < len(cur) else None
        else:
            return None
    return cur


def _parse_date_any(raw: Any, *, fmt: str | None = None) -> date:
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, str):
        if fmt:
            return datetime.strptime(raw.strip(), fmt).date()
        return datetime.fromisoformat(raw.strip()).date()
    raise ValueError(f"Date invalide: {raw!r}")


def _strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _parse_natixis_date(raw: str) -> date | None:
    text = _strip_accents(str(raw or "")).lower().strip()
    text = re.sub(r"\s+", " ", text)
    text = text.replace(".", "")

    slash_match = re.search(r"(\d{2})/(\d{2})/(\d{4})", text)
    if slash_match:
        return datetime.strptime("/".join(slash_match.groups()), "%d/%m/%Y").date()

    textual_match = re.search(r"(\d{1,2})\s+([a-z]+)\s+(\d{4})", text)
    if not textual_match:
        return None

    month_map = {
        "jan": 1,
        "janv": 1,
        "janvier": 1,
        "feb": 2,
        "fev": 2,
        "fevr": 2,
        "fevrier": 2,
        "mar": 3,
        "mars": 3,
        "apr": 4,
        "avr": 4,
        "avril": 4,
        "may": 5,
        "mai": 5,
        "jun": 6,
        "juin": 6,
        "jul": 7,
        "juil": 7,
        "juillet": 7,
        "aug": 8,
        "aou": 8,
        "aout": 8,
        "sep": 9,
        "sept": 9,
        "septembre": 9,
        "oct": 10,
        "octobre": 10,
        "nov": 11,
        "novembre": 11,
        "dec": 12,
        "decembre": 12,
    }
    day_str, month_str, year_str = textual_match.groups()
    month = month_map.get(month_str)
    if month is None:
        return None
    return date(int(year_str), month, int(day_str))


def _parse_devalue_payload(text: str) -> Any:
    table = json.loads(text)
    if not isinstance(table, list) or not table:
        raise ValueError("Payload devalue invalide")

    memo: dict[int, Any] = {}

    def hydrate_ref(index: int) -> Any:
        if index in memo:
            return memo[index]
        if index < 0 or index >= len(table):
            raise ValueError(f"Reference devalue hors borne: {index}")
        value = table[index]
        if isinstance(value, dict):
            obj: dict[str, Any] = {}
            memo[index] = obj
            for key, child in value.items():
                obj[key] = hydrate_node(child, True)
            return obj
        if isinstance(value, list):
            arr: list[Any] = []
            memo[index] = arr
            for child in value:
                arr.append(hydrate_node(child, True))
            return arr
        memo[index] = value
        return value

    def hydrate_node(node: Any, as_ref: bool) -> Any:
        if as_ref and isinstance(node, int):
            return hydrate_ref(node)
        if isinstance(node, list):
            return [hydrate_node(child, True) for child in node]
        if isinstance(node, dict):
            return {key: hydrate_node(child, True) for key, child in node.items()}
        return node

    return hydrate_ref(0)


def _market_db_path(market_data_dir: Path) -> Path:
    return default_db_path(Path(market_data_dir).parent)


def upsert_nav_point(*, market_data_dir: Path, identifier: str, point: NavPoint) -> bool:
    changed = upsert_market_series_points(
        _market_db_path(market_data_dir),
        kind="uc",
        identifier=identifier,
        points=[{"date": point.point_date, "value": float(point.value), "currency": point.currency, "source": point.source}],
    )
    return changed > 0


def upsert_nav_history(*, market_data_dir: Path, identifier: str, points: list[NavPoint]) -> int:
    return upsert_market_series_points(
        _market_db_path(market_data_dir),
        kind="uc",
        identifier=identifier,
        points=[{"date": p.point_date, "value": float(p.value), "currency": p.currency, "source": p.source} for p in points],
    )


def _parse_set_values(set_pairs: Optional[list[str]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for raw in set_pairs or []:
        if "=" not in str(raw):
            raise ValueError(f"--set invalide (attendu asset_id=value): {raw!r}")
        key, value = str(raw).split("=", 1)
        out[key.strip()] = float(value.strip().replace(",", "."))
    return out


def fetch_nav_for_asset_id(
    *,
    market_data_dir: Path,
    asset_id: str,
    target_date: date,
    force_headless: bool = False,
) -> FetchedNav | None:
    cfg = load_nav_sources_cfg(market_data_dir).get(asset_id)
    if not cfg:
        return None
    kind = str(cfg.get("kind") or "").strip()
    url = str(cfg.get("url") or "").strip()
    if not kind or not url:
        return None
    currency = str(cfg.get("currency") or "EUR")
    source_label = str(cfg.get("source") or kind)
    timeout_s = int(cfg.get("timeout_s", 30))
    user_agent = str(cfg.get("user_agent") or "portfolio-tracker/1.0")
    headless = bool(cfg.get("headless", False)) or bool(force_headless)

    if kind == "url_json":
        if headless:
            raw = headless_get_response_text(url)
        else:
            raw = _http_get_text(url, headers={"User-Agent": user_agent}, timeout_s=timeout_s)
        obj = json.loads(raw)
        value_path = cfg.get("value_path")
        if not value_path:
            raise ValueError(f"{asset_id}: value_path manquant pour url_json")
        raw_val = _get_path(obj, str(value_path))
        if raw_val is None:
            raise ValueError(f"{asset_id}: value_path introuvable: {value_path}")
        nav_date = target_date
        if cfg.get("date_path"):
            raw_date = _get_path(obj, str(cfg.get("date_path")))
            nav_date = _parse_date_any(raw_date, fmt=cfg.get("date_format"))
            if nav_date > target_date:
                nav_date = target_date
        return FetchedNav(value=float(str(raw_val).replace(",", ".")), nav_date=nav_date, currency=currency, source=source_label)

    if kind == "url_csv":
        content = _http_get_bytes(url, headers={"User-Agent": user_agent}, timeout_s=timeout_s, headless=headless)
        text = content.decode("utf-8", errors="replace")
        delimiter = str(cfg.get("delimiter") or ",")
        date_col = str(cfg.get("date_column") or "date")
        value_col = str(cfg.get("value_column") or "value")
        date_fmt = cfg.get("date_format")
        decimal = str(cfg.get("decimal") or ".")
        best: tuple[date, float] | None = None
        for row in csv.DictReader(text.splitlines(), delimiter=delimiter):
            if not isinstance(row, dict) or date_col not in row or value_col not in row:
                continue
            try:
                current_date = _parse_date_any(row[date_col], fmt=date_fmt)
                if current_date > target_date:
                    continue
                raw_value = str(row[value_col]).strip()
                if decimal == ",":
                    raw_value = raw_value.replace(",", ".")
                current_value = float(raw_value)
            except Exception:
                continue
            if best is None or current_date > best[0]:
                best = (current_date, current_value)
        if best is None:
            return None
        return FetchedNav(value=best[1], nav_date=best[0], currency=currency, source=source_label)

    if kind == "html_regex":
        if headless:
            html = headless_get_text(url)
        else:
            html = _http_get_text(url, headers={"User-Agent": user_agent}, timeout_s=timeout_s, headless=headless)
        html = html.replace("&nbsp;", " ").replace("\u00A0", " ")
        value_re = cfg.get("value_regex")
        if not value_re:
            raise ValueError(f"{asset_id}: value_regex manquant pour html_regex")
        quantalys_rating = None
        quantalys_category = None
        if "quantalys.com" in url.lower():
            for pattern in (
                r'data-rating["\s]*=["\s]*(\d)',
                r'note["\s]*:["\s]*(\d)',
                r'Note\s+Quantalys[^0-9]{0,50}?(\d)\s*(?:/\s*5|étoiles?|stars?)',
                r'(?:star|rating|note)[-_](\d)',
                r'Quantalys[^0-9]{1,100}?([1-5])(?:\s|<|/)',
            ):
                for match in re.finditer(pattern, html, flags=re.IGNORECASE):
                    try:
                        candidate = int(match.group(1))
                    except Exception:
                        continue
                    if 1 <= candidate <= 5:
                        quantalys_rating = candidate
                        break
                if quantalys_rating is not None:
                    break
            for pattern in (
                r'>Catégorie[^<>]*?:\s*([^<>{}"]+?)\s*<',
                r'>Classification[^<>]*?:\s*([^<>{}"]+?)\s*<',
                r'(?:title|alt)="Catégorie[^"]*?:\s*([^"]{5,80})"',
                r'Catégorie[^:]{0,20}:\s*([A-ZÀ-Ÿa-zà-ÿ][A-ZÀ-Ÿa-zà-ÿ\s/\-]{5,60})(?:\s*(?:<|$|\|))',
                r'"category"\s*:\s*"([^"]{5,80})"',
                r'"categorie"\s*:\s*"([^"]{5,80})"',
            ):
                match = re.search(pattern, html, flags=re.IGNORECASE)
                if not match:
                    continue
                category = re.sub(r"\s+", " ", match.group(1)).strip(' \t\n\r<>"\'')
                if 5 <= len(category) <= 80 and "http" not in category.lower() and "<" not in category and ">" not in category:
                    quantalys_category = category
                    break
        matches = sorted(re.finditer(str(value_re), html, flags=re.IGNORECASE | re.MULTILINE), key=lambda match: len(match.group(1)), reverse=True)
        nav_value = None
        for match in matches:
            raw_value = str(match.group(1)).strip()
            has_comma = "," in raw_value
            has_dot = "." in raw_value
            has_space = " " in raw_value
            if has_comma and has_dot:
                comma_pos = raw_value.rfind(",")
                dot_pos = raw_value.rfind(".")
                if dot_pos < comma_pos and has_space:
                    raw_value = raw_value.replace(".", "").replace(",", ".").replace(" ", "")
                else:
                    continue
            elif has_comma:
                raw_value = raw_value.replace(" ", "").replace(",", ".")
            else:
                raw_value = raw_value.replace(",", "").replace(" ", "")
            try:
                candidate = float(raw_value)
                if 10 <= candidate <= 100000:
                    nav_value = candidate
                    break
            except Exception:
                continue
        if nav_value is None:
            raise ValueError(f"{asset_id}: aucune valeur valide trouvee")
        nav_date = target_date
        if cfg.get("date_regex"):
            date_match = re.search(str(cfg.get("date_regex")), html, flags=re.IGNORECASE | re.MULTILINE)
            if date_match:
                nav_date = _parse_date_any(date_match.group(1), fmt=cfg.get("date_format"))
                if nav_date > target_date:
                    nav_date = target_date
        return FetchedNav(
            value=nav_value,
            nav_date=nav_date,
            currency=currency,
            source=source_label,
            quantalys_rating=quantalys_rating,
            quantalys_category=quantalys_category,
        )

    raise ValueError(f"{asset_id}: kind non supporte: {kind}")


def _fetch_morningstar_history(sec_id: str, start_date: date, end_date: date, currency: str = "EUR") -> list[FetchedNav]:
    url = (
        "https://tools.morningstar.fr/api/rest.svc/timeseries_price/klr5zyak8x"
        f"?id={sec_id}&currencyId=EUR&idtype=Morningstar&frequency=daily"
        f"&startDate={start_date.isoformat()}&endDate={end_date.isoformat()}&outputType=COMPACTJSON"
    )
    data = json.loads(_http_get_text(url, headers={"User-Agent": "Mozilla/5.0"}))
    out: list[FetchedNav] = []
    for item in data:
        if not isinstance(item, list) or len(item) != 2:
            continue
        try:
            nav_date = datetime.utcfromtimestamp(float(item[0]) / 1000).date()
            nav_value = round(float(item[1]), 4)
        except Exception:
            continue
        if start_date <= nav_date <= end_date:
            out.append(FetchedNav(value=nav_value, nav_date=nav_date, currency=currency, source="morningstar"))
    return out


def fetch_nav_history_for_asset_id(
    *,
    market_data_dir: Path,
    asset_id: str,
    start_date: date | None = None,
    end_date: Optional[date] = None,
    force_headless: bool = False,
) -> list[FetchedNav]:
    cfg = load_nav_sources_cfg(market_data_dir).get(asset_id)
    if not cfg:
        return []
    kind = str(cfg.get("kind") or "").strip()
    url = str(cfg.get("url") or "").strip()
    if not kind or not url:
        return []
    end_date = end_date or date.today()
    currency = str(cfg.get("currency") or "EUR")
    source_label = str(cfg.get("source") or kind)
    timeout_s = int(cfg.get("timeout_s", 30))
    user_agent = str(cfg.get("user_agent") or "portfolio-tracker/1.0")
    headless = bool(cfg.get("headless", False)) or bool(force_headless)

    if kind == "url_csv":
        text = _http_get_bytes(url, headers={"User-Agent": user_agent}, timeout_s=timeout_s, headless=headless).decode("utf-8", errors="replace")
        delimiter = str(cfg.get("delimiter") or ",")
        date_col = str(cfg.get("date_column") or "date")
        value_col = str(cfg.get("value_column") or "value")
        date_fmt = cfg.get("date_format")
        decimal = str(cfg.get("decimal") or ".")
        by_date: dict[date, float] = {}
        for row in csv.DictReader(text.splitlines(), delimiter=delimiter):
            if not isinstance(row, dict) or date_col not in row or value_col not in row:
                continue
            try:
                current_date = _parse_date_any(row[date_col], fmt=date_fmt)
                if (start_date and current_date < start_date) or current_date > end_date:
                    continue
                raw_value = str(row[value_col]).strip()
                if decimal == ",":
                    raw_value = raw_value.replace(",", ".")
                by_date[current_date] = float(raw_value)
            except Exception:
                continue
        return [FetchedNav(value=value, nav_date=current_date, currency=currency, source=source_label) for current_date, value in sorted(by_date.items())]

    if kind == "url_json":
        if headless:
            raw = headless_get_response_text(url)
        else:
            raw = _http_get_text(url, headers={"User-Agent": user_agent}, timeout_s=timeout_s)
        obj = json.loads(raw)
        history_path = cfg.get("history_path")
        rows_obj = _get_path(obj, str(history_path)) if history_path else obj
        if isinstance(rows_obj, list):
            date_path = str(cfg.get("history_date_path") or cfg.get("date_path") or "date")
            value_path = str(cfg.get("history_value_path") or cfg.get("value_path") or "value")
            by_date: dict[date, float] = {}
            for row in rows_obj:
                if not isinstance(row, (dict, list)):
                    continue
                try:
                    current_date = _parse_date_any(_get_path(row, date_path), fmt=cfg.get("date_format"))
                    if (start_date and current_date < start_date) or current_date > end_date:
                        continue
                    current_value = float(str(_get_path(row, value_path)).replace(",", "."))
                except Exception:
                    continue
                by_date[current_date] = current_value
            if by_date:
                return [FetchedNav(value=value, nav_date=current_date, currency=currency, source=source_label) for current_date, value in sorted(by_date.items())]

    morningstar_secid = cfg.get("morningstar_secid")
    if morningstar_secid:
        history_start_date = start_date or date(1900, 1, 1)
        return _fetch_morningstar_history(str(morningstar_secid), history_start_date, end_date, currency=currency)

    single = fetch_nav_for_asset_id(
        market_data_dir=market_data_dir,
        asset_id=asset_id,
        target_date=end_date,
        force_headless=force_headless,
    )
    if single and (start_date is None or start_date <= single.nav_date) and single.nav_date <= end_date:
        return [single]
    return []


def update_uc_navs(
    *,
    portfolio: PortfolioData,
    market_data_dir: Path,
    target_date: date,
    set_values: Optional[list[str]] = None,
    headless: bool = False,
    include_historical: bool = False,
    asset_ids: Optional[set[str]] = None,
) -> tuple[list[NavUpdateResult], bool]:
    set_map = _parse_set_values(set_values)
    results: list[NavUpdateResult] = []
    positions_changed = False
    seen_assets: set[str] = set()

    def _fetch_and_store_asset(asset) -> None:
        if asset.asset_id in set_map:
            fetched = FetchedNav(value=set_map[asset.asset_id], nav_date=target_date, currency="EUR", source="manual")
        else:
            fetched = fetch_nav_for_asset_id(
                market_data_dir=market_data_dir,
                asset_id=asset.asset_id,
                target_date=target_date,
                force_headless=headless,
            )
            if fetched is None:
                results.append(NavUpdateResult(asset_id=asset.asset_id, target_date=target_date, status="skipped", message="VL du jour manquante", changed=False))
                return
        try:
            changed = upsert_nav_point(
                market_data_dir=market_data_dir,
                identifier=asset.asset_id,
                point=NavPoint(point_date=fetched.nav_date, value=float(fetched.value), currency="EUR", source=fetched.source),
            )
            if fetched.quantalys_rating is not None or fetched.quantalys_category is not None:
                try:
                    isin = asset.isin
                    if isin:
                        ratings_path = Path(market_data_dir)
                        from .providers import QuantalysProvider

                        QuantalysProvider(ratings_path).upsert_rating(
                            isin=isin,
                            name=asset.name,
                            rating=fetched.quantalys_rating,
                            category=fetched.quantalys_category,
                            update_date=fetched.nav_date,
                        )
                except Exception:
                    pass
            results.append(NavUpdateResult(asset_id=asset.asset_id, target_date=target_date, status="ok", message=f"VL enregistree ({fetched.source}) {fetched.nav_date.isoformat()}: {fetched.value}", changed=changed))
        except Exception as exc:
            results.append(NavUpdateResult(asset_id=asset.asset_id, target_date=target_date, status="error", message=f"Erreur enregistrement VL: {exc}", changed=False))

    for position in portfolio.list_all_positions():
        asset = portfolio.get_asset(position.asset_id)
        if not asset or asset.asset_type.value not in {"uc_fund", "uc_illiquid"}:
            continue
        if asset.valuation_engine.value != "mark_to_market" and not (asset_ids and asset.asset_id in asset_ids):
            continue
        if asset_ids and asset.asset_id not in asset_ids:
            continue
        if asset.asset_id in seen_assets:
            continue
        if not include_historical and (asset.metadata or {}).get("status") == "historical":
            continue
        seen_assets.add(asset.asset_id)
        if position.investment.purchase_nav is None and position.investment.invested_amount and position.investment.units_held:
            try:
                if float(position.investment.units_held) != 0:
                    position.investment.purchase_nav = float(position.investment.invested_amount) / float(position.investment.units_held)
                    position.investment.purchase_nav_source = "derived"
                    positions_changed = True
            except Exception:
                pass
        if position.investment.purchase_nav is not None:
            try:
                upsert_nav_point(
                    market_data_dir=market_data_dir,
                    identifier=asset.asset_id,
                    point=NavPoint(
                        point_date=position.investment.subscription_date,
                        value=float(position.investment.purchase_nav),
                        currency=str(position.investment.purchase_nav_currency or "EUR"),
                        source="purchase_nav",
                    ),
                )
            except Exception as exc:
                results.append(NavUpdateResult(asset_id=asset.asset_id, target_date=target_date, status="error", message=f"Erreur init purchase_nav dans l'historique: {exc}"))
                continue
        _fetch_and_store_asset(asset)

    for asset_id in sorted(asset_ids or set()):
        if asset_id in seen_assets:
            continue
        asset = portfolio.get_asset(asset_id)
        if not asset or asset.asset_type.value not in {"uc_fund", "uc_illiquid"}:
            continue
        if asset.valuation_engine.value != "mark_to_market" and not (asset_ids and asset.asset_id in asset_ids):
            continue
        if not include_historical and (asset.metadata or {}).get("status") == "historical":
            continue
        seen_assets.add(asset.asset_id)
        _fetch_and_store_asset(asset)
    return results, positions_changed


def backfill_uc_navs(
    *,
    portfolio: PortfolioData,
    market_data_dir: Path,
    start_date: date | None,
    end_date: date,
    headless: bool = False,
    include_historical: bool = False,
    asset_ids: Optional[set[str]] = None,
) -> list[NavBackfillResult]:
    results: list[NavBackfillResult] = []
    seen_assets: set[str] = set()

    def _backfill_asset(asset) -> None:
        try:
            fetched_points = fetch_nav_history_for_asset_id(
                market_data_dir=market_data_dir,
                asset_id=asset.asset_id,
                start_date=start_date,
                end_date=end_date,
                force_headless=headless,
            )
            if not fetched_points:
                results.append(NavBackfillResult(asset_id=asset.asset_id, status="skipped", message="Pas d'historique recuperable avec la source actuelle."))
                return
            changed_count = upsert_nav_history(
                market_data_dir=market_data_dir,
                identifier=asset.asset_id,
                points=[
                    NavPoint(
                        point_date=point.nav_date,
                        value=float(point.value),
                        currency=str(point.currency or "EUR"),
                        source=point.source or "auto_history",
                    )
                    for point in fetched_points
                ],
            )
            results.append(NavBackfillResult(asset_id=asset.asset_id, status="ok", message=f"Backfill termine ({changed_count} nouveau(x)/modifie(s)).", points_fetched=len(fetched_points), points_changed=changed_count))
        except Exception as exc:
            results.append(NavBackfillResult(asset_id=asset.asset_id, status="error", message=f"Erreur backfill: {exc}"))

    for position in portfolio.list_all_positions():
        asset = portfolio.get_asset(position.asset_id)
        if not asset or asset.asset_type.value not in {"uc_fund", "uc_illiquid"}:
            continue
        if asset.valuation_engine.value != "mark_to_market" and not (asset_ids and asset.asset_id in asset_ids):
            continue
        if asset_ids and asset.asset_id not in asset_ids:
            continue
        if asset.asset_id in seen_assets:
            continue
        if not include_historical and (asset.metadata or {}).get("status") == "historical":
            continue
        seen_assets.add(asset.asset_id)
        _backfill_asset(asset)

    for asset_id in sorted(asset_ids or set()):
        if asset_id in seen_assets:
            continue
        asset = portfolio.get_asset(asset_id)
        if not asset or asset.asset_type.value not in {"uc_fund", "uc_illiquid"}:
            continue
        if asset.valuation_engine.value != "mark_to_market" and not (asset_ids and asset.asset_id in asset_ids):
            continue
        if not include_historical and (asset.metadata or {}).get("status") == "historical":
            continue
        seen_assets.add(asset.asset_id)
        _backfill_asset(asset)
    return results


def fetch_solactive_indexhistory(url: str, identifier: str, *, headless: bool = False) -> FetchResult:
    split = urlsplit(url)
    origin = f"{split.scheme}://{split.netloc}" if split.scheme and split.netloc else "https://www.solactive.com"
    action_url = f"{origin}/_actions/getDayHistoryChartData/"
    referer = f"{origin}/index/{identifier}/"
    text = _http_post_text(
        action_url,
        body={
            "isin": identifier,
            "indexCreatingTimeStamp": 0,
            "dayDate": int(datetime.now().timestamp() * 1000),
        },
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; portfolio-tracker/1.0)",
            "Accept": "application/json+devalue,application/json,text/plain,*/*",
            "Origin": origin,
            "Referer": referer,
        },
    )
    try:
        data = _parse_devalue_payload(text)
    except Exception as exc:
        raise ValueError(f"Solactive: reponse non exploitable pour {identifier}") from exc

    if not isinstance(data, list):
        raise ValueError(f"Solactive: reponse non exploitable pour {identifier}")
    points = []
    for row in data:
        if not isinstance(row, dict) or row.get("timestamp") is None or row.get("value") is None:
            continue
        try:
            points.append((datetime.utcfromtimestamp(int(row["timestamp"]) / 1000.0).date(), float(row["value"])))
        except Exception:
            continue
    points.sort(key=lambda item: item[0])
    if not points:
        raise ValueError(f"Solactive: aucun point historique pour {identifier}")
    return FetchResult(source="solactive", identifier=identifier, points=points, metadata={"url": referer, "action_url": action_url, "points_count": len(points)})


def fetch_euronext_recent_history(identifier: str, *, headless: bool = False) -> FetchResult:
    url_csv = f"https://live.euronext.com/fr/ajax/AwlHistoricalPrice/getFullDownloadAjax/{identifier}?format=csv&decimal_separator=.&date_form=d/m/Y"
    csv_text = _http_get_text(
        url_csv,
        headers={"User-Agent": "portfolio-tracker/1.0 (personal project)", "Accept": "text/csv,*/*"},
        headless=headless,
    )
    points = []
    for line in csv_text.splitlines():
        line = line.lstrip("\ufeff").strip()
        if not re.match(r"\d{2}/\d{2}/\d{4}", line):
            continue
        parts = [part.strip().strip('"') for part in line.split(";")]
        if len(parts) < 6:
            continue
        try:
            current_date = datetime.strptime(parts[0], "%d/%m/%Y").date()
            close_val = float(parts[5]) if parts[5] else None
        except Exception:
            continue
        if close_val is not None:
            points.append((current_date, close_val))
    if not points:
        raise ValueError(f"Aucun historique Euronext pour {identifier}")
    by_date = {current_date: value for current_date, value in points}
    return FetchResult(source="euronext", identifier=identifier, points=sorted(by_date.items()), metadata={"url": url_csv, "points_count": len(by_date), "format": "csv"})


def fetch_merqube_indexhistory(
    name: str,
    *,
    metric: str = "total_return",
    start_date: date | None = None,
    end_date: date | None = None,
    headless: bool = False,
) -> FetchResult:
    params: dict[str, str] = {"names": str(name), "metrics": str(metric), "format": "records"}
    if start_date is not None:
        params["start_date"] = start_date.isoformat()
    if end_date is not None:
        params["end_date"] = end_date.isoformat()
    url = "https://api.merqube.com/security/index?" + urlencode(params)
    data = json.loads(
        _http_get_text(
            url,
            headers={"User-Agent": "portfolio-tracker/1.0 (personal project)", "Accept": "application/json,text/plain,*/*"},
            headless=headless,
        )
    )
    points = []
    if isinstance(data, list):
        for row in data:
            if not isinstance(row, dict):
                continue
            raw_date = row.get("date") or row.get("day") or row.get("as_of") or row.get("timestamp")
            raw_value = row.get("value") or row.get(metric) or row.get("level") or row.get("index_level")
            if raw_date is None or raw_value is None:
                continue
            try:
                if isinstance(raw_date, (int, float)) or (isinstance(raw_date, str) and raw_date.isdigit()):
                    ts = int(raw_date)
                    current_date = datetime.utcfromtimestamp(ts / 1000.0).date() if ts > 10_000_000_000 else datetime.utcfromtimestamp(ts).date()
                else:
                    current_date = datetime.fromisoformat(str(raw_date).replace("Z", "+00:00")).date()
                points.append((current_date, float(raw_value)))
            except Exception:
                continue
    by_date = {current_date: value for current_date, value in points}
    merged = sorted(by_date.items())
    return FetchResult(source="merqube", identifier=str(name), points=merged, metadata={"url": url, "metric": metric, "points_count": len(merged), "source_page": f"https://merqube.com/indices/{name}"})


def fetch_natixis_index(url: str, identifier: str, *, headless: bool = False) -> FetchResult:
    html = _http_get_text(
        url,
        headers={"User-Agent": "portfolio-tracker/1.0 (personal project)", "Accept": "text/html,*/*"},
        headless=headless,
    )
    points = []
    series_pattern = re.compile(
        r"(?:let\s+)?serieIndice\s*=\s*(\[\[.*?\]\])\s*;",
        re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    series_match = series_pattern.search(html)
    if series_match:
        try:
            raw_series = json.loads(series_match.group(1))
            for item in raw_series:
                if not isinstance(item, list) or len(item) < 2:
                    continue
                raw_ts, raw_value = item[0], item[1]
                try:
                    ts = int(raw_ts)
                    current_date = datetime.utcfromtimestamp(ts / 1000.0).date() if ts > 10_000_000_000 else datetime.utcfromtimestamp(ts).date()
                    points.append((current_date, float(raw_value)))
                except Exception:
                    continue
        except Exception:
            points = []

    block_pattern = re.compile(
        r"Niveau\s+de\s+l['’]indice\s*</p>\s*<p[^>]*>\s*([0-9]+(?:[.,][0-9]+)?)\s*€?\s*</p>\s*<p[^>]*>\s*(.*?)\s*</p>",
        re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    block_match = block_pattern.search(html)
    level_val = None
    val_date = None
    if block_match:
        level_val = float(block_match.group(1).replace(",", "."))
        val_date = _parse_natixis_date(block_match.group(2))
    else:
        level_pattern = re.compile(
            r"(?:Niveau\s+de\s+l['’]indice|Niveau\s+d['’]indice)[^0-9]{0,200}?([0-9]+(?:[.,][0-9]+)?)\s*€?",
            re.IGNORECASE | re.MULTILINE | re.DOTALL,
        )
        date_pattern = re.compile(
            r"(?:Date\s+de\s+derni[èe]re\s+valorisation|Derni[èe]re\s+valorisation|au)\s*[:\-]?\s*"
            r"((?:\d{2}/\d{2}/\d{4})|(?:\d{1,2}\s+[A-Za-zÀ-ÿ.]+\s+\d{4}))",
            re.IGNORECASE | re.MULTILINE,
        )
        level_match = level_pattern.search(html)
        level_val = float(level_match.group(1).replace(",", ".")) if level_match else None
        date_match = date_pattern.search(html)
        val_date = _parse_natixis_date(date_match.group(1)) if date_match else None
    if level_val is not None and val_date is not None and not any(point_date == val_date for point_date, _ in points):
        points.append((val_date, level_val))
    by_date = {current_date: value for current_date, value in points}
    merged = sorted(by_date.items())
    return FetchResult(source="natixis", identifier=identifier, points=merged, metadata={"url": url, "points_count": len(merged)})


def fetch_investing_rate(url: str, identifier: str, *, headless: bool = False) -> FetchResult:
    html = _http_get_text(
        url,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
        headless=headless,
    )
    rate_patterns = [
        re.compile(r'data-test=["\']instrument-price-last["\'][^>]*>[\s\n]*([0-9]+[.,][0-9]+)', re.IGNORECASE | re.MULTILINE),
        re.compile(r'data-value=["\']([0-9]+[.,][0-9]+)["\']', re.IGNORECASE),
    ]
    rate_value = None
    for pattern in rate_patterns:
        match = pattern.search(html)
        if not match:
            continue
        try:
            candidate = float(match.group(1).replace(",", "."))
            if 0.0 <= candidate <= 10.0:
                rate_value = candidate
                break
        except Exception:
            continue
    points = [(date.today(), rate_value)] if rate_value is not None else []
    return FetchResult(source="investing", identifier=identifier, points=points, metadata={"url": url, "source_page": url})


def fetch_ecb_irs_rate(identifier: str, start_date: date | None = None) -> FetchResult:
    series_map = {"CMS_EUR_10Y": "YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y"}
    series_key = series_map.get(identifier)
    if not series_key:
        raise ValueError(f"Série ECB non configurée pour: {identifier}")
    params = "?format=jsondata"
    if start_date:
        params += f"&startPeriod={start_date.isoformat()}"
    ecb_url = f"https://data-api.ecb.europa.eu/service/data/{series_key}{params}"
    req = urllib.request.Request(ecb_url, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0 (compatible; portfolio-tracker/1.0)"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = json.loads(resp.read().decode("utf-8"))
    root = raw.get("data", raw)
    datasets = root.get("dataSets") or []
    structure = root.get("structure") or {}
    if not datasets:
        raise ValueError(f"Aucune donnée ECB pour {identifier}")
    obs_dims = structure.get("dimensions", {}).get("observation") or []
    time_dim = next((dim for dim in obs_dims if dim.get("id") == "TIME_PERIOD"), None)
    if not time_dim:
        raise ValueError("Dimension TIME_PERIOD introuvable dans la réponse ECB")
    time_values = [value["id"] for value in time_dim.get("values") or []]
    series_data = datasets[0].get("series") or {}
    observations = next(iter(series_data.values()), {}).get("observations") or {}
    points = []
    for idx_str, obs in observations.items():
        idx = int(idx_str)
        if idx >= len(time_values) or not obs or obs[0] is None:
            continue
        try:
            points.append((date.fromisoformat(time_values[idx]), float(obs[0])))
        except Exception:
            continue
    points.sort(key=lambda item: item[0])
    return FetchResult(source="ecb", identifier=identifier, points=points, metadata={"url": ecb_url})
