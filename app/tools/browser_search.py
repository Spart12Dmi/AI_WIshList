from collections.abc import Iterator
from typing import Any

from langchain_core.tools import tool
from playwright.sync_api import Browser, Page, Route, sync_playwright

from app.config import get_settings
from app.query_expansion import matches_query
from app.tools.web_search import is_public_http_url, parse_catalog_links, parse_product_offers


def _allow_only_public_requests(route: Route) -> None:
    """Keep automated Chromium requests out of local/private network addresses."""
    url = route.request.url
    if url.startswith(("data:", "blob:")) or is_public_http_url(url):
        route.continue_()
    else:
        route.abort()


def _load_product_page(browser: Browser, url: str, discovered_title: str, query: str = "", queries=()) -> dict[str, Any]:
    settings = get_settings()
    if not is_public_http_url(url):
        return {"accepted": False, "reason": "The candidate URL is not public."}
    page: Page = browser.new_page(viewport={"width": 1440, "height": 1100}, service_workers="block")
    page.route("**/*", _allow_only_public_requests)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=settings.browser_navigation_timeout_ms)
        page.wait_for_timeout(settings.browser_render_wait_ms)
        final_url = page.url
        if not is_public_http_url(final_url):
            return {"accepted": False, "reason": "The page redirected to a non-public address."}
        html = page.content()
        offers = parse_product_offers(html, final_url, discovered_title)
        if not offers or (query and not any(matches_query(query, item["title"], queries) for item in offers)):
            for candidate in parse_catalog_links(html, final_url, query, queries):
                try:
                    page.goto(
                        candidate["url"],
                        wait_until="domcontentloaded",
                        timeout=settings.browser_navigation_timeout_ms,
                    )
                    page.wait_for_timeout(settings.browser_render_wait_ms)
                    if is_public_http_url(page.url):
                        offers.extend(parse_product_offers(page.content(), page.url, candidate["title"]))
                except Exception:
                    continue
        return {"accepted": bool(offers), "offers": offers}
    finally:
        # Drain intercepted requests before closing the page/context.
        page.unroute_all(behavior="wait")
        page.close()


def iter_browser_product_extractions(candidates: list[dict[str, str]]) -> Iterator[dict[str, Any]]:
    """Yield one extraction result at a time while reusing a single Chromium process."""
    settings = get_settings()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=settings.browser_headless)
        try:
            for candidate in candidates[: settings.browser_candidate_limit]:
                try:
                    result = _load_product_page(
                        browser, candidate["url"], candidate.get("title", ""), candidate.get("query", ""), candidate.get("queries", ())
                    )
                    if result.get("offers"):
                        yield from result["offers"]
                    else:
                        yield result
                except Exception:
                    yield {"accepted": False, "reason": "Chromium could not extract this product page."}
        finally:
            browser.close()


@tool("extract_products_in_browser")
def extract_products_in_browser(candidates: list[dict[str, str]]) -> dict[str, Any]:
    """Open failed public product candidates in Chromium, wait for JavaScript, then extract product offers."""
    results = list(iter_browser_product_extractions(candidates))
    products = [result for result in results if result.get("accepted")]
    rejected = len(results) - len(products)
    return {"products": products, "rejected": rejected}
