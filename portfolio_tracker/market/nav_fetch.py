"""
NAV Fetch - récupération automatique des VL (UC) via sources configurables.

Le but est de rester "batteries included" (stdlib) :
- HTTP: urllib.request
- JSON: json
- CSV: csv
- HTML: regex (re) (optionnellement headless via Playwright si installé)

Configuration attendue dans `market_data/nav_sources.yaml`:

nav_sources:
  uc_eleva_absolute_return_europe:
    kind: url_json
    url: "https://example.com/api/nav"
    value_path: "nav.value"
    date_path: "nav.date"          # optionnel
    currency: "EUR"               # optionnel (défaut EUR)
  uc_helium_selection_b_eur:
    kind: url_csv
    url: "https://example.com/nav.csv"
    delimiter: ";"
    date_column: "date"
    value_column: "value"
    date_format: "%Y-%m-%d"       # optionnel
    decimal: "."                  # "." ou ","
  uc_bdl_rempart_c:
    kind: html_regex
    url: "https://example.com/page.html"
    value_regex: "VL\\s*:\\s*([0-9]+[\\.,][0-9]+)"
    date_regex: "Date\\s*:\\s*([0-9]{4}-[0-9]{2}-[0-9]{2})"  # optionnel
    headless: false               # optionnel
"""

from __future__ import annotations

import csv
import json
import logging
import re
import ssl
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, List
from urllib.request import Request, urlopen

import yaml

logger = logging.getLogger(__name__)

from .headless import headless_get_text, headless_get_response_text


@dataclass(frozen=True)
class FetchedNav:
    value: float
    nav_date: date
    currency: str = "EUR"
    source: str = "auto"
    quantalys_rating: Optional[int] = None  # Note Quantalys (1-5) si disponible
    quantalys_category: Optional[str] = None  # Catégorie Quantalys si disponible


def _parse_date_any(raw: Any, *, fmt: Optional[str] = None) -> date:
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, str):
        s = raw.strip()
        if fmt:
            return datetime.strptime(s, fmt).date()
        return datetime.fromisoformat(s).date()
    raise ValueError(f"Date invalide: {raw!r}")


def _get_path(obj: Any, path: str) -> Any:
    """
    Extraction simple via "a.b.c" (dict) / indices numériques (list) ex: "items.0.value".
    """
    cur = obj
    for part in str(path).split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list):
            try:
                idx = int(part)
            except Exception as e:
                raise KeyError(f"Index list invalide: {part}") from e
            cur = cur[idx] if 0 <= idx < len(cur) else None
        else:
            return None
    return cur


def _http_get_text(url: str, *, user_agent: str = "portfolio-tracker/1.0", timeout_s: int = 30) -> str:
    req = Request(url, headers={"User-Agent": user_agent})
    # Use certifi bundle when available (more reliable across environments).
    try:
        import certifi  # type: ignore

        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = ssl.create_default_context()
    with urlopen(req, timeout=timeout_s, context=ctx) as resp:  # nosec - personal project
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def _http_get_bytes(url: str, *, user_agent: str = "portfolio-tracker/1.0", timeout_s: int = 30) -> bytes:
    req = Request(url, headers={"User-Agent": user_agent})
    try:
        import certifi  # type: ignore

        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = ssl.create_default_context()
    with urlopen(req, timeout=timeout_s, context=ctx) as resp:  # nosec - personal project
        return resp.read()


def load_nav_sources_cfg(market_data_dir: Path) -> Dict[str, Dict[str, Any]]:
    cfg_file = Path(market_data_dir) / "nav_sources.yaml"
    if not cfg_file.exists():
        return {}
    cfg = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) or {}
    sources = cfg.get("nav_sources") or {}
    if not isinstance(sources, dict):
        return {}
    # normalize
    out: Dict[str, Dict[str, Any]] = {}
    for k, v in sources.items():
        if isinstance(k, str) and isinstance(v, dict):
            out[k] = v
    return out


def fetch_nav_for_asset_id(
    *,
    market_data_dir: Path,
    asset_id: str,
    target_date: date,
    force_headless: bool = False,
) -> Optional[FetchedNav]:
    """
    Récupère une VL pour un asset_id, via `market_data/nav_sources.yaml`.

    Retourne la VL la plus récente <= target_date quand c'est possible.
    """
    cfg = load_nav_sources_cfg(market_data_dir).get(asset_id)
    if not cfg:
        return None

    kind = str(cfg.get("kind") or "").strip()
    url = str(cfg.get("url") or "").strip()
    if not kind or (not url and kind != "morningstar"):
        return None

    currency = str(cfg.get("currency") or "EUR")
    source_label = str(cfg.get("source") or kind)

    headless = bool(cfg.get("headless", False)) or bool(force_headless)
    timeout_s = int(cfg.get("timeout_s", 30))
    user_agent = str(cfg.get("user_agent") or "portfolio-tracker/1.0")

    # ---------------------------
    # url_json
    # ---------------------------
    if kind == "url_json":
        if headless:
            raw = headless_get_response_text(url)
        else:
            raw = _http_get_text(url, user_agent=user_agent, timeout_s=timeout_s)
        obj = json.loads(raw)

        value_path = cfg.get("value_path")
        if not value_path:
            raise ValueError(f"{asset_id}: value_path manquant pour url_json")
        raw_val = _get_path(obj, str(value_path))
        if raw_val is None:
            raise ValueError(f"{asset_id}: value_path introuvable: {value_path}")

        val = float(str(raw_val).replace(",", "."))

        date_path = cfg.get("date_path")
        if date_path:
            raw_date = _get_path(obj, str(date_path))
            nav_date = _parse_date_any(raw_date, fmt=cfg.get("date_format"))
        else:
            nav_date = target_date

        # si la date est future, on la borne
        if nav_date > target_date:
            nav_date = target_date

        return FetchedNav(value=val, nav_date=nav_date, currency=currency, source=source_label)

    # ---------------------------
    # morningstar
    # ---------------------------
    if kind == "morningstar":
        sec_id = str(cfg.get("morningstar_secid") or "")
        if not sec_id:
            raise ValueError(f"{asset_id}: morningstar_secid manquant pour kind=morningstar")
        results = _fetch_morningstar_history(
            sec_id=sec_id,
            start_date=target_date - timedelta(days=4),
            end_date=target_date,
            currency=currency,
        )
        if not results:
            raise ValueError(f"{asset_id}: aucune NAV Morningstar trouvée pour {sec_id}")
        latest = max(results, key=lambda r: r.nav_date)
        return FetchedNav(value=latest.value, nav_date=latest.nav_date, currency=currency, source=source_label)

    # ---------------------------
    # url_csv
    # ---------------------------
    if kind == "url_csv":
        if headless:
            raw = headless_get_response_text(url)
            content = raw.encode("utf-8", errors="replace")
        else:
            content = _http_get_bytes(url, user_agent=user_agent, timeout_s=timeout_s)

        text = content.decode("utf-8", errors="replace")
        delimiter = str(cfg.get("delimiter") or ",")
        date_col = str(cfg.get("date_column") or "date")
        value_col = str(cfg.get("value_column") or "value")
        date_fmt = cfg.get("date_format")
        decimal = str(cfg.get("decimal") or ".")

        reader = csv.DictReader(text.splitlines(), delimiter=delimiter)
        best: Optional[Tuple[date, float]] = None
        for row in reader:
            if not isinstance(row, dict):
                continue
            if date_col not in row or value_col not in row:
                continue
            try:
                d = _parse_date_any(row[date_col], fmt=date_fmt)
                if d > target_date:
                    continue
                raw_v = str(row[value_col]).strip()
                if decimal == ",":
                    raw_v = raw_v.replace(",", ".")
                v = float(raw_v)
            except (ValueError, TypeError, KeyError, IndexError):
                continue
            if best is None or d > best[0]:
                best = (d, v)

        if best is None:
            return None
        return FetchedNav(value=best[1], nav_date=best[0], currency=currency, source=source_label)

    # ---------------------------
    # html_regex
    # ---------------------------
    if kind == "html_regex":
        if headless:
            html = headless_get_text(url)
        else:
            html = _http_get_text(url, user_agent=user_agent, timeout_s=timeout_s)

        # Normaliser les espaces insécables HTML (&nbsp; et \u00A0) en espaces normaux
        # pour que le regex puisse les capturer correctement
        html = html.replace("&nbsp;", " ").replace("\u00A0", " ")

        value_re = cfg.get("value_regex")
        if not value_re:
            raise ValueError(f"{asset_id}: value_regex manquant pour html_regex")
        
        # Extraire la note Quantalys si on est sur une page Quantalys
        quantalys_rating = None
        quantalys_category = None
        if "quantalys.com" in url.lower():
            # Extraire la note (1-5 étoiles)
            # Patterns améliorés pour Quantalys (testé sur vraies pages)
            rating_patterns = [
                # Note affichée dans un attribut data-rating ou similaire
                r'data-rating["\s]*=["\s]*(\d)',
                r'note["\s]*:["\s]*(\d)',
                # Note dans le texte "Note Quantalys" suivi d'étoiles ou chiffre
                r'Note\s+Quantalys[^0-9]{0,50}?(\d)\s*(?:/\s*5|étoiles?|stars?)',
                # Étoiles sous forme de classes CSS (ex: "star-4" ou "rating-4")
                r'(?:star|rating|note)[-_](\d)',
                # Pattern générique : chiffre entre 1 et 5 proche de "Quantalys"
                r'Quantalys[^0-9]{1,100}?([1-5])(?:\s|<|/)',
            ]
            for pattern in rating_patterns:
                matches = re.finditer(pattern, html, flags=re.IGNORECASE)
                for rating_match in matches:
                    try:
                        rating = int(rating_match.group(1))
                        if 1 <= rating <= 5:
                            quantalys_rating = rating
                            break
                    except (ValueError, IndexError):
                        continue
                if quantalys_rating:
                    break
            
            # Extraire la catégorie Quantalys
            # Patterns améliorés pour éviter de capturer du HTML
            category_patterns = [
                # Catégorie dans un élément de texte propre (entre > et <)
                r'>Catégorie[^<>]*?:\s*([^<>{}"]+?)\s*<',
                r'>Classification[^<>]*?:\s*([^<>{}"]+?)\s*<',
                # Catégorie dans un attribut title ou alt
                r'(?:title|alt)="Catégorie[^"]*?:\s*([^"]{5,80})"',
                # Catégorie avec des mots-clés connus
                r'Catégorie[^:]{0,20}:\s*([A-ZÀ-Ÿa-zà-ÿ][A-ZÀ-Ÿa-zà-ÿ\s/\-]{5,60})(?:\s*(?:<|$|\||–))',
                # Dans les métadonnées JSON
                r'"category"\s*:\s*"([^"]{5,80})"',
                r'"categorie"\s*:\s*"([^"]{5,80})"',
            ]
            for pattern in category_patterns:
                cat_match = re.search(pattern, html, flags=re.IGNORECASE)
                if cat_match:
                    category = cat_match.group(1).strip()
                    # Nettoyer la catégorie
                    category = re.sub(r'\s+', ' ', category)  # Normaliser les espaces
                    category = category.strip(' \t\n\r<>"\'')
                    # Vérifier que c'est une vraie catégorie (pas de code HTML)
                    if (5 <= len(category) <= 80 and 
                        'data-' not in category.lower() and
                        '<' not in category and 
                        '>' not in category and
                        'http' not in category.lower()):
                        quantalys_category = category
                        break
        
        # Chercher toutes les occurrences pour trouver la bonne valeur
        matches = list(re.finditer(str(value_re), html, flags=re.IGNORECASE | re.MULTILINE))
        if not matches:
            raise ValueError(f"{asset_id}: valeur introuvable via regex")
        
        # Trier les matches par longueur décroissante pour privilégier les valeurs complètes
        # (ex: "1 777.24" au lieu de "777.24")
        matches = sorted(matches, key=lambda m: len(m.group(1)), reverse=True)
        
        # Essayer chaque match jusqu'à trouver une valeur valide
        val = None
        for m in matches:
            raw_val = m.group(1)
            # Nettoyer la valeur : gérer les formats français et américains
            # Format français: "1 777,236" ou "1777,236" (virgule = décimal, espaces = milliers)
            # Format américain: "1,777.236" (virgule = milliers, point = décimal)
            cleaned_val = str(raw_val).strip()
            
            # Si on a à la fois virgule ET point, c'est probablement un format mixte invalide
            # On détecte le format en regardant la position relative
            has_comma = "," in cleaned_val
            has_dot = "." in cleaned_val
            has_space = " " in cleaned_val
            
            if has_comma and has_dot:
                # Format mixte suspect: on ignore généralement ces valeurs (ex: "34,072.31")
                # Sauf si c'est clairement un format français avec point comme séparateur de milliers
                comma_pos = cleaned_val.rfind(",")
                dot_pos = cleaned_val.rfind(".")
                # Si le point est avant la virgule ET qu'il y a des espaces, c'est probablement français
                if dot_pos < comma_pos and has_space:
                    # Format français avec point comme milliers: "1.777,236"
                    cleaned_val = cleaned_val.replace(".", "").replace(",", ".").replace(" ", "")
                else:
                    # Format mixte suspect, on ignore cette occurrence
                    continue
            elif has_comma:
                # Format français: virgule = décimal, espaces = milliers
                cleaned_val = cleaned_val.replace(" ", "").replace(",", ".")
            elif has_dot:
                # Format avec point: point = décimal, virgules/espaces = milliers
                cleaned_val = cleaned_val.replace(",", "").replace(" ", "")
            else:
                # Pas de séparateur décimal visible, on garde tel quel
                cleaned_val = cleaned_val.replace(" ", "")
            
            try:
                val = float(cleaned_val)
                # Vérifier que la valeur est dans une plage raisonnable pour une VL (10 à 100000)
                if 10 <= val <= 100000:
                    break
            except (ValueError, OverflowError):
                continue
        
        if val is None:
            raise ValueError(f"{asset_id}: aucune valeur valide trouvée parmi {len(matches)} occurrence(s)")

        nav_date = target_date
        date_re = cfg.get("date_regex")
        if date_re:
            md = re.search(str(date_re), html, flags=re.IGNORECASE | re.MULTILINE)
            if md:
                nav_date = _parse_date_any(md.group(1), fmt=cfg.get("date_format"))
                if nav_date > target_date:
                    nav_date = target_date

        return FetchedNav(
            value=val, 
            nav_date=nav_date, 
            currency=currency, 
            source=source_label,
            quantalys_rating=quantalys_rating,
            quantalys_category=quantalys_category
        )

    raise ValueError(f"{asset_id}: kind non supporté: {kind}")


_MORNINGSTAR_TOKEN = "klr5zyak8x"
_MORNINGSTAR_TIMESERIES_URL = (
    "https://tools.morningstar.fr/api/rest.svc/timeseries_price/{token}"
    "?id={sec_id}&currencyId=EUR&idtype=Morningstar"
    "&frequency=daily&startDate={start}&endDate={end}&outputType=COMPACTJSON"
)


def _fetch_morningstar_history(
    sec_id: str,
    start_date: date,
    end_date: date,
    currency: str = "EUR",
) -> List[FetchedNav]:
    """
    Récupère l'historique NAV depuis Morningstar pour un SecId donné.

    Format de réponse: [[timestamp_ms, nav_float], ...] (COMPACTJSON)
    """
    url = _MORNINGSTAR_TIMESERIES_URL.format(
        token=_MORNINGSTAR_TOKEN,
        sec_id=sec_id,
        start=start_date.isoformat(),
        end=end_date.isoformat(),
    )
    raw = _http_get_text(url, user_agent="Mozilla/5.0", timeout_s=30)
    if not raw.strip().startswith("["):
        raise ValueError(f"Morningstar: réponse inattendue pour {sec_id}: {raw[:120]}")

    data = json.loads(raw)
    out: List[FetchedNav] = []
    for item in data:
        if not isinstance(item, list) or len(item) != 2:
            continue
        ts_ms, nav_val = item
        try:
            nav_date = datetime.utcfromtimestamp(float(ts_ms) / 1000).date()
            nav = round(float(nav_val), 4)
        except (ValueError, TypeError, OSError):
            continue
        if start_date <= nav_date <= end_date:
            out.append(FetchedNav(value=nav, nav_date=nav_date, currency=currency, source="morningstar"))
    return out


def fetch_nav_history_for_asset_id(
    *,
    market_data_dir: Path,
    asset_id: str,
    start_date: date,
    end_date: Optional[date] = None,
    force_headless: bool = False,
) -> List[FetchedNav]:
    """
    Récupère un historique de VL pour un asset_id (si la source le permet).

    - `url_csv`: lit toutes les lignes exploitables de la source CSV.
    - `url_json`: supporte une liste JSON (optionnellement via history_path).
    - `html_regex` + `morningstar_secid`: utilise Morningstar pour l'historique complet.
    - `html_regex` seul: retourne au mieux 1 point ponctuel.
    """
    cfg = load_nav_sources_cfg(market_data_dir).get(asset_id)
    if not cfg:
        return []

    kind = str(cfg.get("kind") or "").strip()
    url = str(cfg.get("url") or "").strip()
    if not kind or not url:
        return []

    end_d = end_date or date.today()
    if start_date > end_d:
        return []

    currency = str(cfg.get("currency") or "EUR")
    source_label = str(cfg.get("source") or kind)
    headless = bool(cfg.get("headless", False)) or bool(force_headless)
    timeout_s = int(cfg.get("timeout_s", 30))
    user_agent = str(cfg.get("user_agent") or "portfolio-tracker/1.0")

    out: List[FetchedNav] = []

    # ---------------------------
    # url_csv (historique natif)
    # ---------------------------
    if kind == "url_csv":
        if headless:
            raw = headless_get_response_text(url)
            content = raw.encode("utf-8", errors="replace")
        else:
            content = _http_get_bytes(url, user_agent=user_agent, timeout_s=timeout_s)

        text = content.decode("utf-8", errors="replace")
        delimiter = str(cfg.get("delimiter") or ",")
        date_col = str(cfg.get("date_column") or "date")
        value_col = str(cfg.get("value_column") or "value")
        date_fmt = cfg.get("date_format")
        decimal = str(cfg.get("decimal") or ".")

        by_date: Dict[date, float] = {}
        reader = csv.DictReader(text.splitlines(), delimiter=delimiter)
        for row in reader:
            if not isinstance(row, dict):
                continue
            if date_col not in row or value_col not in row:
                continue
            try:
                d = _parse_date_any(row[date_col], fmt=date_fmt)
                if d < start_date or d > end_d:
                    continue
                raw_v = str(row[value_col]).strip()
                if decimal == ",":
                    raw_v = raw_v.replace(",", ".")
                v = float(raw_v)
            except Exception:
                continue
            by_date[d] = v

        for d in sorted(by_date.keys()):
            out.append(FetchedNav(value=by_date[d], nav_date=d, currency=currency, source=source_label))
        return out

    # ---------------------------
    # url_json (historique si list)
    # ---------------------------
    if kind == "url_json":
        if headless:
            raw = headless_get_response_text(url)
        else:
            raw = _http_get_text(url, user_agent=user_agent, timeout_s=timeout_s)
        obj = json.loads(raw)

        history_path = cfg.get("history_path")
        rows_obj = _get_path(obj, str(history_path)) if history_path else obj
        if isinstance(rows_obj, list):
            date_path = str(cfg.get("history_date_path") or cfg.get("date_path") or "date")
            value_path = str(cfg.get("history_value_path") or cfg.get("value_path") or "value")
            date_fmt = cfg.get("date_format")
            by_date: Dict[date, float] = {}
            for row in rows_obj:
                if not isinstance(row, (dict, list)):
                    continue
                try:
                    raw_date = _get_path(row, date_path)
                    raw_val = _get_path(row, value_path)
                    if raw_date is None or raw_val is None:
                        continue
                    d = _parse_date_any(raw_date, fmt=date_fmt)
                    if d < start_date or d > end_d:
                        continue
                    v = float(str(raw_val).replace(",", "."))
                except Exception:
                    continue
                by_date[d] = v

            for d in sorted(by_date.keys()):
                out.append(FetchedNav(value=by_date[d], nav_date=d, currency=currency, source=source_label))
            if out:
                return out

        # Fallback : source JSON ponctuelle
        single = fetch_nav_for_asset_id(
            market_data_dir=market_data_dir,
            asset_id=asset_id,
            target_date=end_d,
            force_headless=force_headless,
        )
        if single and start_date <= single.nav_date <= end_d:
            return [single]
        return []

    # ---------------------------
    # html_regex / autres
    # Si morningstar_secid est présent, on l'utilise pour l'historique complet.
    # Sinon, fallback sur un seul point ponctuel.
    # ---------------------------
    morningstar_secid = cfg.get("morningstar_secid")
    if morningstar_secid:
        try:
            return _fetch_morningstar_history(
                sec_id=str(morningstar_secid),
                start_date=start_date,
                end_date=end_d,
                currency=currency,
            )
        except Exception as e:
            raise ValueError(f"{asset_id}: erreur Morningstar ({morningstar_secid}): {e}") from e

    try:
        single = fetch_nav_for_asset_id(
            market_data_dir=market_data_dir,
            asset_id=asset_id,
            target_date=end_d,
            force_headless=force_headless,
        )
    except Exception:
        single = None
    if single and start_date <= single.nav_date <= end_d:
        return [single]
    return []

