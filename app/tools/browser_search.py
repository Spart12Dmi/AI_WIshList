import logging
import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlsplit

from bs4 import BeautifulSoup
from langchain_core.tools import tool
from playwright.sync_api import Browser, Page, Route, sync_playwright

from app.config import get_settings
from app.query_expansion import matches_query
from app.regions import REGIONS
from app.tools.web_search import (
    NON_PRODUCT_PAGE_PATTERNS,
    _meta_content,
    _normalise_currency,
    _shop_name,
    is_public_http_url,
    parse_catalog_links,
    parse_price,
    parse_product_offers,
)

SEARCH_PAGE_URLS = (
    "https://html.duckduckgo.com/html/?q={query}",
    "https://www.bing.com/search?count=20&q={query}",
    "https://www.google.com/search?num=20&q={query}",
)
SEARCH_PAGE_HOSTS = ("duckduckgo.com", "bing.com", "google.com")
log = logging.getLogger(__name__)


def _allow_only_public_requests(route: Route) -> None:
    """Keep automated Chromium requests out of local/private network addresses."""
    url = route.request.url
    if url.startswith(("data:", "blob:")) or is_public_http_url(url):
        route.continue_()
    else:
        route.abort()


def _unwrap_search_link(href: str, page_url: str) -> str | None:
    """Turn a search-engine redirect into a public result URL."""
    absolute = urljoin(page_url, href.strip())
    parsed = urlsplit(absolute)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if not host or any(host == engine or host.endswith("." + engine) for engine in SEARCH_PAGE_HOSTS):
        params = parse_qs(parsed.query)
        for key in ("uddg", "q", "url", "u"):
            values = params.get(key)
            if values:
                absolute = unquote(values[0])
                break
        else:
            return None
    return absolute if is_public_http_url(absolute) else None


def search_browser(query: str, max_results: int, region: str = "wt-wt") -> list[dict[str, str]]:
    """Last-resort discovery through a real browser search page.

    This is deliberately provider- and category-generic.  It runs only after
    DDGS engines returned no usable rows and still returns links that pass the
    same public-URL checks as ordinary discovery.
    """
    settings = get_settings()
    if not settings.use_browser_fallback or not query.strip():
        return []
    limit = max(1, min(max_results, 50))
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=settings.browser_headless)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1100}, service_workers="block")
            page.route("**/*", _allow_only_public_requests)
            for template in SEARCH_PAGE_URLS:
                try:
                    page.goto(
                        template.format(query=quote_plus(query)),
                        wait_until="domcontentloaded",
                        timeout=settings.browser_navigation_timeout_ms,
                    )
                    page.wait_for_timeout(min(settings.browser_render_wait_ms, 800))
                    for anchor in page.locator("a[href]").all():
                        href = anchor.get_attribute("href")
                        if not href:
                            continue
                        target = _unwrap_search_link(href, page.url)
                        if not target or target in seen:
                            continue
                        title = " ".join((anchor.inner_text() or "").split())
                        if len(title) < 2:
                            continue
                        seen.add(target)
                        results.append({"url": target, "title": title[:500], "snippet": "", "providers": "chromium"})
                        if len(results) >= limit:
                            return results
                except Exception as error:
                    log.info("Browser search page failed: %s", type(error).__name__)
                    continue
            return results
        finally:
            browser.close()


def parse_rendered_product_page(
    html: str, page_url: str, discovered_title: str = "", query: str = "", queries=(), region: str = "wt-wt"
) -> dict[str, Any]:
    """Read a visible price from a rendered product page when JSON-LD is absent.

    Modern storefronts often inject the price into the DOM after JavaScript
    runs and expose no Schema.org offer.  This fallback is intentionally
    conservative: it requires a product-like heading, an explicit currency,
    and a current-price selector (never arbitrary page text).
    """
    soup = BeautifulSoup(html, "lxml")
    heading = soup.find("h1")
    title = next(
        (
            value.strip()
            for value in (
                heading.get_text(" ", strip=True) if heading else "",
                _meta_content(soup, "og:title", "twitter:title") or "",
                discovered_title,
                soup.title.get_text(" ", strip=True) if soup.title else "",
            )
            if value and value.strip()
        ),
        "",
    )
    if not title or NON_PRODUCT_PAGE_PATTERNS.search(title):
        return {"accepted": False, "reason": "The rendered page did not expose a product heading."}
    if query and not matches_query(query, title, queries):
        return {"accepted": False, "reason": "The rendered product heading did not match the query."}

    expected = next(
        (set(profile.preferred_currencies) for profile in REGIONS.values() if profile.search_region == region),
        set(),
    )
    currency_markers = {
        "CZK": re.compile(r"\bCZK\b|K(?:č|c)", re.I),
        "EUR": re.compile(r"\bEUR\b|€", re.I),
        "PLN": re.compile(r"\bPLN\b|zł", re.I),
        "GBP": re.compile(r"\bGBP\b|£", re.I),
        "USD": re.compile(r"\bUSD\b|\$", re.I),
    }
    # Keep symbols as escaped Unicode so this source remains correct on
    # Windows checkouts that use a legacy code page (not mojibake bytes).
    currency_markers.update({
        "CZK": re.compile(r"\bCZK\b|K(?:\u010d|c)", re.I),
        "EUR": re.compile(r"\bEUR\b|\u20ac", re.I),
        "PLN": re.compile(r"\bPLN\b|z\u0142", re.I),
        "GBP": re.compile(r"\bGBP\b|\u00a3", re.I),
    })
    selectors = (
        '[itemprop="price"]', '[data-price]', '[data-product-price]',
        ".price-current", ".current-price", ".product-price", ".price",
        '[class*="price"]', '[id*="price"]',
    )
    price_node = None
    for node in soup.select(", ".join(selectors)):
        classes = " ".join(node.get("class", [])) + " " + str(node.get("id", ""))
        if re.search(r"(?:old|was|before|shipping|delivery|installment|monthly|unit)[-_ ]?price", classes, re.I):
            continue
        text = " ".join(node.get_text(" ", strip=True).split())
        if not text:
            continue
        price = parse_price(node.get("content") or node.get("data-price") or text)
        if price is None:
            continue
        currency = _normalise_currency(node.get("data-currency") or node.get("content-currency"))
        if not currency:
            for code, marker in currency_markers.items():
                if marker.search(text):
                    currency = code
                    break
        if not currency:
            currency = _normalise_currency(
                _meta_content(soup, "product:price:currency", "og:price:currency")
            )
        if not currency or (expected and currency not in expected):
            continue
        price_node = (price, currency)
        break
    if not price_node:
        return {"accepted": False, "reason": "The rendered page did not expose a current price and currency."}
    price, currency = price_node
    image_url = _meta_content(soup, "og:image", "twitter:image", "twitter:image:src")
    if image_url:
        image_url = urljoin(page_url, image_url)
        if not is_public_http_url(image_url):
            image_url = None
    return {
        "accepted": True,
        "title": title,
        "price": price,
        "currency": currency,
        "shop": _shop_name(page_url),
        "url": page_url,
        "source_url": page_url,
        "image_url": image_url,
        "availability": None,
    }


def _load_product_page(
    browser: Browser, url: str, discovered_title: str, query: str = "", queries=(), region: str = "wt-wt"
) -> dict[str, Any]:
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
        if not offers:
            rendered = parse_rendered_product_page(html, final_url, discovered_title, query, queries, region)
            if rendered.get("accepted"):
                offers.append(rendered)
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
                        candidate_html = page.content()
                        candidate_offers = parse_product_offers(candidate_html, page.url, candidate["title"])
                        if not candidate_offers:
                            rendered = parse_rendered_product_page(
                                candidate_html, page.url, candidate["title"], query, queries, region
                            )
                            if rendered.get("accepted"):
                                candidate_offers.append(rendered)
                        offers.extend(candidate_offers)
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
                        browser,
                        candidate["url"],
                        candidate.get("title", ""),
                        candidate.get("query", ""),
                        candidate.get("queries", ()),
                        candidate.get("region", "wt-wt"),
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
