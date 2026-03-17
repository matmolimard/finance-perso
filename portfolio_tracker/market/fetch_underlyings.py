"""
Fetch underlyings - Récupération (web) des séries de sous-jacents.

Sources visées (public):
- Solactive: endpoint indexhistory (renvoie une liste JSON timestamp/value)
- Euronext: endpoint getHistoricalPriceBlock (HTML avec tableau d'historiques récents)
- Natixis: page HTML avec niveau actuel et date de valorisation

Note: ces scrapers sont best-effort et peuvent casser si les sites changent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import List, Tuple, Dict, Any, Optional
import json
import re
import ssl
import urllib.request
import subprocess
from urllib.parse import urlencode

from .headless import headless_get_text, headless_get_response_text

@dataclass(frozen=True)
class FetchResult:
    source: str
    identifier: str
    points: List[Tuple[date, float]]
    metadata: Dict[str, Any]


def _http_get_text(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout_s: int = 30,
    *,
    headless: bool = False
) -> str:
    req = urllib.request.Request(url, headers=headers or {})
    cafile = None
    try:
        # Use certifi CA bundle if available (helps in some sandboxed environments)
        ctx = ssl.create_default_context()
        try:
            import certifi  # type: ignore

            cafile = certifi.where()
            ctx = ssl.create_default_context(cafile=cafile)
        except Exception:
            pass

        with urllib.request.urlopen(req, timeout=timeout_s, context=ctx) as resp:
            raw = resp.read()
            # try utf-8 then fallback latin-1
            try:
                return raw.decode("utf-8")
            except Exception:
                return raw.decode("latin-1", errors="ignore")
    except Exception:
        # Fallback: use curl with an explicit CA bundle when available.
        # This is robust on macOS/sandbox setups where Python/OpenSSL CA paths are misconfigured.
        try:
            cmd = ["curl", "-fsSL", "--max-time", str(timeout_s)]
            if cafile:
                cmd += ["--cacert", cafile]
            for k, v in (headers or {}).items():
                cmd += ["-H", f"{k}: {v}"]
            cmd.append(url)
            raw = subprocess.check_output(cmd)
            try:
                return raw.decode("utf-8")
            except Exception:
                return raw.decode("latin-1", errors="ignore")
        except Exception:
            pass
        if not headless:
            raise
        # fallback to rendered DOM (JS)
        return headless_get_text(url)


def fetch_solactive_indexhistory(url: str, identifier: str, *, headless: bool = False) -> FetchResult:
    """
    Solactive endpoint example:
      https://www.solactive.com/indices/?indexhistory=DE000SL0MLE8&indexhistorytype=max
    Renvoie un JSON array de {indexId, timestamp(ms), value}
    """
    # Solactive peut renvoyer un body vide sans Referer; on force un referer "indices" stable.
    referer = f"https://www.solactive.com/indices/?index={identifier}&lang=fr"
    text = _http_get_text(
        url,
        headers={
            "User-Agent": "portfolio-tracker/1.0 (personal project)",
            "Accept": "application/json,text/plain,*/*",
            "Referer": referer,
        },
        headless=headless,
    )

    # Le endpoint peut renvoyer:
    # - un JSON brut (historique)
    # - une page HTML (consentement / fallback)
    # On tente d'abord json.loads direct, puis extraction d'un JSON array embarqué.
    try:
        data = json.loads(text)
    except Exception:
        # Essai 2: extraire un JSON array directement dans le texte
        m = re.search(r"(\[\s*\{[\s\S]*?\}\s*\])", text)
        if m:
            try:
                data = json.loads(m.group(1))
            except Exception:
                data = None
        else:
            data = None

        # Essai 3: fallback headless (body de réponse)
        if data is None:
            try:
                text2 = headless_get_response_text(url)
                data = json.loads(text2)
            except Exception:
                # Essai 4: JSON array embarqué côté headless
                m2 = re.search(r"(\[\s*\{[\s\S]*?\}\s*\])", text2 if 'text2' in locals() else "")
                if m2:
                    data = json.loads(m2.group(1))
                else:
                    # Message explicite (plutôt qu'une JSONDecodeError opaque)
                    raise ValueError(
                        f"Solactive: réponse non exploitable pour {identifier} "
                        f"(JSON direct absent, fallback headless sans données parseables)"
                    )
    points: List[Tuple[date, float]] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        ts = row.get("timestamp")
        v = row.get("value")
        if ts is None or v is None:
            continue
        try:
            d = datetime.utcfromtimestamp(int(ts) / 1000.0).date()
            points.append((d, float(v)))
        except Exception:
            continue

    points.sort(key=lambda p: p[0])
    return FetchResult(
        source="solactive",
        identifier=identifier,
        points=points,
        metadata={"url": url, "points_count": len(points)},
    )


_EURO_DATE_RE = re.compile(r"\b(\d{2})/(\d{2})/(\d{4})\b")
_EURO_NUM_RE = re.compile(r"\b\d{1,3}(?:[\s\u00A0]\d{3})*(?:,\d+)?\b")


def _parse_euronext_number(s: str) -> float:
    # Euronext uses comma as decimal separator
    s = s.replace("\u00A0", " ").replace(" ", "")
    s = s.replace(",", ".")
    return float(s)


def fetch_euronext_recent_history(identifier: str, *, headless: bool = False) -> FetchResult:
    """
    Euronext HTML block with recent history table:
      https://live.euronext.com/fr/ajax/getHistoricalPriceBlock/<identifier>

    Ce bloc contient un tableau \"Cours Historiques\" avec Date / Haut / Bas / Clot.
    """
    # Prefer CSV download endpoint (no JS) which contains full history.
    url_csv = (
        f"https://live.euronext.com/fr/ajax/AwlHistoricalPrice/getFullDownloadAjax/{identifier}"
        "?format=csv&decimal_separator=.&date_form=d/m/Y"
    )
    csv_text = _http_get_text(
        url_csv,
        headers={
            "User-Agent": "portfolio-tracker/1.0 (personal project)",
            "Accept": "text/csv,*/*",
        },
        headless=headless,
    )

    points: List[Tuple[date, float]] = []
    # CSV is semicolon-delimited and often starts with a BOM + 2-3 info lines.
    # We parse data lines that begin with dd/mm/yyyy.
    for line in csv_text.splitlines():
        line = line.lstrip("\ufeff").strip()
        if not _EURO_DATE_RE.match(line):
            continue
        parts = [p.strip().strip('"') for p in line.split(";")]
        if len(parts) < 6:
            continue
        ds = parts[0]
        close_str = parts[5]  # Date;Open;High;Low;Last;Close;...
        try:
            dt = datetime.strptime(ds, "%d/%m/%Y").date()
            close_val = float(close_str) if close_str else None
        except Exception:
            continue
        if close_val is None:
            continue
        points.append((dt, close_val))

    if points:
        by_date: Dict[date, float] = {d: v for d, v in points}
        merged = sorted(by_date.items(), key=lambda p: p[0])
        return FetchResult(
            source="euronext",
            identifier=identifier,
            points=merged,
            metadata={"url": url_csv, "points_count": len(merged), "format": "csv"},
        )

    # Fallback: HTML block (may be empty / JS-driven)
    url_html = f"https://live.euronext.com/fr/ajax/getHistoricalPriceBlock/{identifier}"
    html = _http_get_text(
        url_html,
        headers={
            "User-Agent": "portfolio-tracker/1.0 (personal project)",
            "Accept": "text/html,*/*",
        },
        headless=headless,
    )

    # Robust parsing: strip tags to text, then extract sequences like:
    #   dd/mm/yyyy <high> <low> <close> <vol>
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"[\\s\\u00A0]+", " ", text).strip()

    row_re = re.compile(
        r"(\\d{2}/\\d{2}/\\d{4})\\s+"
        r"([0-9][0-9\\s\\u00A0]*,?\\d*)\\s+"
        r"([0-9][0-9\\s\\u00A0]*,?\\d*)\\s+"
        r"([0-9][0-9\\s\\u00A0]*,?\\d*)\\s+"
        r"([0-9][0-9\\s\\u00A0]*,?\\d*)"
    )

    points = []
    for m in row_re.finditer(text):
        ds, _high, _low, close_str, _vol = m.groups()
        try:
            dt = datetime.strptime(ds, "%d/%m/%Y").date()
            close_val = _parse_euronext_number(close_str)
        except Exception:
            continue
        points.append((dt, close_val))

    # Dédoublonner par date
    by_date: Dict[date, float] = {}
    for d, v in points:
        by_date[d] = v
    merged = sorted(by_date.items(), key=lambda p: p[0])

    return FetchResult(
        source="euronext",
        identifier=identifier,
        points=merged,
        metadata={"url": url_html, "points_count": len(merged), "format": "html_block"},
    )


def fetch_merqube_indexhistory(
    name: str,
    *,
    metric: str = "total_return",
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    headless: bool = False
) -> FetchResult:
    """
    MerQube API (utilisée par https://merqube.com/indices/<NAME>).

    Exemple observé:
      https://api.merqube.com/security/index?names=MQDCA09P&metrics=total_return&format=records

    Supporte aussi (observé) des paramètres optionnels start_date/end_date.
    """
    params: Dict[str, str] = {"names": str(name), "metrics": str(metric), "format": "records"}
    if start_date is not None:
        params["start_date"] = start_date.isoformat()
    if end_date is not None:
        params["end_date"] = end_date.isoformat()
    url = "https://api.merqube.com/security/index?" + urlencode(params)

    text = _http_get_text(
        url,
        headers={
            "User-Agent": "portfolio-tracker/1.0 (personal project)",
            "Accept": "application/json,text/plain,*/*",
        },
        headless=headless,
    )
    data = json.loads(text)

    points: List[Tuple[date, float]] = []

    # Format attendu: liste de records contenant une date et une valeur.
    # On gère plusieurs conventions (robuste aux variations).
    if isinstance(data, list):
        for row in data:
            if not isinstance(row, dict):
                continue
            ds = row.get("date") or row.get("day") or row.get("as_of") or row.get("timestamp")
            v = row.get("value") or row.get(metric) or row.get("level") or row.get("index_level")
            if ds is None or v is None:
                continue
            try:
                if isinstance(ds, (int, float)) or (isinstance(ds, str) and ds.isdigit()):
                    # ms epoch or s epoch (best-effort)
                    ts = int(ds)
                    if ts > 10_000_000_000:  # ms
                        d = datetime.utcfromtimestamp(ts / 1000.0).date()
                    else:  # s
                        d = datetime.utcfromtimestamp(ts).date()
                else:
                    d = datetime.fromisoformat(str(ds).replace("Z", "+00:00")).date()
                points.append((d, float(v)))
            except Exception:
                continue

    points.sort(key=lambda p: p[0])
    # Dédoublonnage
    by_date: Dict[date, float] = {}
    for d, v in points:
        by_date[d] = v
    merged = sorted(by_date.items(), key=lambda p: p[0])

    return FetchResult(
        source="merqube",
        identifier=str(name),
        points=merged,
        metadata={
            "url": url,
            "metric": metric,
            "points_count": len(merged),
            "source_page": f"https://merqube.com/indices/{name}",
        },
    )


def fetch_natixis_index(url: str, identifier: str, *, headless: bool = False) -> FetchResult:
    """
    Natixis Equity Derivatives page (ex: https://equityderivatives.natixis.com/fr/indice/luxgstdg/)
    
    La page affiche:
    - Niveau de l'indice: valeur numérique
    - Date de dernière valorisation: dd/mm/yyyy
    
    Pour l'instant, on récupère uniquement la valeur actuelle et sa date.
    L'historique complet nécessiterait d'accéder à un endpoint API ou un graphique.
    """
    html = _http_get_text(
        url,
        headers={
            "User-Agent": "portfolio-tracker/1.0 (personal project)",
            "Accept": "text/html,*/*",
        },
        headless=headless,
    )
    
    points: List[Tuple[date, float]] = []
    
    # Chercher le niveau de l'indice (pattern: "Niveau de l'indice" suivi d'un nombre)
    # Format observé: "Niveau de l'indice\n925.03" ou similaire
    level_pattern = re.compile(
        r"(?:Niveau\s+de\s+l['']indice|Niveau\s+d['']indice)[\s:]*\n?\s*([0-9]+[.,][0-9]+)",
        re.IGNORECASE | re.MULTILINE
    )
    
    # Chercher la date de valorisation (format: "dd/mm/yyyy")
    date_pattern = re.compile(
        r"(?:Date\s+de\s+dernière\s+valorisation|Dernière\s+valorisation)[\s:]*\n?\s*(\d{2})/(\d{2})/(\d{4})",
        re.IGNORECASE | re.MULTILINE
    )
    
    # Extraire le niveau
    level_match = level_pattern.search(html)
    if level_match:
        level_str = level_match.group(1).replace(",", ".")
        try:
            level_val = float(level_str)
        except Exception:
            level_val = None
    else:
        # Chercher autour de "Niveau" dans le HTML (plus flexible)
        level_section = re.search(
            r"(?:Niveau\s+(?:de\s+)?l['']indice|Niveau\s+d['']indice)[^<]*>.*?([0-9]+[.,][0-9]+)",
            html,
            re.IGNORECASE | re.DOTALL
        )
        if level_section:
            level_str = level_section.group(1).replace(",", ".")
            try:
                level_val = float(level_str)
            except Exception:
                level_val = None
        else:
            # Dernier recours: chercher un nombre décimal dans une section "Caractéristiques" ou similaire
            # Le site affiche souvent le niveau dans une section dédiée
            characteristics_section = re.search(
                r'(?:Caractéristiques|Characteristics)[^<]*Niveau[^<]*>.*?([0-9]+[.,][0-9]+)',
                html,
                re.IGNORECASE | re.DOTALL
            )
            if characteristics_section:
                level_str = characteristics_section.group(1).replace(",", ".")
                try:
                    level_val = float(level_str)
                except Exception:
                    level_val = None
            else:
                level_val = None
    
    # Extraire la date
    date_match = date_pattern.search(html)
    if date_match:
        day, month, year = date_match.groups()
        try:
            val_date = datetime.strptime(f"{day}/{month}/{year}", "%d/%m/%Y").date()
        except Exception:
            val_date = None
    else:
        # Chercher un pattern de date plus simple dans le HTML
        date_simple_pattern = re.compile(r'(\d{2})/(\d{2})/(\d{4})')
        date_matches = date_simple_pattern.findall(html)
        val_date = None
        for day, month, year in date_matches:
            try:
                candidate = datetime.strptime(f"{day}/{month}/{year}", "%d/%m/%Y").date()
                # Prendre la date la plus récente qui est dans le passé ou aujourd'hui
                if val_date is None or (candidate <= datetime.now().date() and candidate > val_date):
                    val_date = candidate
            except Exception:
                continue
    
    # Si on a trouvé niveau et date, ajouter le point
    if level_val is not None and val_date is not None:
        points.append((val_date, level_val))
    
    # Si on n'a pas trouvé, essayer avec headless pour le rendu JS
    if not points and not headless:
        try:
            html_headless = headless_get_text(url)
            # Réessayer avec le HTML rendu
            level_match = level_pattern.search(html_headless)
            date_match = date_pattern.search(html_headless)
            
            if level_match:
                level_str = level_match.group(1).replace(",", ".")
                try:
                    level_val = float(level_str)
                except Exception:
                    level_val = None
            else:
                level_val = None
            
            if date_match:
                day, month, year = date_match.groups()
                try:
                    val_date = datetime.strptime(f"{day}/{month}/{year}", "%d/%m/%Y").date()
                except Exception:
                    val_date = None
            else:
                val_date = None
            
            if level_val is not None and val_date is not None:
                points = [(val_date, level_val)]
        except Exception:
            pass
    
    return FetchResult(
        source="natixis",
        identifier=identifier,
        points=points,
        metadata={
            "url": url,
            "points_count": len(points),
            "note": "Récupération de la valeur actuelle uniquement (historique complet non disponible via cette méthode)",
        },
    )


def fetch_investing_rate(url: str, identifier: str, *, headless: bool = False) -> FetchResult:
    """
    Investing.com rate page (ex: https://fr.investing.com/rates-bonds/eur-10-years-irs-interest-rate-swap)
    
    La page affiche le taux actuel. On récupère le taux depuis la page HTML.
    """
    html = _http_get_text(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
        },
        headless=headless,
    )
    
    points: List[Tuple[date, float]] = []
    today = date.today()
    
    # Chercher le taux actuel dans la page
    # Pattern 1: data-test="instrument-price-last" (investing.com utilise souvent ce pattern)
    rate_patterns = [
        # Pattern 1: data-test="instrument-price-last"
        re.compile(
            r'data-test=["\']instrument-price-last["\'][^>]*>[\s\n]*([0-9]+[.,][0-9]+)',
            re.IGNORECASE | re.MULTILINE
        ),
        # Pattern 2: Classe avec "instrument-price"
        re.compile(
            r'<[^>]+class=["\'][^"\']*instrument-price[^"\']*["\'][^>]*>[\s\n]*([0-9]+[.,][0-9]+)',
            re.IGNORECASE | re.MULTILINE
        ),
        # Pattern 3: Chercher dans un span avec data-value
        re.compile(
            r'data-value=["\']([0-9]+[.,][0-9]+)["\']',
            re.IGNORECASE
        ),
        # Pattern 4: Chercher "2.15%" dans le contexte du taux
        re.compile(
            r'(?:EUR\s+10\s+Years\s+IRS|Taux\s+actuel|Current\s+Rate|Last)[\s:]*([0-9]+[.,][0-9]+)',
            re.IGNORECASE | re.MULTILINE
        ),
    ]
    
    rate_value = None
    for pattern in rate_patterns:
        match = pattern.search(html)
        if match:
            rate_str = match.group(1).replace(",", ".")
            try:
                rate_value = float(rate_str)
                # Vérifier que c'est un taux raisonnable (entre 0 et 10%)
                if 0.0 <= rate_value <= 10.0:
                    break
            except (ValueError, TypeError):
                continue
    
    # Si pas trouvé, essayer de parser le JSON embarqué
    if rate_value is None:
        json_patterns = [
            re.compile(r'["\']last["\']\s*:\s*([0-9]+[.,][0-9]+)', re.IGNORECASE),
            re.compile(r'["\']value["\']\s*:\s*([0-9]+[.,][0-9]+)', re.IGNORECASE),
        ]
        for pattern in json_patterns:
            match = pattern.search(html)
            if match:
                rate_str = match.group(1).replace(",", ".")
                try:
                    rate_value = float(rate_str)
                    if 0.0 <= rate_value <= 10.0:
                        break
                except (ValueError, TypeError):
                    pass
    
    # Si toujours pas trouvé, essayer avec headless pour le rendu JS
    if rate_value is None and not headless:
        try:
            html_headless = headless_get_text(url)
            for pattern in rate_patterns:
                match = pattern.search(html_headless)
                if match:
                    rate_str = match.group(1).replace(",", ".")
                    try:
                        rate_value = float(rate_str)
                        if 0.0 <= rate_value <= 10.0:
                            break
                    except (ValueError, TypeError):
                        continue
        except Exception:
            pass
    
    if rate_value is not None:
        points.append((today, rate_value))
    
    return FetchResult(
        source="investing",
        identifier=identifier,
        points=points,
        metadata={
            "url": url,
            "source_page": url,
        },
    )