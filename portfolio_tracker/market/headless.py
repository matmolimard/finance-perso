"""
Headless browser utilities (optional).

This module is intentionally optional: it only works if Playwright is installed.
It can be used as a fallback when market data pages require JavaScript rendering.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class HeadlessFetchOptions:
    """
    Options for headless navigation.
    """

    wait_until: str = "networkidle"  # 'load', 'domcontentloaded', 'networkidle'
    timeout_ms: int = 30_000
    user_agent: str = "portfolio-tracker/1.0 (personal project)"


def headless_get_text(url: str, *, options: Optional[HeadlessFetchOptions] = None) -> str:
    """
    Fetch a URL using a headless Chromium browser and return the rendered page HTML.

    Requires:
      pip install playwright
      python -m playwright install chromium
    """
    options = options or HeadlessFetchOptions()

    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception as e:  # pragma: no cover
        raise RuntimeError(
            "Playwright n'est pas installé. Installe-le avec:\n"
            "  pip install playwright\n"
            "  python -m playwright install chromium\n"
        ) from e

    with sync_playwright() as p:  # pragma: no cover
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(user_agent=options.user_agent)
            page = context.new_page()
            page.goto(url, wait_until=options.wait_until, timeout=options.timeout_ms)
            return page.content()
        finally:
            browser.close()


def headless_get_response_text(url: str, *, options: Optional[HeadlessFetchOptions] = None) -> str:
    """
    Fetch a URL using a headless Chromium browser and return the raw response body text.

    Use this for endpoints that return JSON/text (not HTML).
    """
    options = options or HeadlessFetchOptions(wait_until="load")

    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception as e:  # pragma: no cover
        raise RuntimeError(
            "Playwright n'est pas installé. Installe-le avec:\n"
            "  pip install playwright\n"
            "  python -m playwright install chromium\n"
        ) from e

    with sync_playwright() as p:  # pragma: no cover
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(user_agent=options.user_agent)
            page = context.new_page()
            resp = page.goto(url, wait_until=options.wait_until, timeout=options.timeout_ms)
            if resp is None:
                raise RuntimeError("Aucune réponse HTTP (navigation interrompue?)")
            return resp.text()
        finally:
            browser.close()


