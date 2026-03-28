"""Utilitaires headless V2, sans dépendance au package marché historique."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HeadlessFetchOptions:
    wait_until: str = "networkidle"
    timeout_ms: int = 30_000
    user_agent: str = "portfolio-tracker/1.0 (personal project)"


def headless_get_text(url: str, *, options: HeadlessFetchOptions | None = None) -> str:
    options = options or HeadlessFetchOptions()
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "Playwright n'est pas installe. Installe-le avec:\n"
            "  pip install playwright\n"
            "  python -m playwright install chromium\n"
        ) from exc

    with sync_playwright() as playwright:  # pragma: no cover
        browser = playwright.chromium.launch(headless=True)
        try:
            context = browser.new_context(user_agent=options.user_agent)
            page = context.new_page()
            page.goto(url, wait_until=options.wait_until, timeout=options.timeout_ms)
            return page.content()
        finally:
            browser.close()


def headless_get_response_text(url: str, *, options: HeadlessFetchOptions | None = None) -> str:
    options = options or HeadlessFetchOptions(wait_until="load")
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "Playwright n'est pas installe. Installe-le avec:\n"
            "  pip install playwright\n"
            "  python -m playwright install chromium\n"
        ) from exc

    with sync_playwright() as playwright:  # pragma: no cover
        browser = playwright.chromium.launch(headless=True)
        try:
            context = browser.new_context(user_agent=options.user_agent)
            page = context.new_page()
            response = page.goto(url, wait_until=options.wait_until, timeout=options.timeout_ms)
            if response is None:
                raise RuntimeError("Aucune reponse HTTP")
            return response.text()
        finally:
            browser.close()
