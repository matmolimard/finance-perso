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
import re
import ssl
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.request import Request, urlopen

import yaml

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
    if not kind or not url:
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
            except Exception:
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


