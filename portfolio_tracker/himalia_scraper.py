"""Scraper Himalia — collecteur autonome via session persistée (storage_state ou profil navigateur).

Objectif: exploration/collecte (contrat, documents, mouvements) avec artefacts (HTML/PNG/log JSON),
sans branchement applicatif.

Workflow (100% headless) :
  1. `ANTICAPTCHA_KEY=... make himalia-scrape`
       → si aucune session n'existe, crée une session headless (login + captcha via anticaptchaofficial),
         sauvegarde le storage_state dans `$(DATA_DIR)/logs/himalia/session.json`, puis collecte.

Variables d'environnement :
  - `.env`:
      HIMALIA_LOGIN        — identifiant du compte
      HIMALIA_PASSWORD     — mot de passe
  - environnement du process (pas dans `.env`):
      ANTICAPTCHA_KEY      — clé API anti-captcha.com (pour anticaptchaofficial)
"""

from __future__ import annotations

import json
import os
import re
from html import unescape
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


ENTRY_URL = "https://www.acces-clients.com/b2b2c/entreesite/EntAccBou"
LOGIN_URL = "https://www.acces-clients.com/b2b2c/entreesite/EntAccLog"
OTP_URL = "https://www.acces-clients.com/b2b2c/entreesite/EntAccLog?task=SaisirCodeOTP"
CONTRACT_URL = "https://www.acces-clients.com/b2b2c/entreesite/EntAccCli?task=DetailContrat&numContrat={contract_id}"
DOCUMENTS_URL = "https://www.acces-clients.com/b2b2c/epargne/CoeConAve"
MOVEMENTS_URL = "https://www.acces-clients.com/b2b2c/epargne/CoeLisMvt"

TARGET_DOMAIN = "acces-clients.com"

# User-agent réaliste pour réduire la détection bot
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_BROWSER_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-dev-shm-usage",
    "--lang=fr-FR",
    "--window-size=1440,2200",
]

_CONTEXT_KWARGS = {
    "user_agent": _USER_AGENT,
    "locale": "fr-FR",
    "timezone_id": "Europe/Paris",
    "viewport": {"width": 1440, "height": 2200},
    "color_scheme": "light",
    "reduced_motion": "no-preference",
    "extra_http_headers": {
        "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
    },
}

# Script injecté avant chaque page pour masquer les signaux d'automatisation
_STEALTH_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
Object.defineProperty(navigator, 'languages', { get: () => ['fr-FR', 'fr'] });
Object.defineProperty(navigator, 'platform', { get: () => 'MacIntel' });
Object.defineProperty(navigator, 'vendor', { get: () => 'Google Inc.' });
Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
Object.defineProperty(navigator, 'maxTouchPoints', { get: () => 0 });
window.chrome = { runtime: {} };

const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
if (originalQuery) {
  window.navigator.permissions.query = (parameters) => (
    parameters && parameters.name === 'notifications'
      ? Promise.resolve({ state: Notification.permission })
      : originalQuery(parameters)
  );
}

const getParameter = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(parameter) {
  if (parameter === 37445) return 'Intel Inc.';
  if (parameter === 37446) return 'Intel Iris OpenGL Engine';
  return getParameter.call(this, parameter);
};
"""

# Tokens indiquant que la session est expirée ou invalide
SESSION_EXPIRED_TOKENS = [
    "erreur de sécurité",
    "erreur de securite",
    "veuillez-vous connecter",
    "veuillez vous connecter",
    "déconnecté du site",
    "deconnecte du site",
    "session expirée",
    "session expiree",
    "vous avez été déconnecté",
    "vous avez ete deconnecte",
]

OTP_TOKENS = [
    "code de sécurité",
    "code de securite",
    "code sécurité",
    "code securite",
    "code de vérification",
    "code de verification",
    "mot de passe à usage unique",
    "mot de passe a usage unique",
    "usage unique",
    "code reçu",
    "code recu",
    "sms",
]


class HimaliaScraperError(RuntimeError):
    """Erreur fonctionnelle du scraper Himalia."""


class HimaliaSessionExpiredError(HimaliaScraperError):
    """La session Himalia est expirée ou invalide — relancer himalia-setup-session."""


class HimaliaSecurityBlockError(HimaliaScraperError):
    """Le site a bloqué l'accès (page erreur de sécurité) avant le formulaire."""


class HimaliaOtpRequiredError(HimaliaScraperError):
    """Une vérification OTP est requise après soumission du login."""


# ---------------------------------------------------------------------------
# Chemins
# ---------------------------------------------------------------------------


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _default_output_dir(data_dir: Path | None) -> Path:
    if data_dir is not None:
        return data_dir / "logs" / "himalia"
    return _project_root() / "portfolio_tracker" / "data" / "logs" / "himalia"


def _default_session_path(data_dir: Path | None = None) -> Path:
    return _default_output_dir(data_dir) / "session.json"


def _default_pending_otp_session_path(data_dir: Path | None = None) -> Path:
    return _default_output_dir(data_dir) / "session_otp_pending.json"


def _launch_browser(playwright: Any, *, headless: bool) -> Any:
    """Lance Chromium avec un fingerprint plus proche d'un Chrome standard."""
    kwargs = {
        "headless": headless,
        "args": list(_BROWSER_ARGS),
        "ignore_default_args": ["--enable-automation"],
    }
    try:
        return playwright.chromium.launch(channel="chrome", **kwargs)
    except TypeError:
        return playwright.chromium.launch(**kwargs)


def _launch_persistent_context(playwright: Any, profile_dir: Path, *, headless: bool) -> Any:
    kwargs = {
        "headless": headless,
        "args": list(_BROWSER_ARGS),
        "ignore_default_args": ["--enable-automation"],
        **_CONTEXT_KWARGS,
    }
    try:
        return playwright.chromium.launch_persistent_context(str(profile_dir), channel="chrome", **kwargs)
    except TypeError:
        return playwright.chromium.launch_persistent_context(str(profile_dir), **kwargs)


# ---------------------------------------------------------------------------
# Secrets (.env)
# ---------------------------------------------------------------------------


def _load_env() -> None:
    load_dotenv(_project_root() / ".env", override=False)


def _load_credentials() -> tuple[str, str]:
    """Retourne (username, password) depuis .env. Lève si absent."""
    _load_env()
    username = os.getenv("HIMALIA_LOGIN", "").strip()
    password = os.getenv("HIMALIA_PASSWORD", "").strip()
    if not username or not password:
        raise HimaliaScraperError(
            "Identifiants Himalia introuvables dans le .env. "
            "Variables attendues : HIMALIA_LOGIN et HIMALIA_PASSWORD."
        )
    return username, password


def _load_anticaptcha_key() -> str:
    """Retourne ANTICAPTCHA_KEY depuis le `.env` (ou l'environnement du process)."""
    _load_env()
    key = os.getenv("ANTICAPTCHA_KEY", "").strip()
    if not key:
        raise HimaliaScraperError(
            "Clé anticaptcha introuvable dans le .env (ou l'environnement). "
            "Variable attendue : ANTICAPTCHA_KEY. "
            "Obtenez une clé sur https://anti-captcha.com "
        )
    return key


def _load_otp_code() -> str:
    """Retourne un code OTP Himalia depuis l'environnement si disponible."""
    _load_env()
    candidates = [
        "HIMALIA_OTP_CODE",
        "HIMALIA_SECURITY_CODE",
        "HIMALIA_VERIFICATION_CODE",
    ]
    for name in candidates:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def _mask_secret(value: str, *, keep_start: int = 2, keep_end: int = 2) -> str:
    if keep_start == 0 and keep_end == 0:
        return "*" * len(value)
    if len(value) <= keep_start + keep_end:
        return "*" * len(value)
    return f"{value[:keep_start]}{'*' * (len(value) - keep_start - keep_end)}{value[-keep_end:]}"


# ---------------------------------------------------------------------------
# Utilitaires texte / fichiers
# ---------------------------------------------------------------------------


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "page"


def _sanitize_text(value: str) -> str:
    value = value.replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _collect_candidate_values(text: str) -> list[str]:
    amounts = re.findall(r"\b\d{1,3}(?:[ .]\d{3})*,\d{2}\s*€?", text)
    ordered: list[str] = []
    seen: set[str] = set()
    for raw in amounts:
        cleaned = _sanitize_text(raw)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            ordered.append(cleaned)
    return ordered[:50]


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_json(path: Path, payload: Any) -> None:
    _write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))


def _truncate_text(value: str | None, limit: int = 240) -> str | None:
    if value is None:
        return None
    return value if len(value) <= limit else value[: limit - 3] + "..."


def _filter_header_map(headers: dict[str, str] | None) -> dict[str, str]:
    if not headers:
        return {}
    allowed = {
        "content-type",
        "location",
        "server",
        "via",
        "x-cache",
        "x-frame-options",
        "cf-cache-status",
        "cf-mitigated",
        "x-cdn",
        "set-cookie",
    }
    filtered: dict[str, str] = {}
    for key, value in headers.items():
        lowered = key.lower()
        if lowered in allowed:
            filtered[lowered] = _truncate_text(value, 300) or ""
    return filtered


def _cookie_summary(cookies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for cookie in cookies:
        summary.append(
            {
                "name": cookie.get("name"),
                "domain": cookie.get("domain"),
                "path": cookie.get("path"),
                "expires": cookie.get("expires"),
                "httpOnly": cookie.get("httpOnly"),
                "secure": cookie.get("secure"),
                "sameSite": cookie.get("sameSite"),
                "value_preview": _mask_secret(cookie.get("value", ""), keep_start=6, keep_end=4),
            }
        )
    return summary


def _parse_french_number(value: Any) -> float | None:
    if value is None:
        return None
    text = _sanitize_text(str(value))
    text = (
        text.replace("\xa0", " ")
        .replace("€", "")
        .replace("%", "")
        .replace("?", "")
        .strip()
    )
    if not text or text in {"-", "nc"}:
        return None
    text = text.replace(" ", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def _extract_date_from_text(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"(\d{2})/(\d{2})/(\d{4})", value)
    if not match:
        return None
    day, month, year = match.groups()
    return f"{year}-{month}-{day}"


def _read_artifact_html(page_payload: dict[str, Any]) -> str:
    artifacts = page_payload.get("artifacts") or {}
    html_path = artifacts.get("html")
    if not html_path:
        return ""
    try:
        return Path(str(html_path)).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def _strip_tags(value: str) -> str:
    return _sanitize_text(unescape(re.sub(r"<[^>]+>", " ", value)))


# ---------------------------------------------------------------------------
# Snapshot de page
# ---------------------------------------------------------------------------


def _snapshot_page(page: Any, name: str, output_dir: Path) -> dict[str, Any]:
    """Capture HTML, screenshot et extraits structurés d'une page Playwright."""
    slug = _safe_slug(name)
    html_path = output_dir / f"{slug}.html"
    png_path = output_dir / f"{slug}.png"

    html = page.content()
    _write_text(html_path, html)
    page.screenshot(path=str(png_path), full_page=True)

    extracted = page.evaluate(
        """() => {
            const normalize = (value) => (value || "").replace(/\\s+/g, " ").trim();
            const bodyText = normalize(document.body ? document.body.innerText : "");
            const headings = Array.from(document.querySelectorAll("h1, h2, h3"))
                .map((node) => normalize(node.textContent))
                .filter(Boolean)
                .slice(0, 50);
            const tables = Array.from(document.querySelectorAll("table"))
                .map((table, index) => {
                    const rows = Array.from(table.querySelectorAll("tr"))
                        .map((row) => Array.from(row.querySelectorAll("th, td"))
                            .map((cell) => normalize(cell.textContent))
                            .filter(Boolean))
                        .filter((row) => row.length > 0)
                        .slice(0, 50);
                    return {
                        index,
                        headers: rows[0] || [],
                        rows: rows.slice(1, 21),
                    };
                })
                .filter((table) => table.headers.length || table.rows.length);
            const links = Array.from(document.querySelectorAll("a"))
                .map((link) => ({
                    text: normalize(link.textContent),
                    href: link.href || "",
                }))
                .filter((link) => link.text || link.href)
                .slice(0, 100);
            const buttons = Array.from(
                    document.querySelectorAll(
                        "button, [role='button'], input[type='submit'], input[type='button']"
                    )
                )
                .map((btn) => normalize(
                    btn.textContent || btn.value || btn.getAttribute("aria-label")
                ))
                .filter(Boolean)
                .slice(0, 50);
            return { title: document.title || "", bodyText, headings, tables, links, buttons };
        }"""
    )

    body_text = extracted.get("bodyText", "")
    excerpt_lines: list[str] = []
    for chunk in re.split(r"(?<=[.!?])\s+|\n+", body_text):
        cleaned = _sanitize_text(chunk)
        if cleaned and cleaned not in excerpt_lines:
            excerpt_lines.append(cleaned)
        if len(excerpt_lines) >= 25:
            break

    return {
        "name": name,
        "url": page.url,
        "title": extracted.get("title", ""),
        "headings": extracted.get("headings", []),
        "buttons": extracted.get("buttons", []),
        "links": extracted.get("links", []),
        "tables": extracted.get("tables", []),
        "body_excerpt": excerpt_lines,
        "value_candidates": _collect_candidate_values(body_text),
        "artifacts": {
            "html": str(html_path),
            "screenshot": str(png_path),
        },
    }


def _page_summary(page_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": page_payload.get("title"),
        "headings": page_payload.get("headings"),
        "buttons": page_payload.get("buttons"),
        "value_candidates": page_payload.get("value_candidates"),
        "tables_count": len(page_payload.get("tables") or []),
        "links_count": len(page_payload.get("links") or []),
        "body_excerpt": page_payload.get("body_excerpt"),
    }


def _build_himalia_contract_summary(page_payload: dict[str, Any], contract_id: str) -> dict[str, Any]:
    html = _read_artifact_html(page_payload)
    text = _strip_tags(html) if html else " ".join(page_payload.get("body_excerpt") or [])
    heading = next((item for item in (page_payload.get("headings") or []) if "Contrat HIMALIA" in item), None)
    contract_number_match = re.search(r"Contrat\s+HIMALIA\s+N[°º]?\s*([0-9]+)", heading or text, flags=re.IGNORECASE)
    effective_date_match = re.search(r"Date d'effet du contrat\s*:\s*(\d{2}/\d{2}/\d{4})", text, flags=re.IGNORECASE)
    fiscal_date_match = re.search(r"Date d'effet fiscale\s*:\s*(\d{2}/\d{2}/\d{4})", text, flags=re.IGNORECASE)
    valuation_match = re.search(r"Epargne atteinte.*?au\s+(\d{2}/\d{2}/\d{4})\s+([0-9 ]+,\d{2})\s*€", text, flags=re.IGNORECASE)
    subscriber_match = re.search(r"Souscripteur\s*:\s*([^:]+?)\s+Assuré", text, flags=re.IGNORECASE)
    insured_match = re.search(r"Assuré\(e\)\s*:\s*(.+?)\s+Nationalité", text, flags=re.IGNORECASE)
    status_match = re.search(r"Situation du contrat\s*:\s*([^:]+?)\s+Profil de gestion", text, flags=re.IGNORECASE)
    profile_match = re.search(r"Profil de gestion\s*:\s*([^:]+?)\s+Option de prévoyance", text, flags=re.IGNORECASE)
    tax_option_match = re.search(r"Option fiscale\s*:\s*([^:]+?)\s+Dématerialisation", text, flags=re.IGNORECASE)
    total_paid_match = re.search(r"Total versé depuis l.?origine\s*:\s*([0-9 ]+,\d{2})\s*€", text, flags=re.IGNORECASE)
    total_withdrawn_match = re.search(r"Total racheté depuis l'origine\s*:\s*([0-9 ]+,\d{2})\s*€", text, flags=re.IGNORECASE)
    total_invested_match = re.search(r"Total investi depuis l.?origine\s*:\s*([0-9 ]+,\d{2})\s*€", text, flags=re.IGNORECASE)
    return {
        "contract_id": contract_id,
        "contract_number": contract_number_match.group(1) if contract_number_match else None,
        "contract_label": "HIMALIA",
        "effective_date": _extract_date_from_text(effective_date_match.group(1) if effective_date_match else None),
        "fiscal_effective_date": _extract_date_from_text(fiscal_date_match.group(1) if fiscal_date_match else None),
        "currency": "EUR",
        "official_total_valuation": _parse_french_number(valuation_match.group(2) if valuation_match else None),
        "valuation_date": _extract_date_from_text(valuation_match.group(1) if valuation_match else None),
        "subscriber": _sanitize_text(subscriber_match.group(1)) if subscriber_match else None,
        "insured": _sanitize_text(insured_match.group(1)) if insured_match else None,
        "contract_status": _sanitize_text(status_match.group(1)) if status_match else None,
        "management_profile": _sanitize_text(profile_match.group(1)) if profile_match else None,
        "tax_option": _sanitize_text(tax_option_match.group(1)) if tax_option_match else None,
        "total_paid_in": _parse_french_number(total_paid_match.group(1) if total_paid_match else None),
        "total_withdrawn": _parse_french_number(total_withdrawn_match.group(1) if total_withdrawn_match else None),
        "total_invested": _parse_french_number(total_invested_match.group(1) if total_invested_match else None),
        "raw": {
            "headings": page_payload.get("headings"),
            "body_excerpt": page_payload.get("body_excerpt"),
        },
    }


def _extract_himalia_visible_metrics(page_payload: dict[str, Any]) -> dict[str, Any]:
    html = _read_artifact_html(page_payload)
    text = _strip_tags(html) if html else " ".join(page_payload.get("body_excerpt") or [])
    valuation_match = re.search(r"Epargne atteinte.*?au\s+(\d{2}/\d{2}/\d{4})\s+([0-9 ]+,\d{2})\s*€", text, flags=re.IGNORECASE)
    total_paid_match = re.search(r"Total versé depuis l.?origine\s*:\s*([0-9 ]+,\d{2})\s*€", text, flags=re.IGNORECASE)
    total_withdrawn_match = re.search(r"Total racheté depuis l'origine\s*:\s*([0-9 ]+,\d{2})\s*€", text, flags=re.IGNORECASE)
    total_invested_match = re.search(r"Total investi depuis l.?origine\s*:\s*([0-9 ]+,\d{2})\s*€", text, flags=re.IGNORECASE)
    return {
        "reference_date": _extract_date_from_text(valuation_match.group(1) if valuation_match else None),
        "visible_total_valuation": _parse_french_number(valuation_match.group(2) if valuation_match else None),
        "visible_total_paid_in": _parse_french_number(total_paid_match.group(1) if total_paid_match else None),
        "visible_total_withdrawn": _parse_french_number(total_withdrawn_match.group(1) if total_withdrawn_match else None),
        "visible_total_invested": _parse_french_number(total_invested_match.group(1) if total_invested_match else None),
    }


def _normalize_himalia_positions(page_payload: dict[str, Any]) -> list[dict[str, Any]]:
    html = _read_artifact_html(page_payload)
    if not html:
        return []
    rows = re.findall(
        r"<tr>\s*<td class=\"lpTableau lpTableauFirstCol\"><a [^>]*>(.*?)</a></td>\s*"
        r"<td class=\"lpTableau\" align=\"center\">(.*?)</td>\s*"
        r"<td class=\"lpTableau\" align=\"right\" nowrap=\"\">(.*?)</td>\s*"
        r"<td class=\"lpTableau\" align=\"right\" nowrap=\"\">(.*?)</td>\s*"
        r"<td class=\"lpTableau\" align=\"right\" nowrap=\"\">(.*?)</td>\s*"
        r"<td class=\"lpTableau\" align=\"right\" nowrap=\"\">(.*?)</td>\s*"
        r"<td class=\"lpTableau\" align=\"right\" nowrap=\"\">(.*?)</td>\s*"
        r"<td class=\"lpTableau lpTableauLastCol\" nowrap=\"\" align=\"right\">(.*?)</td>\s*</tr>",
        html,
        flags=re.S,
    )
    positions: list[dict[str, Any]] = []
    for asset_name, value_date, nav, units, valuation, pam, plus_minus, perf in rows:
        positions.append(
            {
                "asset_name": _strip_tags(asset_name),
                "asset_isin": None,
                "support_type": None,
                "valuation_date": _extract_date_from_text(_strip_tags(value_date)),
                "nav": _parse_french_number(_strip_tags(nav)),
                "units": _parse_french_number(_strip_tags(units)),
                "valuation": _parse_french_number(_strip_tags(valuation)),
                "purchase_price_avg": _parse_french_number(_strip_tags(pam)),
                "plus_minus_value": _parse_french_number(_strip_tags(plus_minus)),
                "performance_pct": _parse_french_number(_strip_tags(perf)),
                "raw": {
                    "value_date": _strip_tags(value_date),
                    "nav": _strip_tags(nav),
                    "units": _strip_tags(units),
                    "valuation": _strip_tags(valuation),
                    "purchase_price_avg": _strip_tags(pam),
                    "plus_minus_value": _strip_tags(plus_minus),
                    "performance_pct": _strip_tags(perf),
                },
            }
        )
    positions.sort(key=lambda row: row.get("valuation") or 0.0, reverse=True)
    return positions


def _normalize_himalia_operations(page_payload: dict[str, Any]) -> list[dict[str, Any]]:
    html = _read_artifact_html(page_payload)
    if not html:
        return []
    rows = re.findall(
        r"<tr>\s*<td class=\"(?:lp|li)Tableau (?:lp|li)TableauFirstCol\" align=\"center\">(?:<a [^>]*>)?(.*?)(?:</a>)?</td>\s*"
        r"<td class=\"(?:lp|li)Tableau\">(.*?)</td>\s*"
        r"<td class=\"(?:lp|li)Tableau (?:lp|li)TableauLastCol\" align=\"right\">(.*?)</td>\s*</tr>",
        html,
        flags=re.S,
    )
    operations: list[dict[str, Any]] = []
    for effect_date, label, gross_amount in rows:
        clean_date = _strip_tags(effect_date)
        if clean_date == "Date d'effet":
            continue
        operations.append(
            {
                "operation_date": _extract_date_from_text(clean_date),
                "label": _strip_tags(label),
                "gross_amount": _parse_french_number(_strip_tags(gross_amount)),
                "raw": {
                    "effect_date": clean_date,
                    "label": _strip_tags(label),
                    "gross_amount": _strip_tags(gross_amount),
                },
            }
        )
    operations.sort(key=lambda row: row.get("operation_date") or "", reverse=True)
    return operations


def _normalize_himalia_documents(page_payload: dict[str, Any]) -> list[dict[str, Any]]:
    html = _read_artifact_html(page_payload)
    if not html:
        return []
    documents: list[dict[str, Any]] = []
    avenants = re.findall(
        r"<tr>\s*<td class=\"(?:lp|li)Tableau (?:lp|li)TableauFirstCol\"[^>]*>(.*?)<input.*?</td>\s*"
        r"<td class=\"(?:lp|li)Tableau\">(.*?)</td>\s*"
        r"<td class=\"(?:lp|li)Tableau (?:lp|li)TableauLastCol\"><a href=\"#\" onclick=\"javascript:OpenWin\('(.*?)','Avenant'",
        html,
        flags=re.S,
    )
    for sent_date, doc_type, path in avenants:
        documents.append(
            {
                "category": "avenant",
                "document_date": _extract_date_from_text(_strip_tags(sent_date)),
                "label": _strip_tags(doc_type),
                "download_path": unescape(path),
            }
        )
    releves = re.findall(
        r"<tr>\s*<td class=\"(?:lp|li)Tableau (?:lp|li)TableauFirstCol\"[^>]*><a href=\"#\" onclick=\"javascript:(?:OpenWin|creerPageExterne)\('(.*?)'.*?\">(.*?)<input.*?</a></td>\s*"
        r"<td class=\"(?:lp|li)Tableau (?:lp|li)TableauLastCol\">(.*?)</td>",
        html,
        flags=re.S,
    )
    for path, period, label in releves:
        documents.append(
            {
                "category": "releve",
                "document_date": _extract_date_from_text(_strip_tags(period)),
                "label": _strip_tags(label),
                "download_path": unescape(path),
                "period_label": _strip_tags(period),
            }
        )
    ifi_docs = re.findall(
        r"<tr>\s*<td class=\"(?:lp|li)Tableau (?:lp|li)TableauFirstCol\"[^>]*><a href=\"#\" onclick=\"javascript:creerPageExterne\('(.*?)'.*?\">(.*?)<input.*?</a></td>\s*"
        r"<td class=\"(?:lp|li)Tableau (?:lp|li)TableauLastCol\">(.*?)</td>",
        html,
        flags=re.S,
    )
    for path, when, label in ifi_docs:
        if "IFI" not in path and "fortune" not in _strip_tags(label).lower():
            continue
        documents.append(
            {
                "category": "fiscal",
                "document_date": _extract_date_from_text(_strip_tags(when)),
                "label": _strip_tags(label),
                "download_path": unescape(path),
            }
        )
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for row in documents:
        key = (row.get("category"), row.get("document_date"), row.get("label"), row.get("download_path"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    deduped.sort(key=lambda row: row.get("document_date") or "", reverse=True)
    return deduped


# ---------------------------------------------------------------------------
# Détection session expirée
# ---------------------------------------------------------------------------


def _detect_session_expired(page: Any) -> bool:
    """Retourne True si la page indique que la session est expirée ou invalide."""
    if LOGIN_URL in page.url or "EntAccLog" in page.url:
        return True
    try:
        body = page.inner_text("body", timeout=5_000)
    except Exception:
        return False
    body_lower = _sanitize_text(body).lower()
    return any(token in body_lower for token in SESSION_EXPIRED_TOKENS)


def _detect_otp_required(page: Any) -> bool:
    if "task=SaisirCodeOTP" in page.url:
        return True
    try:
        body = page.inner_text("body", timeout=5_000)
    except Exception:
        return False
    body_lower = _sanitize_text(body).lower()
    if any(token in body_lower for token in OTP_TOKENS):
        return True
    try:
        otp_fields = page.locator(
            "input[name*='otp' i], input[name*='code' i], input[id*='otp' i], input[id*='code' i]"
        )
        return otp_fields.count() > 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Captcha — détection et résolution via anticaptchaofficial
# ---------------------------------------------------------------------------


def _detect_captcha_on_page(page: Any) -> dict[str, Any]:
    """Détecte le type de captcha et extrait la site key depuis la page.

    Supporte : FriendlyCaptcha uniquement (cas Himalia).
    """
    return page.evaluate(
        """() => {
            // FriendlyCaptcha (v1/v2)
            const fcEl = document.querySelector('.frc-captcha, [data-sitekey][data-widget][data-start], [data-sitekey][data-solution-field-name]');
            const fcKey = fcEl ? fcEl.getAttribute('data-sitekey') : null;
            const fcSolutionFieldName = fcEl ? (fcEl.getAttribute('data-solution-field-name') || fcEl.getAttribute('data-response-field-name')) : null;

            return {
                friendlycaptcha_key: fcKey,
                friendlycaptcha_solution_field_name: fcSolutionFieldName,
            };
        }"""
    )


def _inject_friendlycaptcha_token(page: Any, token: str, solution_field_name: str | None) -> None:
    """Injecte un token FriendlyCaptcha dans la page (v1/v2)."""
    page.evaluate(
        """([token, solutionFieldName]) => {
            const candidates = [];
            if (solutionFieldName) candidates.push(solutionFieldName);
            // FriendlyCaptcha v1 / v2
            candidates.push('frc-captcha-solution');
            candidates.push('frc-captcha-response');

            for (const name of candidates) {
                document.querySelectorAll(`[name="${name}"]`).forEach((f) => { f.value = token; });
            }

            // Certains widgets stockent aussi la valeur dans un textarea
            document.querySelectorAll('textarea[name], input[type="hidden"][name]').forEach((f) => {
                const n = (f.getAttribute('name') || '').toLowerCase();
                if (n.includes('frc') && (n.includes('solution') || n.includes('response'))) {
                    f.value = token;
                }
            });
        }""",
        [token, solution_field_name],
    )


def _resolve_friendlycaptcha_in_browser(
    page: Any,
    solution_field_name: str | None,
    *,
    timeout_ms: int = 30_000,
) -> str | None:
    """Essaie de laisser le widget FriendlyCaptcha se résoudre nativement dans le navigateur."""
    clicked = _click_first_visible(
        page,
        [
            ".frc-button",
            "button.frc-button",
            "button:has-text('Clique ici pour vérifier')",
            "button:has-text('Cliquer ici pour vérifier')",
        ],
        timeout_ms=3_000,
    )
    if not clicked:
        return None

    deadline_script = f"""
        ([solutionFieldName, timeoutMs]) => new Promise((resolve) => {{
            const names = [];
            if (solutionFieldName) names.push(solutionFieldName);
            names.push('frc-captcha-solution', 'frc-captcha-response');

            const readValue = () => {{
                for (const name of names) {{
                    const field = document.querySelector(`[name="${{name}}"]`);
                    const value = field ? (field.value || '').trim() : '';
                    if (value && value !== '.UNSTARTED') return value;
                }}
                return '';
            }};

            const immediate = readValue();
            if (immediate) {{
                resolve(immediate);
                return;
            }}

            const startedAt = Date.now();
            const timer = setInterval(() => {{
                const value = readValue();
                if (value) {{
                    clearInterval(timer);
                    resolve(value);
                    return;
                }}
                if (Date.now() - startedAt >= timeoutMs) {{
                    clearInterval(timer);
                    resolve('');
                }}
            }}, 500);
        }})
    """
    token = page.evaluate(deadline_script, [solution_field_name, timeout_ms])
    return token or None


def _read_existing_friendlycaptcha_token(page: Any, solution_field_name: str | None) -> str | None:
    """Retourne un token FriendlyCaptcha déjà présent dans la page, si disponible."""
    return page.evaluate(
        """([solutionFieldName]) => {
            const names = [];
            if (solutionFieldName) names.push(solutionFieldName);
            names.push('frc-captcha-solution', 'frc-captcha-response');

            for (const name of names) {
                const field = document.querySelector(`[name="${name}"]`);
                const value = field ? (field.value || '').trim() : '';
                if (value && value !== '.UNSTARTED') return value;
            }

            const successText = (document.body ? document.body.innerText : '') || '';
            const successClass = !!document.querySelector('.frc-container.frc-success, .frc-success');
            if (successClass || successText.includes('Je ne suis pas un robot') || successText.includes('Vérification automatique des spams terminée')) {
                const fallback = document.querySelector('input[name="frc-captcha-solution"], input[name="frc-captcha-response"]');
                const value = fallback ? (fallback.value || '').trim() : '';
                if (value && value !== '.UNSTARTED') return value;
                return '__FRC_BROWSER_SUCCESS__';
            }

            return '';
        }""",
        [solution_field_name],
    ) or None


def _solve_captcha(
    page: Any,
    anticaptcha_key: str,
    captcha_info: dict[str, Any],
) -> dict[str, Any]:
    """Résout le captcha détecté via python3-anticaptcha et l'injecte dans la page.

    Docs: [python3-anticaptcha](https://andreidrang.github.io/python3-anticaptcha/)

    Supporte FriendlyCaptcha uniquement.
    Retourne un dict avec 'type', 'site_key', 'token_length'.
    """
    page_url = page.url

    # --- FriendlyCaptcha --- (proxyless)
    if captcha_info.get("friendlycaptcha_key"):
        site_key = captcha_info["friendlycaptcha_key"]
        solution_field_name = captcha_info.get("friendlycaptcha_solution_field_name")
        existing_token = _read_existing_friendlycaptcha_token(page, solution_field_name)
        if existing_token:
            if existing_token != "__FRC_BROWSER_SUCCESS__":
                _inject_friendlycaptcha_token(page, existing_token, solution_field_name)
            return {
                "type": "friendlycaptcha_browser",
                "site_key": site_key,
                "token_length": 0 if existing_token == "__FRC_BROWSER_SUCCESS__" else len(existing_token),
                "source": "existing",
            }
        browser_token = _resolve_friendlycaptcha_in_browser(page, solution_field_name)
        if browser_token:
            _inject_friendlycaptcha_token(page, browser_token, solution_field_name)
            return {
                "type": "friendlycaptcha_browser",
                "site_key": site_key,
                "token_length": len(browser_token),
                "source": "interactive",
            }
        try:
            from python3_anticaptcha.friendly_captcha import FriendlyCaptcha
        except Exception as exc:
            raise HimaliaScraperError(
                "Dépendance python3-anticaptcha introuvable. "
                "Installez-la avec : pip install python3-anticaptcha"
            ) from exc

        payload = FriendlyCaptcha(
            api_key=anticaptcha_key,
            captcha_type="FriendlyCaptchaTaskProxyless",
            websiteURL=page_url,
            websiteKey=site_key,
        ).captcha_handler()
        token = ((payload or {}).get("solution") or {}).get("token")
        if not token:
            error_code = (payload or {}).get("errorCode")
            error_desc = (payload or {}).get("errorDescription")
            raise HimaliaScraperError(
                "Résolution FriendlyCaptcha échouée. "
                f"errorCode={error_code} errorDescription={error_desc}"
            )
        _inject_friendlycaptcha_token(page, token, solution_field_name)
        return {"type": "friendlycaptcha", "site_key": site_key, "token_length": len(token)}

    # --- reCAPTCHA v3 ---
    return {"type": "none", "detected": False}


# ---------------------------------------------------------------------------
# Login automatique
# ---------------------------------------------------------------------------


def _fill_first_visible(page: Any, selectors: list[str], value: str, *, timeout_ms: int = 5_000) -> str:
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if locator.is_visible(timeout=timeout_ms):
                locator.fill(value, timeout=timeout_ms)
                return selector
        except Exception:
            continue
    raise HimaliaScraperError(f"Champ introuvable parmi les sélecteurs : {selectors}")


def _click_first_visible(page: Any, selectors: list[str], *, timeout_ms: int = 5_000) -> str | None:
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if locator.is_visible(timeout=timeout_ms):
                locator.click(timeout=timeout_ms)
                return selector
        except Exception:
            continue
    return None


def _submit_otp_code(page: Any, otp_code: str, output_dir: Path) -> dict[str, Any]:
    otp_selector = _fill_first_visible(
        page,
        [
            "input[name='codeVerificationOTP']",
            "input[id*='OTP' i]",
            "input[name*='otp' i]",
            "input[name*='code' i]",
        ],
        otp_code,
    )
    submit_selector = _click_first_visible(
        page,
        [
            "a[href*='task=ValiderCodeOTP']",
            "a:has-text('Valider')",
            "#boutonValider",
            "button:has-text('Valider')",
        ],
    )
    if submit_selector is None:
        try:
            page.locator("form[name='nomForm']").evaluate("(form) => form.submit()")
            submit_selector = "form[name='nomForm'].submit()"
        except Exception:
            submit_selector = None

    page.wait_for_load_state("domcontentloaded", timeout=60_000)
    page.wait_for_timeout(3_000)
    otp_after_snapshot = _snapshot_page(page, "login_otp_after", output_dir)
    return {
        "otp_selector": otp_selector,
        "otp_submit_selector": submit_selector,
        "otp_after_url": page.url,
        "otp_after_excerpt": otp_after_snapshot["body_excerpt"][:5],
    }


def _resume_pending_otp(page: Any, otp_code: str, output_dir: Path) -> dict[str, Any]:
    """Recharge la page OTP courante et soumet le code fourni."""
    page.goto(OTP_URL, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(2_500)
    before_snapshot = _snapshot_page(page, "login_otp_resume", output_dir)
    if not _detect_otp_required(page):
        return {
            "resume_url": page.url,
            "resume_excerpt": before_snapshot["body_excerpt"][:5],
            "otp_not_required": True,
        }
    otp_meta = _submit_otp_code(page, otp_code, output_dir)
    return {
        "resume_url": before_snapshot["url"],
        "resume_excerpt": before_snapshot["body_excerpt"][:5],
        **otp_meta,
    }


def _open_login_entry(page: Any, output_dir: Path) -> dict[str, Any]:
    """Passe par la page d'entrée publique avant le formulaire de login si possible."""
    page.goto(ENTRY_URL, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(2_500)
    entry_snapshot = _snapshot_page(page, "login_entry", output_dir)

    clicked_selector = _click_first_visible(
        page,
        [
            "a:has-text('Accès client')",
            "a:has-text('Acces client')",
            "button:has-text('Accès client')",
            "button:has-text('Acces client')",
            "[role='button']:has-text('Accès client')",
            "[role='button']:has-text('Acces client')",
            "a[href*='EntAccLog']",
        ],
        timeout_ms=4_000,
    )
    if clicked_selector:
        page.wait_for_load_state("domcontentloaded", timeout=60_000)
        page.wait_for_timeout(2_500)
        return {
            "entry_url": ENTRY_URL,
            "entry_title": entry_snapshot.get("title"),
            "entry_selector": clicked_selector,
            "entry_buttons": entry_snapshot.get("buttons"),
            "entry_links_count": len(entry_snapshot.get("links") or []),
        }

    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(2_500)
    return {
        "entry_url": ENTRY_URL,
        "entry_title": entry_snapshot.get("title"),
        "entry_selector": None,
        "entry_buttons": entry_snapshot.get("buttons"),
        "entry_links_count": len(entry_snapshot.get("links") or []),
        "fallback_to_login_url": True,
    }


def _perform_auto_login(
    page: Any,
    username: str,
    password: str,
    anticaptcha_key: str,
    output_dir: Path,
    pending_otp_session_path: Path | None = None,
) -> dict[str, Any]:
    """Navigue sur la page de login, résout les captchas et remplit les credentials.

    Workflow :
      1. Chargement de la page de login
      2. Détection et résolution d'un éventuel captcha d'accès (Turnstile, etc.)
      3. Remplissage du formulaire (login + password)
      4. Détection et résolution d'un éventuel captcha de soumission (reCAPTCHA v2/v3, hCaptcha)
      5. Soumission du formulaire et vérification de la connexion

    Retourne un dict de métadonnées. Lève HimaliaScraperError si la connexion échoue.
    """
    entry_meta = _open_login_entry(page, output_dir)
    before_snapshot = None
    security_error = False

    # Certains WAF posent un cookie/challenge sur la première visite, puis la page
    # de formulaire devient accessible lors d'un second chargement avec le même contexte.
    for attempt in range(3):
        if attempt > 0:
            entry_meta = _open_login_entry(page, output_dir)
        page.wait_for_timeout(3_500 if attempt == 0 else 2_500)
        before_snapshot = _snapshot_page(page, "login_before", output_dir)
        body_lower = " ".join(before_snapshot["body_excerpt"]).lower()
        security_error = any(token in body_lower for token in SESSION_EXPIRED_TOKENS)
        if not security_error:
            break
        if attempt < 2:
            page.wait_for_timeout(1_500)

    if security_error:
        raise HimaliaSecurityBlockError(
            "La page de login Himalia a renvoyé une erreur de sécurité avant l'affichage du "
            "formulaire. Le navigateur automatisé est peut-être détecté. "
            "Consultez les artefacts `login_before.html` / `login_before.png` dans le dossier de logs."
        )

    # Étape 1 — résoudre un captcha d'accès éventuel (FriendlyCaptcha) avant le formulaire
    pre_captcha_info = _detect_captcha_on_page(page)
    pre_captcha_result = _solve_captcha(page, anticaptcha_key, pre_captcha_info)
    if pre_captcha_result["type"] != "none":
        # Laisser le captcha se valider et recharger si nécessaire
        page.wait_for_timeout(2_000)

    # Étape 2 — remplir les credentials
    username_selector = _fill_first_visible(
        page,
        [
            "input[name='login']",
            "input[name='identifiant']",
            "input[name='username']",
            "input[type='text']",
        ],
        username,
    )
    password_selector = _fill_first_visible(
        page,
        [
            "input[name='password']",
            "input[name='motDePasse']",
            "input[type='password']",
        ],
        password,
    )

    # Étape 3 — résoudre un captcha de soumission (FriendlyCaptcha)
    page.wait_for_timeout(1_000)
    post_captcha_info = _detect_captcha_on_page(page)
    post_captcha_result = _solve_captcha(page, anticaptcha_key, post_captcha_info)

    # Combiner les résultats captcha pour le log
    captcha_results = [r for r in [pre_captcha_result, post_captcha_result] if r["type"] != "none"]
    captcha_summary = captcha_results if captcha_results else [{"type": "none", "detected": False}]

    # Étape 4 — soumettre le formulaire
    page.wait_for_timeout(500)
    submit_selector = _click_first_visible(
        page,
        [
            "#boutonValider",
            "a#boutonValider",
            "a[href*='task=Valider']",
            "a[href^='javascript:document.nomForm.submit']",
            "a:has-text('Valider')",
            "input[type='submit']",
            "button[type='submit']",
            "button:has-text('Valider')",
            "button:has-text('Se connecter')",
            "button:has-text('Connexion')",
        ],
    )
    if submit_selector is None:
        try:
            page.locator("form[name='nomForm']").evaluate("(form) => form.submit()")
            submit_selector = "form[name='nomForm'].submit()"
        except Exception:
            pass

    page.wait_for_load_state("domcontentloaded", timeout=60_000)
    page.wait_for_timeout(3_000)

    after_snapshot = _snapshot_page(page, "login_after", output_dir)

    if _detect_otp_required(page):
        _snapshot_page(page, "login_otp_required", output_dir)
        otp_code = _load_otp_code()
        if not otp_code:
            if pending_otp_session_path is not None:
                pending_otp_session_path.parent.mkdir(parents=True, exist_ok=True)
                page.context.storage_state(path=str(pending_otp_session_path))
            raise HimaliaOtpRequiredError(
                "Connexion Himalia interrompue par une étape OTP après soumission du formulaire. "
                "Fournissez un code via `HIMALIA_OTP_CODE=123456 make himalia-scrape` pour reprendre la session OTP en attente. "
                "Consultez les artefacts `login_after.*` / `login_otp_required.*`."
            )
        otp_meta = _submit_otp_code(page, otp_code, output_dir)
        if _detect_otp_required(page):
            raise HimaliaOtpRequiredError(
                "Le code OTP Himalia a été soumis mais une étape OTP est toujours affichée. "
                "Le code est peut-être expiré ou invalide. "
                "Consultez les artefacts `login_otp_required.*` / `login_otp_after.*`."
            )
        final_url = page.url
        if TARGET_DOMAIN not in final_url or "EntAccLog" in final_url:
            excerpt = " ".join(otp_meta["otp_after_excerpt"])
            raise HimaliaScraperError(
                f"Connexion Himalia non confirmée après soumission OTP. URL courante : {final_url}. "
                f"Extrait page : {excerpt[:300]}"
            )
        return {
            "entry_meta": entry_meta,
            "final_url": final_url,
            "username_selector": username_selector,
            "password_selector": password_selector,
            "submit_selector": submit_selector,
            "captcha": captcha_summary,
            "otp": otp_meta,
        }

    final_url = page.url
    if TARGET_DOMAIN not in final_url or "EntAccLog" in final_url:
        excerpt = " ".join(after_snapshot["body_excerpt"][:5])
        raise HimaliaScraperError(
            f"Connexion Himalia non confirmée. URL courante : {final_url}. "
            f"Extrait page : {excerpt[:300]}"
        )

    return {
        "entry_meta": entry_meta,
        "final_url": final_url,
        "username_selector": username_selector,
        "password_selector": password_selector,
        "submit_selector": submit_selector,
        "captcha": captcha_summary,
        "username_masked": _mask_secret(username),
        "password_masked": _mask_secret(password, keep_start=0, keep_end=0),
    }


# ---------------------------------------------------------------------------
# Attente de connexion manuelle (mode --manual)
# ---------------------------------------------------------------------------


def _wait_for_manual_login(page: Any, *, timeout_ms: int) -> bool:
    """Attend que l'utilisateur complète la connexion manuelle.

    Considère que la connexion est réussie dès qu'on quitte la page de login
    tout en restant sur le domaine cible.

    Retourne True si une URL de post-login est détectée, False si timeout.
    """
    deadline = datetime.now(timezone.utc).timestamp() + (timeout_ms / 1000)
    elapsed_shown = False
    while datetime.now(timezone.utc).timestamp() < deadline:
        url = page.url
        # Toute page sur le domaine cible qui n'est pas la page de login
        if TARGET_DOMAIN in url and "EntAccLog" not in url:
            return True
        remaining = int(deadline - datetime.now(timezone.utc).timestamp())
        if not elapsed_shown or remaining % 30 == 0:
            print(f"  En attente de connexion manuelle... ({remaining}s restantes) — URL: {url}")
            elapsed_shown = True
        try:
            page.wait_for_timeout(2_000)
        except Exception:
            break
    return False


# ---------------------------------------------------------------------------
# API publique
# ---------------------------------------------------------------------------


def setup_himalia_session(
    *,
    output_path: Path | None = None,
    wait_timeout_ms: int = 300_000,
    data_dir: Path | None = None,
    manual: bool = False,
) -> dict[str, Any]:
    """Crée une session Himalia persistée pour usage ultérieur par scrape_himalia_contract().

    Mode automatique (défaut) :
      - Charge HIMALIA_LOGIN, HIMALIA_PASSWORD et ANTICAPTCHA_KEY depuis .env
      - Remplit le formulaire de login et résout le captcha via anticaptchaofficial
      - Lance le navigateur en mode headless

    Mode manuel (manual=True) :
      - Ouvre un navigateur headed sur la page de login
      - Attend que l'utilisateur se connecte manuellement (identifiants + captcha)

    Dans les deux cas, sauvegarde le storage_state Playwright dans output_path.

    Args:
        output_path: Chemin de sauvegarde (JSON). Défaut : data/logs/himalia/session.json
        wait_timeout_ms: Délai max en mode manuel (ms, défaut 5 min).
        data_dir: Répertoire de données.
        manual: Si True, connexion manuelle (navigateur headed).

    Returns:
        dict avec 'ok', 'session_path', 'setup_at_utc', 'final_url', 'mode'.
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise HimaliaScraperError(
            "Playwright n'est pas installé. Installe-le avec `pip install playwright` "
            "puis `python -m playwright install chromium`."
        ) from exc

    if output_path is None:
        output_path = _default_session_path(data_dir)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    setup_at = datetime.now(timezone.utc).isoformat()
    log_dir = output_path.parent / ("setup_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    log_dir.mkdir(parents=True, exist_ok=True)

    login_result: dict[str, Any] = {}

    with sync_playwright() as playwright:
        browser = _launch_browser(playwright, headless=not manual)
        context = browser.new_context(**_CONTEXT_KWARGS)
        context.add_init_script(_STEALTH_SCRIPT)
        page = context.new_page()

        if manual:
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60_000)
            print("\n" + "=" * 60)
            print("HIMALIA — Connexion manuelle requise")
            print("=" * 60)
            print(f"URL  : {LOGIN_URL}")
            print(f"Délai: {wait_timeout_ms // 1000}s")
            print("\nConnectez-vous dans le navigateur ouvert.")
            print("Si un captcha apparaît, résolvez-le manuellement.")
            print("La session sera sauvegardée automatiquement après connexion.")
            print("=" * 60 + "\n")

            logged_in = _wait_for_manual_login(page, timeout_ms=wait_timeout_ms)
            final_url = page.url
            if not logged_in:
                context.close()
                browser.close()
                raise HimaliaScraperError(
                    f"Délai d'attente dépassé sans connexion confirmée. "
                    f"URL courante : {final_url}"
                )
            login_result["final_url"] = final_url
        else:
            username, password = _load_credentials()
            anticaptcha_key = _load_anticaptcha_key()
            login_result = _perform_auto_login(page, username, password, anticaptcha_key, log_dir)

        context.storage_state(path=str(output_path))
        context.close()
        browser.close()

    print(f"\nSession sauvegardée : {output_path}")
    return {
        "ok": True,
        "mode": "manual" if manual else "auto",
        "session_path": str(output_path),
        "setup_at_utc": setup_at,
        "final_url": login_result.get("final_url"),
        "login": {k: v for k, v in login_result.items() if k != "final_url"},
    }


def scrape_himalia_contract(
    *,
    contract_id: str = "222387113",
    data_dir: Path | None = None,
    output_path: Path | None = None,
    storage_state: Path | None = None,
    user_data_dir: Path | None = None,
    headless: bool = True,
) -> dict[str, Any]:
    """Collecte les pages contrat, documents et mouvements Himalia via session persistée.

    Nécessite une session créée au préalable avec setup_himalia_session() /
    `make himalia-setup-session`.

    Args:
        contract_id: Numéro de contrat Himalia.
        data_dir: Répertoire de données.
        output_path: Chemin du fichier de log JSON (optionnel).
        storage_state: Chemin vers un fichier storage_state Playwright JSON.
                       Par défaut : data/logs/himalia/session.json (si existant).
        user_data_dir: Chemin vers un répertoire de profil Chromium persistant.
                       Alternatif à storage_state.
        headless: Lance le navigateur en mode headless (défaut True).
                  Passer False pour diagnostiquer visuellement les problèmes de session.

    Returns:
        dict avec 'ok', 'log_path', 'artifacts_dir', résumé des pages collectées.

    Raises:
        HimaliaSessionExpiredError: Si la session est expirée ou invalide.
        HimaliaScraperError: Pour toute autre erreur.
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise HimaliaScraperError(
            "Playwright n'est pas installé. Installe-le avec `pip install playwright` "
            "puis `python -m playwright install chromium`."
        ) from exc

    # Résolution de la session (même philosophie que SwissLife: une seule commande)
    default_session = _default_session_path(data_dir)
    pending_otp_session = _default_pending_otp_session_path(data_dir)
    provided_otp_code = _load_otp_code()
    resuming_pending_otp = False
    if storage_state is None and user_data_dir is None:
        if provided_otp_code and pending_otp_session.exists():
            storage_state = pending_otp_session
            resuming_pending_otp = True
        elif default_session.exists():
            storage_state = default_session

    if storage_state is not None and not storage_state.exists():
        raise HimaliaScraperError(f"Fichier de session introuvable : {storage_state}.")

    session_mode = "user_data_dir" if user_data_dir is not None else "storage_state"
    session_source = str(user_data_dir if user_data_dir is not None else (storage_state or default_session))

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = (output_path.parent if output_path else _default_output_dir(data_dir) / timestamp).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_path.resolve() if output_path else output_dir / "himalia_scrape_log.json"

    response_events: list[dict[str, Any]] = []
    failed_requests: list[dict[str, Any]] = []
    console_events: list[dict[str, Any]] = []
    pages_collected: list[dict[str, Any]] = []
    login_diagnostics: list[dict[str, Any]] = []

    result: dict[str, Any] = {
        "scraped_at_utc": datetime.now(timezone.utc).isoformat(),
        "contract_id": contract_id,
        "session_mode": session_mode,
        "session_source": session_source,
        "credentials": {
            "username_env": "HIMALIA_LOGIN",
            "password_env": "HIMALIA_PASSWORD",
            # masquage strict (jamais en clair)
            "username_masked": None,
            "password_masked": None,
            "username_length": None,
            "password_length": None,
        },
        "pages": pages_collected,
        "network": {
            "responses_sample": [],
            "failed_requests": failed_requests,
            "console_events": [],
        },
        "login_diagnostics": login_diagnostics,
        "artifacts_dir": str(output_dir),
    }
    if resuming_pending_otp:
        result["resuming_pending_otp"] = True

    def _on_response(response: Any) -> None:
        if TARGET_DOMAIN not in response.url:
            return
        headers = {}
        try:
            headers = dict(response.headers or {})
        except Exception:
            headers = {}
        response_events.append(
            {
                "url": response.url,
                "status": response.status,
                "ok": response.ok,
                "content_type": headers.get("content-type"),
                "headers": _filter_header_map(headers),
            }
        )

    def _on_request_failed(request: Any) -> None:
        if TARGET_DOMAIN not in request.url:
            return
        failed_requests.append(
            {
                "url": request.url,
                "method": request.method,
                "failure": str(request.failure),
            }
        )

    browser = None

    def _record_login_diagnostic(page: Any, context: Any, *, stage: str, mode: str) -> None:
        try:
            cookies = context.cookies([LOGIN_URL])
        except Exception:
            cookies = []
        try:
            body_excerpt = page.locator("body").inner_text(timeout=2_000)
            body_excerpt = _sanitize_text(body_excerpt)[:400]
        except Exception:
            body_excerpt = ""
        login_diagnostics.append(
            {
                "stage": stage,
                "mode": mode,
                "url": page.url,
                "title": _truncate_text(page.title(), 200),
                "body_excerpt": body_excerpt,
                "cookies": _cookie_summary(cookies),
            }
        )

    def _wire_page_events(page: Any) -> None:
        page.on("response", _on_response)
        page.on("requestfailed", _on_request_failed)
        page.on(
            "console",
            lambda msg: console_events.append({"type": msg.type, "text": msg.text}),
        )

    with sync_playwright() as playwright:
        context = None
        page = None

        def _new_context_and_page(
            *,
            prefer_persistent: bool,
            force_headless: bool | None = None,
        ) -> tuple[Any, Any, Any | None]:
            """Crée un context/page. Si prefer_persistent=True, utilise un user-data-dir local."""
            effective_headless = headless if force_headless is None else force_headless
            if user_data_dir is not None:
                ctx = _launch_persistent_context(playwright, user_data_dir, headless=effective_headless)
                pg = ctx.pages[0] if ctx.pages else ctx.new_page()
                return ctx, pg, None

            if prefer_persistent:
                default_profile_dir = (_default_output_dir(data_dir) / "profile").resolve()
                default_profile_dir.mkdir(parents=True, exist_ok=True)
                ctx = _launch_persistent_context(playwright, default_profile_dir, headless=effective_headless)
                pg = ctx.pages[0] if ctx.pages else ctx.new_page()
                result["session_mode"] = "user_data_dir"
                result["session_source"] = str(default_profile_dir)
                return ctx, pg, None

            br = _launch_browser(playwright, headless=effective_headless)
            ctx = br.new_context(
                storage_state=str(storage_state) if storage_state is not None else None,
                **_CONTEXT_KWARGS,
            )
            pg = ctx.new_page()
            return ctx, pg, br

        context, page, browser = _new_context_and_page(prefer_persistent=False)
        context.add_init_script(_STEALTH_SCRIPT)
        _wire_page_events(page)

        target_pages = [
            ("contract", CONTRACT_URL.format(contract_id=contract_id)),
            ("documents", DOCUMENTS_URL),
            ("movements", MOVEMENTS_URL),
        ]

        try:
            # Si aucune session n'est fournie/existante, on fait le login headless + captcha ici,
            # puis on persiste la session (storage_state) pour les runs suivants.
            if user_data_dir is None and storage_state is None and not default_session.exists():
                username, password = _load_credentials()
                anticaptcha_key = _load_anticaptcha_key()
                result["credentials"] = {
                    "username_env": "HIMALIA_LOGIN",
                    "password_env": "HIMALIA_PASSWORD",
                    "username_masked": _mask_secret(username),
                    "password_masked": _mask_secret(password, keep_start=0, keep_end=0),
                    "username_length": len(username),
                    "password_length": len(password),
                }
                # Crée la session (login + captcha) inline.
                # En cas de blocage, on retente avec un profil persistant puis, si besoin,
                # avec un Chrome visible mais toujours piloté automatiquement.
                login_attempts = [
                    {"prefer_persistent": False, "force_headless": headless, "label": "stateless"},
                    {"prefer_persistent": True, "force_headless": headless, "label": "persistent"},
                ]
                if headless:
                    login_attempts.append(
                        {"prefer_persistent": True, "force_headless": False, "label": "persistent_headed"}
                    )

                last_security_exc: HimaliaSecurityBlockError | None = None
                login_meta = None
                result["login_attempts"] = []

                for index, attempt in enumerate(login_attempts):
                    if index > 0:
                        try:
                            context.close()
                        except Exception:
                            pass
                        if browser is not None:
                            try:
                                browser.close()
                            except Exception:
                                pass
                        context, page, browser = _new_context_and_page(
                            prefer_persistent=attempt["prefer_persistent"],
                            force_headless=attempt["force_headless"],
                        )
                        context.add_init_script(_STEALTH_SCRIPT)
                        _wire_page_events(page)
                    try:
                        _record_login_diagnostic(
                            page,
                            context,
                            stage="before_login",
                            mode=str(attempt["label"]),
                        )
                        login_meta = _perform_auto_login(
                            page,
                            username,
                            password,
                            anticaptcha_key,
                            output_dir,
                            pending_otp_session_path=pending_otp_session,
                        )
                        _record_login_diagnostic(
                            page,
                            context,
                            stage="after_login",
                            mode=str(attempt["label"]),
                        )
                        result["login_attempt_mode"] = attempt["label"]
                        break
                    except HimaliaSecurityBlockError as exc:
                        _record_login_diagnostic(
                            page,
                            context,
                            stage="security_block",
                            mode=str(attempt["label"]),
                        )
                        last_security_exc = exc
                        result["login_attempts"].append(
                            {"mode": attempt["label"], "blocked": True, "message": str(exc)}
                        )
                        continue

                if login_meta is None:
                    result["login_security_block"] = True
                    result["login_security_block_message"] = (
                        str(last_security_exc) if last_security_exc is not None else "Blocage sécurité inconnu"
                    )
                    raise last_security_exc if last_security_exc is not None else HimaliaSecurityBlockError(
                        "La page de login Himalia a renvoyé une erreur de sécurité avant l'affichage du formulaire."
                    )
                result["login"] = {k: v for k, v in login_meta.items() if k not in {"password_masked", "username_masked"}}
                # Persist session.json
                default_session.parent.mkdir(parents=True, exist_ok=True)
                context.storage_state(path=str(default_session))
                result["session_persisted_to"] = str(default_session)
                if pending_otp_session.exists():
                    pending_otp_session.unlink()
            elif resuming_pending_otp:
                otp_meta = _resume_pending_otp(page, provided_otp_code, output_dir)
                result["otp_resume"] = otp_meta
                if _detect_otp_required(page):
                    context.storage_state(path=str(pending_otp_session))
                    raise HimaliaOtpRequiredError(
                        "Le code OTP soumis n'a pas permis de sortir de l'étape OTP. "
                        "Un nouveau code est peut-être requis."
                    )
                if TARGET_DOMAIN not in page.url or "EntAccLog" in page.url:
                    excerpt = " ".join((otp_meta.get("otp_after_excerpt") or otp_meta.get("resume_excerpt") or [])[:5])
                    raise HimaliaScraperError(
                        f"Connexion Himalia non confirmée après reprise OTP. URL courante : {page.url}. "
                        f"Extrait page : {excerpt[:300]}"
                    )
                default_session.parent.mkdir(parents=True, exist_ok=True)
                context.storage_state(path=str(default_session))
                result["session_persisted_to"] = str(default_session)
                if pending_otp_session.exists():
                    pending_otp_session.unlink()
            elif user_data_dir is None:
                # Session fournie: on masque quand même les creds dans le log (sans les lire)
                result["credentials"] = {
                    "username_env": "HIMALIA_LOGIN",
                    "password_env": "HIMALIA_PASSWORD",
                    "username_masked": "***",
                    "password_masked": "***",
                    "username_length": None,
                    "password_length": None,
                }

            for page_name, url in target_pages:
                page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                page.wait_for_timeout(3_000)

                if _detect_session_expired(page):
                    try:
                        pages_collected.append(
                            _snapshot_page(page, f"{page_name}_session_expired", output_dir)
                        )
                    except Exception:
                        pass
                    raise HimaliaSessionExpiredError(
                        f"Session Himalia expirée ou invalide lors de l'accès à '{page_name}'. "
                        f"URL courante : {page.url}. "
                        "Relancez `make himalia-scrape` (la session sera recréée si nécessaire)."
                    )

                pages_collected.append(_snapshot_page(page, page_name, output_dir))

            result["ok"] = True

        except HimaliaSessionExpiredError:
            result["ok"] = False
            result["error"] = {
                "type": "HimaliaSessionExpiredError",
                "message": "Session expirée — relancer himalia-setup-session",
            }
            if pending_otp_session.exists() and not resuming_pending_otp:
                pending_otp_session.unlink()
            raise

        except Exception as exc:
            result["ok"] = False
            result["error"] = {"type": type(exc).__name__, "message": str(exc)}
            try:
                pages_collected.append(_snapshot_page(page, "failure_state", output_dir))
            except Exception:
                pass
            raise

        finally:
            contract_page = next((row for row in pages_collected if row["name"] == "contract"), None)
            documents_page = next((row for row in pages_collected if row["name"] == "documents"), None)
            movements_page = next((row for row in pages_collected if row["name"] == "movements"), None)
            collector_payload = {
                "collected_at_utc": result["scraped_at_utc"],
                "contract": _build_himalia_contract_summary(contract_page or {}, contract_id),
                "visible_metrics": _extract_himalia_visible_metrics(contract_page or {}),
                "positions": _normalize_himalia_positions(contract_page or {}),
                "operations": _normalize_himalia_operations(movements_page or {}),
                "documents": _normalize_himalia_documents(documents_page or {}),
                "page_artifacts": {
                    "contract": contract_page.get("artifacts") if contract_page else None,
                    "documents": documents_page.get("artifacts") if documents_page else None,
                    "movements": movements_page.get("artifacts") if movements_page else None,
                },
                "api_sources": [],
            }
            collector_payload["summary"] = {
                "positions_count": len(collector_payload["positions"]),
                "operations_count": len(collector_payload["operations"]),
                "documents_count": len(collector_payload["documents"]),
                "api_payload_count": 0,
            }
            collector_path = output_dir / "himalia_collected.json"
            _write_json(collector_path, collector_payload)
            result["collector_path"] = str(collector_path)
            result["network"]["responses_sample"] = response_events[:300]
            result["network"]["console_events"] = console_events[:100]
            result["pages_logged"] = [p["name"] for p in pages_collected]
            result["page_summaries"] = {p["name"]: _page_summary(p) for p in pages_collected}
            context.close()
            if browser is not None:
                browser.close()
            _write_json(log_path, result)

    return {
        "ok": True,
        "contract_id": contract_id,
        "log_path": str(log_path),
        "collector_path": str(result.get("collector_path")),
        "artifacts_dir": str(output_dir),
        "pages_logged": result["pages_logged"],
        "responses_logged": len(response_events),
        "failed_requests": len(failed_requests),
        "session_mode": session_mode,
    }
