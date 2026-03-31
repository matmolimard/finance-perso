"""Scraper SwissLife pour tester la connexion et journaliser les pages contrat."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


LOGIN_URL = "https://myswisslife.fr/connection/login"
BASE_CONTRACT_URL = "https://myswisslife.fr/univers/patrimoine/contrat-patrimoine-financier/{contract_id}"
API_BASE_URL = "https://myswisslife.fr/api/v4/nest"


class SwissLifeScraperError(RuntimeError):
    """Erreur fonctionnelle du scraper SwissLife."""


@dataclass(frozen=True)
class SwissLifeCredentials:
    username: str
    password: str
    username_env: str
    password_env: str


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _default_output_dir(data_dir: Path | None) -> Path:
    if data_dir is not None:
        return data_dir / "logs" / "swisslife"
    return _project_root() / "portfolio_tracker" / "data" / "logs" / "swisslife"


def _mask_secret(value: str, *, keep_start: int = 2, keep_end: int = 2) -> str:
    if keep_start == 0 and keep_end == 0:
        return "*" * len(value)
    if len(value) <= keep_start + keep_end:
        return "*" * len(value)
    return f"{value[:keep_start]}{'*' * (len(value) - keep_start - keep_end)}{value[-keep_end:]}"


def _load_credentials() -> SwissLifeCredentials:
    env_path = _project_root() / ".env"
    load_dotenv(env_path, override=False)

    username_candidates = [
        "SWISSLIFE_LOGIN",
        "SWISSLIFE_USERNAME",
        "SWISSLIFE_USER",
        "MYSWISSLIFE_LOGIN",
        "MYSWISSLIFE_USERNAME",
    ]
    password_candidates = [
        "SWISSLIFE_PASSWORD",
        "SWISSLIFE_PASS",
        "MYSWISSLIFE_PASSWORD",
        "MYSWISSLIFE_PASS",
    ]

    username_env, username = next(
        ((name, os.getenv(name, "").strip()) for name in username_candidates if os.getenv(name, "").strip()),
        (None, ""),
    )
    password_env, password = next(
        ((name, os.getenv(name, "").strip()) for name in password_candidates if os.getenv(name, "").strip()),
        (None, ""),
    )

    if not username or not password:
        raise SwissLifeScraperError(
            "Identifiants SwissLife introuvables dans le .env. "
            f"Variables login acceptees: {', '.join(username_candidates)}. "
            f"Variables mot de passe acceptees: {', '.join(password_candidates)}."
        )

    return SwissLifeCredentials(
        username=username,
        password=password,
        username_env=str(username_env),
        password_env=str(password_env),
    )


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


def _snapshot_page(page: Any, name: str, output_dir: Path) -> dict[str, Any]:
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
            const buttons = Array.from(document.querySelectorAll("button, [role='button'], input[type='submit']"))
                .map((button) => normalize(button.textContent || button.value || button.getAttribute("aria-label")))
                .filter(Boolean)
                .slice(0, 50);
            return {
                title: document.title || "",
                bodyText,
                headings,
                tables,
                links,
                buttons,
            };
        }"""
    )

    body_text = extracted.get("bodyText", "")
    excerpt_lines = []
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


def _click_if_visible(page: Any, selectors: list[str], *, timeout_ms: int = 3_000) -> str | None:
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if locator.is_visible(timeout=timeout_ms):
                locator.click(timeout=timeout_ms)
                return selector
        except Exception:
            continue
    return None


def _parse_french_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = _sanitize_text(str(value))
    if not text:
        return None
    text = text.replace("€", "").replace("%", "").replace(" ", "").replace("\u202f", "")
    text = text.replace(",", ".")
    text = re.sub(r"[^0-9.\-]", "", text)
    if not text or text in {"-", ".", "-."}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _extract_date_from_text(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"\b(\d{2}/\d{2}/\d{4})\b", value)
    if not match:
        return None
    day, month, year = match.group(1).split("/")
    return f"{year}-{month}-{day}"


def _timestamp_ms_to_iso(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    if amount <= 0:
        return None
    return datetime.fromtimestamp(amount / 1000, tz=timezone.utc).date().isoformat()


def _sanitize_filename(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", value).strip("_") or "payload"


def _api_payload_filename(url: str) -> str:
    fragment = url.split("/api/v4/nest/", 1)[-1] if "/api/v4/nest/" in url else url.rsplit("/", 1)[-1]
    fragment = fragment.split("?", 1)[0]
    return _sanitize_filename(fragment) + ".json"


def _is_tracked_api_response(url: str, contract_id: str) -> bool:
    tracked = [
        f"{API_BASE_URL}/contrats/{contract_id}",
        f"{API_BASE_URL}/contrats/{contract_id}/repartition",
        f"{API_BASE_URL}/contrats/{contract_id}/operations/",
        f"{API_BASE_URL}/contrats?univers=UNIVERS_PATRIMOINE_FINANCIER&isLight=false",
        f"{API_BASE_URL}/contrats?univers=UNIVERS_PATRIMOINE_FINANCIER&isLight=true",
    ]
    return any(token in url for token in tracked)


def _collect_json_response(response: Any, contract_id: str, output_dir: Path, api_payloads: list[dict[str, Any]]) -> None:
    url = response.url
    if response.status >= 400 or not _is_tracked_api_response(url, contract_id):
        return
    content_type = (response.headers or {}).get("content-type", "")
    if "json" not in content_type.lower():
        return
    try:
        payload = response.json()
    except Exception:
        return

    api_dir = output_dir / "api"
    api_dir.mkdir(parents=True, exist_ok=True)
    file_path = api_dir / _api_payload_filename(url)
    _write_json(file_path, payload)
    api_payloads.append(
        {
            "url": url,
            "status": response.status,
            "content_type": content_type,
            "file": str(file_path),
            "payload": payload,
        }
    )


def _extract_json_api_payloads(api_payloads: list[dict[str, Any]], contract_id: str) -> dict[str, Any]:
    mapping: dict[str, Any] = {
        "contract": None,
        "repartition": None,
        "operations_pages": [],
        "contract_list_light": None,
        "contract_list_full": None,
    }
    for item in api_payloads:
        url = item["url"]
        payload = item["payload"]
        if f"/contrats/{contract_id}/repartition" in url:
            mapping["repartition"] = payload
        elif f"/contrats/{contract_id}/operations/" in url:
            mapping["operations_pages"].append({"url": url, "payload": payload})
        elif f"/contrats/{contract_id}" in url and "/repartition" not in url and "/operations/" not in url:
            mapping["contract"] = payload
        elif "contrats?univers=UNIVERS_PATRIMOINE_FINANCIER&isLight=true" in url:
            mapping["contract_list_light"] = payload
        elif "contrats?univers=UNIVERS_PATRIMOINE_FINANCIER&isLight=false" in url:
            mapping["contract_list_full"] = payload
    return mapping


def _find_contract_in_payload(payload: Any, contract_id: str) -> dict[str, Any] | None:
    if isinstance(payload, dict):
        if str(payload.get("id") or payload.get("contratId") or payload.get("numeroContratTechnique") or "") == contract_id:
            return payload
        for value in payload.values():
            found = _find_contract_in_payload(value, contract_id)
            if found is not None:
                return found
    if isinstance(payload, list):
        for item in payload:
            found = _find_contract_in_payload(item, contract_id)
            if found is not None:
                return found
    return None


def _iter_dicts(payload: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        rows.append(payload)
        for value in payload.values():
            rows.extend(_iter_dicts(value))
    elif isinstance(payload, list):
        for item in payload:
            rows.extend(_iter_dicts(item))
    return rows


def _pick_first(row: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        if key in row and row.get(key) not in (None, ""):
            return row.get(key)
    return None


def _normalize_positions_from_repartition(payload: Any) -> list[dict[str, Any]]:
    positions: list[dict[str, Any]] = []
    seen: set[tuple[str, float | None]] = set()
    for row in _iter_dicts(payload):
        label = _pick_first(row, ["libelle", "label", "nomSupport", "nom", "title"])
        amount = _parse_french_number(_pick_first(row, ["montant", "encours", "valorisation", "valeur", "montantBrut"]))
        weight = _parse_french_number(_pick_first(row, ["pourcentageEncours", "poids", "repartition", "pourcentage"]))
        performance = _parse_french_number(_pick_first(row, ["performance", "plusMoinsValuePct", "tauxPerformance"]))
        isin = _pick_first(row, ["isin", "codeIsin"])
        support_type = _pick_first(row, ["typeSupport", "categorieSupport", "familleSupport", "type"])
        if not label or amount is None:
            continue
        key = (str(label), amount)
        if key in seen:
            continue
        seen.add(key)
        positions.append(
            {
                "asset_name": str(label),
                "asset_isin": str(isin) if isin else None,
                "support_type": str(support_type) if support_type else None,
                "valuation": amount,
                "weight_pct": weight,
                "performance_pct": performance,
                "raw": row,
            }
        )
    positions.sort(key=lambda row: row["valuation"], reverse=True)
    return positions


def _normalize_operations_from_pages(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    seen: set[tuple[str | None, str | None, float | None]] = set()
    for page in pages:
        payload = page["payload"]
        rows = payload.get("operations") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            rows = []
        for row in rows:
            date_raw = _pick_first(row, ["effetDate", "dateOperation", "dateEffet", "date", "dateValeur"])
            label = _pick_first(row, ["natureLibelle", "libelleOperation", "libelle", "typeOperation", "natureOperation", "title"])
            status = _pick_first(row, ["etatLibelle", "statut", "status", "etat"])
            amount = _parse_french_number(_pick_first(row, ["montantBrut", "montantNet", "montant", "valeur"]))
            if not date_raw or not label or amount is None:
                continue
            date_iso = _timestamp_ms_to_iso(date_raw) or _extract_date_from_text(str(date_raw)) or str(date_raw)
            operation_items = row.get("operationItems") if isinstance(row.get("operationItems"), list) else []
            key = (date_iso, str(label), amount)
            if key in seen:
                continue
            seen.add(key)
            operations.append(
                {
                    "operation_id": _pick_first(row, ["operationId", "id"]),
                    "operation_date": date_iso,
                    "label": str(label),
                    "status": str(status) if status else None,
                    "gross_amount": amount,
                    "items_count": len(operation_items),
                    "items": [
                        {
                            "asset_name": _pick_first(item.get("uniteCompteItem") or {}, ["libelle"]),
                            "asset_isin": _pick_first(item.get("uniteCompteItem") or {}, ["isincode", "isin"]),
                            "support_type": _pick_first(item.get("uniteCompteItem") or {}, ["supportNatureLibelle", "natureCode"]),
                            "gross_amount": _parse_french_number(_pick_first(item, ["montantBrut"])),
                            "net_amount": _parse_french_number(_pick_first(item, ["montantNet"])),
                            "units": _parse_french_number(_pick_first(item, ["partQuantite"])),
                            "side": _pick_first(item, ["sensCode"]),
                            "nav": _parse_french_number(_pick_first(item.get("valeurLiquidativeItem") or {}, ["valeur"])),
                        }
                        for item in operation_items
                    ],
                    "raw": row,
                }
            )
    operations.sort(key=lambda row: (row["operation_date"] or "", row["label"]), reverse=True)
    return operations


def _build_contract_summary(contract_payload: Any, contract_id: str) -> dict[str, Any]:
    row = _find_contract_in_payload(contract_payload, contract_id) or {}
    encours = row.get("encours") if isinstance(row.get("encours"), dict) else {}
    detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
    return {
        "contract_id": contract_id,
        "contract_number": _pick_first(row, ["numContrat", "numeroContrat", "numeroContratTechnique", "numero"]),
        "contract_label": _pick_first(row, ["lblContrat", "libelle", "nom", "label"]),
        "effective_date": _timestamp_ms_to_iso(_pick_first(row, ["dateEffet", "dateAdhesion", "dateSouscription"]))
        or _extract_date_from_text(str(_pick_first(row, ["dateEffet", "dateAdhesion", "dateSouscription"]) or "")),
        "currency": _pick_first(row, ["devise", "currency"]) or "EUR",
        "official_total_valuation": _parse_french_number(_pick_first(encours, ["montant", "rachatValeurMontant", "epargneDisponibleMontantNet"]))
        or _parse_french_number(_pick_first(row, ["valorisation", "valeurTotale", "montantEpargne"])),
        "official_plus_minus_value": _parse_french_number(_pick_first(detail, ["plusMoinsValueMontant"]))
        or _parse_french_number(_pick_first(row, ["plusMoinsValue", "plusValue", "performanceValue"])),
        "official_performance_pct": _parse_french_number(_pick_first(encours, ["performance"]))
        or _parse_french_number(_pick_first(row, ["performance", "performancePct", "tauxPerformance"])),
        "valuation_date": _timestamp_ms_to_iso(_pick_first(encours, ["dateValorisation"])),
        "raw": row,
    }


def _extract_visible_metrics(page_payload: dict[str, Any]) -> dict[str, Any]:
    text = " ".join(page_payload.get("body_excerpt") or [])
    date_match = re.search(r"Montant de mon epargne au (\d{2}/\d{2}/\d{4})", text, flags=re.IGNORECASE)
    if not date_match:
        date_match = re.search(r"Montant de mon épargne au (\d{2}/\d{2}/\d{4})", text, flags=re.IGNORECASE)
    amounts = page_payload.get("value_candidates") or []
    return {
        "reference_date": _extract_date_from_text(date_match.group(1)) if date_match else None,
        "visible_total_valuation": _parse_french_number(amounts[0]) if len(amounts) > 0 else None,
        "visible_performance_pct": _parse_french_number(amounts[1]) if len(amounts) > 1 else None,
        "visible_plus_minus_value": _parse_french_number(amounts[2]) if len(amounts) > 2 else None,
    }


def _click_load_more_operations(page: Any) -> int:
    clicks = 0
    while clicks < 20:
        clicked = _click_if_visible(
            page,
            [
                "button:has-text(\"Afficher plus d'opérations\")",
                "[role='button']:has-text(\"Afficher plus d'opérations\")",
            ],
            timeout_ms=2_000,
        )
        if not clicked:
            break
        clicks += 1
        page.wait_for_timeout(2_500)
    return clicks


def _fill_first_visible(page: Any, selectors: list[str], value: str, *, timeout_ms: int = 5_000) -> dict[str, Any]:
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if locator.is_visible(timeout=timeout_ms):
                locator.fill(value, timeout=timeout_ms)
                filled_value = locator.input_value(timeout=timeout_ms)
                return {
                    "selector": selector,
                    "maxlength": locator.get_attribute("maxlength"),
                    "placeholder": locator.get_attribute("placeholder"),
                    "type": locator.get_attribute("type"),
                    "inputmode": locator.get_attribute("inputmode"),
                    "provided_length": len(value),
                    "filled_length": len(filled_value),
                    "truncated": filled_value != value,
                }
        except Exception:
            continue
    raise SwissLifeScraperError(f"Champ introuvable pour selecteurs: {selectors}")


def _perform_login(page: Any, credentials: SwissLifeCredentials, output_dir: Path) -> dict[str, Any]:
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(2_000)

    cookie_selector = _click_if_visible(
        page,
        [
            "button:has-text('Accepter')",
            "button:has-text('Tout accepter')",
            "button:has-text('Autoriser')",
            "[role='button']:has-text('Accepter')",
        ],
    )

    before_login = _snapshot_page(page, "login_before_submit", output_dir)

    username_field = _fill_first_visible(
        page,
        [
            "input[autocomplete='username']",
            "input[name*='ident' i]",
            "input[id*='ident' i]",
            "input[name*='user' i]",
            "input[id*='user' i]",
            "input[type='email']",
            "input[type='text']",
        ],
        credentials.username,
    )
    password_field = _fill_first_visible(
        page,
        [
            "input[autocomplete='current-password']",
            "input[name*='pass' i]",
            "input[id*='pass' i]",
            "input[type='password']",
        ],
        credentials.password,
    )

    if username_field.get("truncated"):
        raise SwissLifeScraperError(
            "L'identifiant SwissLife saisi a ete tronque par le formulaire avant envoi. "
            f"Longueur fournie: {username_field.get('provided_length')}, "
            f"longueur acceptee par la page: {username_field.get('filled_length')}, "
            f"maxlength HTML: {username_field.get('maxlength')}."
        )
    if password_field.get("truncated"):
        raise SwissLifeScraperError(
            "Le mot de passe SwissLife saisi a ete tronque par le formulaire avant envoi. "
            f"Longueur fournie: {password_field.get('provided_length')}, "
            f"longueur acceptee par la page: {password_field.get('filled_length')}, "
            f"maxlength HTML: {password_field.get('maxlength')}."
        )

    submit_selector = _click_if_visible(
        page,
        [
            "button[type='submit']",
            "input[type='submit']",
            "button:has-text('Se connecter')",
            "button:has-text('Connexion')",
            "button:has-text('Me connecter')",
            "[role='button']:has-text('Se connecter')",
        ],
        timeout_ms=5_000,
    )
    if submit_selector is None:
        raise SwissLifeScraperError("Bouton de connexion introuvable sur la page SwissLife.")

    page.wait_for_load_state("domcontentloaded", timeout=60_000)
    page.wait_for_timeout(5_000)

    login_succeeded = LOGIN_URL not in page.url and "connection/login" not in page.url
    after_login = _snapshot_page(page, "login_after_submit", output_dir)

    if not login_succeeded:
        error_text = page.locator("body").inner_text(timeout=5_000)
        raise SwissLifeScraperError(
            "Connexion SwissLife non confirmee. "
            f"URL courante: {page.url}. "
            f"Extrait: {_sanitize_text(error_text)[:500]}"
        )

    return {
        "login_url": LOGIN_URL,
        "final_url": page.url,
        "cookie_selector_clicked": cookie_selector,
        "username_field": username_field,
        "password_field": password_field,
        "submit_selector": submit_selector,
        "before_login": before_login,
        "after_login": after_login,
    }


def scrape_swisslife_contract(
    *,
    contract_id: str,
    data_dir: Path | None = None,
    output_path: Path | None = None,
    headless: bool = True,
) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise SwissLifeScraperError(
            "Playwright n'est pas installe. Installe-le avec `pip install playwright` puis "
            "`python -m playwright install chromium`."
        ) from exc

    credentials = _load_credentials()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = (output_path.parent if output_path else _default_output_dir(data_dir) / timestamp).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    log_path = output_path.resolve() if output_path else output_dir / "swisslife_scrape_log.json"

    response_events: list[dict[str, Any]] = []
    failed_requests: list[dict[str, Any]] = []
    console_events: list[dict[str, Any]] = []
    pages: list[dict[str, Any]] = []
    api_payloads: list[dict[str, Any]] = []

    result: dict[str, Any] = {
        "scraped_at_utc": datetime.now(timezone.utc).isoformat(),
        "contract_id": contract_id,
        "headless": headless,
        "credentials": {
            "username_env": credentials.username_env,
            "password_env": credentials.password_env,
            "username_masked": _mask_secret(credentials.username),
            "password_masked": _mask_secret(credentials.password, keep_start=0, keep_end=0),
            "username_length": len(credentials.username),
            "password_length": len(credentials.password),
        },
        "pages": pages,
        "api_payloads": [],
        "network": {
            "responses_sample": response_events,
            "failed_requests": failed_requests,
            "console_events": console_events,
        },
        "artifacts_dir": str(output_dir),
    }

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        context = browser.new_context(
            viewport={"width": 1440, "height": 2200},
            locale="fr-FR",
            timezone_id="Europe/Paris",
        )
        page = context.new_page()

        page.on(
            "console",
            lambda message: console_events.append(
                {
                    "type": message.type,
                    "text": message.text,
                }
            ),
        )
        page.on(
            "requestfailed",
            lambda request: failed_requests.append(
                {
                    "url": request.url,
                    "method": request.method,
                    "failure": str(request.failure),
                }
            ),
        )
        page.on(
            "response",
            lambda response: (
                response_events.append(
                    {
                        "url": response.url,
                        "status": response.status,
                        "ok": response.ok,
                    }
                ),
                _collect_json_response(response, contract_id, output_dir, api_payloads),
            ),
        )

        try:
            login_result = _perform_login(page, credentials, output_dir)
            result["login"] = login_result

            for page_name, suffix in [
                ("repartition", "repartition"),
                ("operations", "operation"),
            ]:
                target_url = f"{BASE_CONTRACT_URL.format(contract_id=contract_id)}/{suffix}"
                page.goto(target_url, wait_until="domcontentloaded", timeout=60_000)
                page.wait_for_timeout(5_000)
                if page_name == "operations":
                    result["operations_load_more_clicks"] = _click_load_more_operations(page)
                    page.wait_for_timeout(2_500)
                pages.append(_snapshot_page(page, page_name, output_dir))
        except Exception as exc:
            result["ok"] = False
            result["error"] = {
                "type": type(exc).__name__,
                "message": str(exc),
            }
            try:
                result["failure_snapshot"] = _snapshot_page(page, "failure_state", output_dir)
            except Exception:
                result["failure_snapshot"] = None
            raise
        finally:
            api_index = [
                {
                    "url": item["url"],
                    "status": item["status"],
                    "content_type": item["content_type"],
                    "file": item["file"],
                }
                for item in api_payloads
            ]
            result["api_payloads"] = api_index
            extracted = _extract_json_api_payloads(api_payloads, contract_id)
            repartition_page = next((row for row in pages if row["name"] == "repartition"), None)
            operations_page = next((row for row in pages if row["name"] == "operations"), None)
            collector_payload = {
                "collected_at_utc": result["scraped_at_utc"],
                "contract": _build_contract_summary(extracted.get("contract"), contract_id),
                "visible_metrics": _extract_visible_metrics(repartition_page or {}),
                "positions": _normalize_positions_from_repartition(extracted.get("repartition")),
                "operations": _normalize_operations_from_pages(extracted.get("operations_pages") or []),
                "page_artifacts": {
                    "repartition": repartition_page.get("artifacts") if repartition_page else None,
                    "operations": operations_page.get("artifacts") if operations_page else None,
                },
                "api_sources": api_index,
            }
            collector_payload["summary"] = {
                "positions_count": len(collector_payload["positions"]),
                "operations_count": len(collector_payload["operations"]),
                "operations_load_more_clicks": result.get("operations_load_more_clicks", 0),
                "api_payload_count": len(api_index),
            }
            collector_path = output_dir / "swisslife_collected.json"
            _write_json(collector_path, collector_payload)
            result["collector_path"] = str(collector_path)
            result["network"]["responses_sample"] = response_events[:300]
            result["network"]["console_events"] = console_events[:100]
            browser.close()
            _write_text(log_path, json.dumps(result, ensure_ascii=False, indent=2))

    return {
        "ok": True,
        "contract_id": contract_id,
        "log_path": str(log_path),
        "collector_path": str(result["collector_path"]),
        "artifacts_dir": str(output_dir),
        "pages_logged": [page_info["name"] for page_info in pages],
        "login_final_url": result["login"]["final_url"],
        "api_payloads_logged": len(api_payloads),
        "operations_load_more_clicks": result.get("operations_load_more_clicks", 0),
        "responses_logged": len(response_events),
        "failed_requests": len(failed_requests),
    }
