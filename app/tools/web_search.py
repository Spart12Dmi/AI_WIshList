import ipaddress
import json
import logging
import re
import socket
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup
from ddgs import DDGS
from langchain_core.tools import tool

from app.config import get_settings
from app.matching import SHOPPING_WORDS, words
from app.network import tls_context
from app.query_expansion import matches_query
from app.regions import REGIONS
from app.urls import canonical_offer_url

USER_AGENT = "LocalProductFinder/0.1 (+local research tool)"
_search_slots = threading.BoundedSemaphore(4)
log = logging.getLogger(__name__)
_search_cache: OrderedDict = OrderedDict()
_search_cache_lock = threading.Lock()
NON_PRODUCT_PAGE_PATTERNS = re.compile(
    r"\b(price guide|pricing guide|complete price|review|blog|article|news|recipe|history|top \d+|best \d+)\b",
    re.IGNORECASE,
)
# Classified listings often publish a nominal ``1`` amount while asking
# buyers to negotiate.  Such a value is not a comparable current offer.
# Keep this language-agnostic and require the explicit price label plus a
# negotiation phrase; ordinary low-cost products remain valid.
NON_ACTIONABLE_PRICE_PATTERNS = re.compile(
    r"\b(?:price|cena|preis|prix|precio|pre[cç]o|цена)\b.{0,40}"
    r"\b(?:negotiable|on request|dohodou|nab[ií]dn[eě]te|make an offer|contact)\b",
    re.IGNORECASE | re.DOTALL,
)
# Some DDGS providers intermittently return an empty list (or are unavailable
# in a particular network).  Keep a small provider-independent fallback set so
# one outage cannot turn an otherwise valid product query into a zero-result
# search.  These are engines, not product/category rules.
DISCOVERY_FALLBACK_BACKENDS = ("duckduckgo", "google", "mojeek")


def is_public_http_url(url: str) -> bool:
    """Allow only ordinary public HTTP(S) URLs before making server-side requests."""
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        if parsed.username or parsed.password:
            return False
        if parsed.port not in {None, 80, 443}:
            return False
        host = parsed.hostname.lower().rstrip(".")
        if host in {"localhost", "localhost.localdomain"}:
            return False
        addresses = {item[4][0] for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)}
        return bool(addresses) and all(ipaddress.ip_address(address).is_global for address in addresses)
    except (OSError, ValueError):
        return False


def _clean_result_url(url: str) -> str:
    return canonical_offer_url(url)


def _run_web_search(query: str, max_results: int, region: str) -> list[dict[str, str]]:
    """Run and clean one public-web search request."""
    result_limit = max(1, min(max_results, 50))
    settings = get_settings()
    key = (query.strip().casefold(), result_limit, region, settings.search_backends)
    ttl = settings.search_cache_ttl_seconds
    with _search_cache_lock:
        cached = _search_cache.get(key)
        if ttl and cached and time.monotonic() - cached[0] < ttl:
            _search_cache.move_to_end(key)
            return [dict(item) for item in cached[1]]
    providers = list(
        dict.fromkeys(name.strip() for name in settings.search_backends.split(",") if name.strip())
    )
    if not providers or any(name in {"auto", "all"} for name in providers):
        raise ValueError("SEARCH_BACKENDS must list explicit providers, not auto/all")

    def fetch(provider):
        try:
            with _search_slots:
                return DDGS(timeout=8).text(query, max_results=result_limit, region=region, backend=provider)
        except Exception as exc:
            log.info("Search provider %s failed: %s", provider, type(exc).__name__)
            return []

    # DDGS' combined call can exit its collection loop before all pending engine
    # results are consumed. Query engines individually and collect EVERY future.
    # Merge/rank before the result limit so foreign hits cannot crowd out locals.
    with ThreadPoolExecutor(max_workers=min(4, len(providers))) as pool:
        batches = dict(zip(providers, pool.map(fetch, providers), strict=True))
    cleaned = rank_provider_results(batches, query, region)[:result_limit]
    # Provider failures are common on desktop networks (certificate stores,
    # rate limits, DNS and regional blocks).  Retry with a separate generic
    # backend set only after the configured set produced no usable rows.  The
    # same URL cleaning and regional ranking still apply, so this cannot bypass
    # public-URL or relevance safeguards.
    if not cleaned:
        fallback = [name for name in DISCOVERY_FALLBACK_BACKENDS if name not in providers]
        if fallback:
            log.info("Configured search providers returned no usable rows; trying generic fallback backends")
            with ThreadPoolExecutor(max_workers=min(3, len(fallback))) as pool:
                fallback_batches = dict(zip(fallback, pool.map(fetch, fallback), strict=True))
            cleaned = rank_provider_results(fallback_batches, query, region)[:result_limit]
    # DDGS can fail before the browser's certificate/network stack is touched.
    # Use a real search page as the final discovery fallback for unscoped
    # requests; scoped ``site:`` retries remain cheap and are covered by the
    # generic market-hint query in StoreDiscoveryAgent.
    if not cleaned and settings.use_browser_fallback and not re.search(r"\bsite:\S+", query, re.I):
        try:
            from app.tools.browser_search import search_browser

            browser_rows = search_browser(query, result_limit, region)
            cleaned = rank_provider_results({"chromium": browser_rows}, query, region)[:result_limit]
        except Exception as exc:
            log.info("Browser discovery fallback failed: %s", type(exc).__name__)
    if not cleaned:
        raise RuntimeError("No usable results from configured or fallback search providers")
    # Stabilize discovery URLs, NEVER prices or extracted offers. Every product
    # page is fetched and validated again. Failed/empty searches are not cached.
    if ttl and cleaned:
        with _search_cache_lock:
            _search_cache[key] = (time.monotonic(), [dict(item) for item in cleaned])
            _search_cache.move_to_end(key)
            while len(_search_cache) > 128:
                _search_cache.popitem(last=False)
    return cleaned


def rank_provider_results(batches, query, region):
    """Stable regional ranking and reciprocal-rank fusion across provider lists."""
    suffixes = next(
        (profile.country_domains for profile in REGIONS.values() if profile.search_region == region), ()
    )
    anchors = words(re.sub(r"\bsite:\S+", "", query)) - SHOPPING_WORDS
    merged, scores, sources = {}, {}, {}
    for provider, results in batches.items():
        seen = set()
        for rank, result in enumerate(results):
            url = _clean_result_url(str(result.get("href") or result.get("url") or ""))
            if url in seen or not is_public_http_url(url):
                continue
            seen.add(url)
            merged.setdefault(
                url,
                {
                    "title": str(result.get("title") or ""),
                    "url": url,
                    "snippet": str(result.get("body") or result.get("snippet") or ""),
                },
            )
            scores[url] = scores.get(url, 0) + 1 / (60 + rank + 1)
            sources.setdefault(url, []).append(provider)

    def priority(item):
        host = (urlsplit(item["url"]).hostname or "").lower()
        native = any(host.endswith(suffix) for suffix in suffixes)
        overlap = len(anchors & words(item["title"]))
        return (-native, -overlap, -scores[item["url"]], item["url"])

    return [
        {**item, "providers": ",".join(sources[item["url"]])}
        for item in sorted(merged.values(), key=priority)
    ]


@tool("search_web")
def search_web(query: str, max_results: int = 12, region: str = "wt-wt") -> list[dict[str, str]]:
    """Search the public web for relevant stores in a selected region. Returns title, URL, and result snippet."""
    return _run_web_search(query, max_results, region)


@tool("search_store_catalog")
def search_store_catalog(
    domain: str, query: str, region: str = "wt-wt", max_results: int = 3,
    queries: list[str] | None = None,
) -> list[dict[str, str]]:
    """Find individual product pages for a query inside one store domain using a site-restricted web search."""
    clean_domain = domain.lower().strip().removeprefix("www.")
    if not re.fullmatch(r"(?:[a-z0-9-]+\.)+[a-z]{2,63}", clean_domain):
        raise ValueError("A valid public store domain is required.")
    results, seen = [], set()
    failures = []
    for phrase in list(dict.fromkeys([query, *(queries or [])]))[:4]:
        try:
            found = _run_web_search(f"site:{clean_domain} {phrase}", max_results, region)
        except Exception as error:
            failures.append(error)
            continue
        for result in found:
            host = (urlsplit(result["url"]).hostname or "").lower()
            if host != clean_domain and not host.endswith("." + clean_domain):
                continue
            if not matches_query(query, result.get("title", "") + " " + result.get("snippet", ""), queries):
                continue
            if result["url"] not in seen:
                seen.add(result["url"])
                results.append({**result, "store_domain": clean_domain})
        if len(results) >= max_results:
            break
    if not results and failures:
        log.info("Store query retries encountered %s provider failures", len(failures))
    return results[:max_results]


def _meta_content(soup: BeautifulSoup, *names: str) -> str | None:
    wanted = {name.lower() for name in names}
    for tag in soup.find_all("meta"):
        key = str(tag.get("property") or tag.get("name") or "").lower()
        value = tag.get("content")
        if key in wanted and value:
            return str(value).strip()
    return None


def _walk_json(value: Any):
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _walk_json(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_json(nested)


def _is_product(value: dict[str, Any]) -> bool:
    value_type = value.get("@type", "")
    types = value_type if isinstance(value_type, list) else [value_type]
    return any(str(item).lower().endswith("product") for item in types)


def _first_string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, list):
        for item in value:
            found = _first_string(item)
            if found:
                return found
    if isinstance(value, dict):
        for key in ("url", "contentUrl", "@id"):
            found = _first_string(value.get(key))
            if found:
                return found
    return None


def _normalise_currency(value: Any) -> str | None:
    if not value:
        return None
    currency = str(value).upper().strip()
    return currency if re.fullmatch(r"[A-Z]{3}", currency) else None


def parse_price(value: Any) -> float | None:
    """Parse common machine-readable and visible price formats without guessing a currency."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if float(value) >= 0 else None
    raw_text = str(value).replace("\u00a0", " ").replace("\u202f", " ")
    if re.search(r"-\s*\d", raw_text):
        return None
    price_tokens = re.findall(r"(?<!\d)(?:\d{1,3}(?:[.,\s]\d{3})+|\d+)(?:[.,]\d{1,2})?(?!\d)", raw_text)
    if not price_tokens:
        return None
    text = price_tokens[0].replace(" ", "")
    if "," in text and "." in text:
        decimal = "," if text.rfind(",") > text.rfind(".") else "."
        thousands = "." if decimal == "," else ","
        text = text.replace(thousands, "").replace(decimal, ".")
    elif "," in text:
        before, after = text.rsplit(",", 1)
        text = before.replace(",", "") + ("." + after if len(after) in {1, 2} else after)
    elif text.count(".") > 1:
        before, after = text.rsplit(".", 1)
        text = before.replace(".", "") + "." + after
    try:
        price = float(text)
        return price if 0 <= price < 10_000_000 else None
    except ValueError:
        return None


def _product_from_json_ld(soup: BeautifulSoup) -> dict[str, Any]:
    for script in soup.select('script[type="application/ld+json"]'):
        raw = script.string or script.get_text(strip=True)
        if not raw:
            continue
        try:
            structured = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for item in _walk_json(structured):
            if not _is_product(item):
                continue
            offers = item.get("offers") or {}
            offer_items = offers if isinstance(offers, list) else [offers]
            offer = next((candidate for candidate in offer_items if isinstance(candidate, dict)), {})
            # A range across sizes/shops is not the price of this exact offer.
            price = parse_price(offer.get("price"))
            if price is None:
                continue
            return {
                "title": _first_string(item.get("name")),
                "brand": _first_string(item.get("brand", {}).get("name"))
                if isinstance(item.get("brand"), dict)
                else _first_string(item.get("brand")),
                "mpn": _first_string(item.get("mpn")),
                "color": _first_string(item.get("color")),
                "size": _first_string(item.get("size")),
                "price": price,
                "currency": _normalise_currency(offer.get("priceCurrency")),
                "image_url": _first_string(item.get("image")),
                "availability": _first_string(offer.get("availability")),
                "gtin": _first_string(
                    item.get("gtin13") or item.get("gtin") or item.get("gtin14") or item.get("gtin12")
                ),
            }
    return {}


def _shop_name(url: str) -> str:
    host = (urlsplit(url).hostname or "Unknown shop").lower()
    host = host.removeprefix("www.")
    return host.split(".")[0].replace("-", " ").title() or "Unknown shop"


def parse_product_page(html: str, page_url: str, discovered_title: str = "") -> dict[str, Any]:
    """Extract a product offer from Schema.org JSON-LD, with OpenGraph fallbacks."""
    soup = BeautifulSoup(html, "lxml")
    structured = _product_from_json_ld(soup)
    metadata_price = parse_price(_meta_content(soup, "product:price:amount"))
    # A broad CSS price match can be shipping, an old price, or another product.
    # Only explicit structured product prices enter the catalogue.
    price = structured.get("price") or metadata_price
    if price is None or price <= 0:
        return {"accepted": False, "reason": "The page did not expose a machine-readable price."}
    if price <= 1 and NON_ACTIONABLE_PRICE_PATTERNS.search(" ".join(soup.stripped_strings)):
        return {"accepted": False, "reason": "The displayed amount is a negotiable placeholder, not a current price."}

    page_title = soup.title.get_text(" ", strip=True) if soup.title else ""
    heading = soup.find("h1")
    heading_text = heading.get_text(" ", strip=True) if heading else ""
    title = (
        structured.get("title")
        or _meta_content(soup, "og:title", "twitter:title")
        or heading_text
        or discovered_title
        or page_title
    )
    title = re.sub(r"\s+", " ", str(title or "")).strip()
    if not title:
        return {"accepted": False, "reason": "The page did not expose a product title."}
    page_type = (_meta_content(soup, "og:type") or "").casefold()
    if page_type in {"article", "newsarticle", "blogposting", "blog"}:
        return {"accepted": False, "reason": "The page identifies itself as editorial content."}
    if NON_PRODUCT_PAGE_PATTERNS.search(title):
        return {
            "accepted": False,
            "reason": "The page looked like an article, guide, or category rather than one purchasable product.",
        }

    image_url = structured.get("image_url") or _meta_content(
        soup, "og:image", "twitter:image", "twitter:image:src"
    )
    if image_url:
        image_url = urljoin(page_url, image_url)
        if not is_public_http_url(image_url):
            image_url = None

    availability = structured.get("availability")
    if availability:
        availability = str(availability).rsplit("/", 1)[-1].replace("_", " ")

    return {
        "accepted": True,
        "title": title,
        "price": price,
        "currency": structured.get("currency")
        or _normalise_currency(_meta_content(soup, "product:price:currency", "og:price:currency")),
        "shop": _shop_name(page_url),
        "url": page_url,
        "image_url": image_url,
        "availability": availability,
        "gtin": structured.get("gtin"),
        **{key: structured.get(key) for key in ("brand", "mpn", "color", "size")},
    }


def parse_product_offers(html: str, page_url: str, discovered_title: str = "") -> list[dict[str, Any]]:
    """Extract multiple explicitly linked Product records from a category page."""
    soup = BeautifulSoup(html, "lxml")
    microdata = _microdata_offers(soup, page_url)
    items = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            items.extend(
                item
                for item in _walk_json(json.loads(script.string or script.get_text()))
                if _is_product(item)
            )
        except (ValueError, TypeError):
            continue
    offers = []
    expanded_items = []
    for item in items[:30]:
        variants = item.get("offers")
        if isinstance(variants, list):
            expanded_items.extend({**item, "offers": variant} for variant in variants[:30] if isinstance(variant, dict))
        else:
            expanded_items.append(item)
    for item in expanded_items[:90]:
        target = _first_string(item.get("url"))
        # ItemList products may point to category#product_1 while their actual
        # Offer points to a PDP. Bind URL to the SAME Offer whose price we read.
        item_offers = item.get("offers") or {}
        first_offer = (
            next((value for value in item_offers if isinstance(value, dict)), {})
            if isinstance(item_offers, list)
            else item_offers
        )
        offer_url = _first_string(first_offer.get("url")) if isinstance(first_offer, dict) else None
        if offer_url:
            target = offer_url
        elif target and urlsplit(urljoin(page_url, target)).fragment:
            resolved = urljoin(page_url, target)
            if len(items) > 1 and canonical_offer_url(resolved) == canonical_offer_url(page_url):
                # An identity anchor on a catalogue is not a product page.
                continue
        if not target and len(items) == 1:
            target = page_url
        if not target:
            continue
        target = canonical_offer_url(urljoin(page_url, target))
        if not is_public_http_url(target):
            continue
        document = (
            '<script type="application/ld+json">' + json.dumps(item).replace("</", "<\\/") + "</script>"
        )
        result = parse_product_page(document, target, discovered_title)
        if result.get("accepted"):
            result["source_url"] = page_url
            offers.append(result)
    # Recommendation cards must not hide a detail page's own head metadata.
    # Restrict OG fallback to the head: category cards can contain their own meta tags.
    head = soup.head or soup
    if _meta_content(head, "product:price:amount"):
        metadata = BeautifulSoup(str(head), "lxml")
        for script in metadata.select('script[type="application/ld+json"]'):
            script.decompose()
        own = parse_product_page(str(metadata), page_url, discovered_title)
        if own.get("accepted"):
            structured_own = next(
                (
                    offer
                    for offer in offers
                    if canonical_offer_url(offer["url"]) == canonical_offer_url(page_url)
                ),
                None,
            )
            if structured_own:
                # OG usually omits variant identifiers. It must not overwrite
                # the same PDP's richer JSON-LD with a bare generic name.
                for key, value in own.items():
                    if not structured_own.get(key) and value is not None:
                        structured_own[key] = value
            else:
                offers.append(own)
    # Keep separately linked cards as candidates, but validate each against the
    # original query downstream. Prefer main structured records for duplicate URLs.
    combined = {offer["url"]: offer for offer in microdata}
    for offer in offers:
        combined[offer["url"]] = offer
    editorial = (_meta_content(soup, "og:type") or "").casefold() in {"article", "newsarticle", "blogposting", "blog"}
    editorial = editorial or bool(re.search(r"/(?:articles?|news|blog|clanek|clanky|reviews?)/", urlsplit(page_url).path, re.I))
    if editorial:
        # An article may link a genuine product, but its own URL cannot be a
        # purchasable offer, even when its template embeds Product JSON-LD.
        combined = {url: offer for url, offer in combined.items()
                    if canonical_offer_url(url) != canonical_offer_url(page_url)}
    return list(combined.values())


def _microdata_offers(soup: BeautifulSoup, page_url: str) -> list[dict[str, Any]]:
    """Read scoped Schema.org/Shoptet product records, never arbitrary CSS prices."""
    products, seen = [], set()
    scopes = soup.select('[data-micro="product"], [itemscope][itemtype$="/Product"]')
    for scope in scopes[:40]:
        name = _owned_property(scope, '[data-micro="name"], [itemprop="name"]')
        # A main Product can contain recommendation and seller scopes. Their
        # properties do not belong to it. <link> is common on PrestaShop pages.
        link = _owned_property(scope, 'link[itemprop="url"], meta[itemprop="url"]', {"Offer"})
        main_heading = _owned_property(scope, "h1")
        if link is None and main_heading is None:
            link = _owned_property(scope, 'a[data-micro="url"], a[itemprop="url"]', {"Offer"})
        offer = _owned_property(scope, '[data-micro="offer"]')
        price_node = _owned_property(scope, '[itemprop="price"]', {"Offer", "PriceSpecification"})
        currency_node = _owned_property(scope, '[itemprop="priceCurrency"]', {"Offer", "PriceSpecification"})
        amount = parse_price(
            offer.get("data-micro-price") if offer else (price_node.get("content") or price_node.get_text(" ", strip=True)) if price_node else None
        )
        currency = _normalise_currency(
            offer.get("data-micro-price-currency")
            if offer
            else (currency_node.get("content") or currency_node.get_text(" ", strip=True))
            if currency_node
            else None
        )
        if name is None or amount is None or not currency:
            continue
        target = (
            (link.get("href") or link.get("content"))
            if link
            else page_url
            if main_heading is not None
            else None
        )
        if not target:
            continue
        target = urljoin(page_url, target)
        if target in seen or not is_public_http_url(target):
            continue
        if urlsplit(target).hostname != urlsplit(page_url).hostname:
            continue
        title = str(name.get("content") or name.get_text(" ", strip=True)).strip()
        image = _owned_property(scope, '[data-micro-image], [itemprop="image"], img', {"ImageObject"})
        image_url = None
        if image:
            image_url = (
                image.get("data-micro-image")
                or image.get("content")
                or image.get("data-src")
                or image.get("src")
            )
            if image_url:
                image_url = urljoin(page_url, image_url)
                if not is_public_http_url(image_url):
                    image_url = None
        availability = offer.get("data-micro-availability") if offer else None
        gtin = _owned_property(scope, '[itemprop="gtin13"], [itemprop="gtin"]')
        products.append(
            {
                "accepted": True,
                "title": title,
                "price": amount,
                "currency": currency,
                "shop": _shop_name(target),
                "url": target,
                "source_url": page_url,
                "image_url": image_url,
                "availability": availability.rsplit("/", 1)[-1] if availability else None,
                "gtin": str(gtin.get("content") or gtin.get_text(strip=True)) if gtin else None,
            }
        )
        seen.add(target)
    return products


def _owned_property(scope, selector, allowed_nested_types=frozenset()):
    """Select a field without leaking out of its owning microdata item."""
    for node in scope.select(selector):
        owned = True
        for ancestor in node.parents:
            if ancestor is scope:
                break
            if ancestor.get("data-micro") == "product":
                owned = False
                break
            if ancestor.has_attr("itemscope"):
                item_type = str(ancestor.get("itemtype", "")).rsplit("/", 1)[-1]
                if item_type not in allowed_nested_types:
                    owned = False
                    break
        if owned:
            return node
    return None


@tool("extract_offers")
def extract_offers(url: str, discovered_title: str = "", query: str = "", queries: list[str] | None = None) -> list[dict[str, Any]]:
    """Fetch explicit product offers, including separately linked category items."""
    html, final_url = _fetch_product_html(url)
    offers = parse_product_offers(html, final_url, discovered_title)
    # Server-rendered prices do not need Chromium. Use the same guarded DOM
    # parser here so scarce browser slots remain available for JS-only pages.
    from app.tools.browser_search import parse_rendered_product_page

    if not offers:
        rendered = parse_rendered_product_page(html, final_url, discovered_title, query, queries)
        if rendered.get("accepted"):
            offers.append(rendered)
    if offers and (not query or any(matches_query(query, item["title"], queries) for item in offers)):
        return offers
    # Search engines frequently return categories. Follow only bounded, explicit
    # product-card links, one level deep, and read each product's own metadata.
    for candidate in parse_catalog_links(html, final_url, query, queries):
        try:
            item_html, item_url = _fetch_product_html(candidate["url"])
            items = parse_product_offers(item_html, item_url, candidate["title"])
            if not items:
                rendered = parse_rendered_product_page(item_html, item_url, candidate["title"], query, queries)
                if rendered.get("accepted"):
                    items.append(rendered)
            offers.extend(items)
        except (httpx.HTTPError, ValueError):
            continue
    return offers


def parse_catalog_links(html: str, page_url: str, query: str = "", queries: list[str] | None = None) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "lxml")
    links, seen = [], {page_url}
    selectors = (
        'a[data-micro="url"], .product a.name, .product-card h3 a, '
        ".product-item-link, .product-title a, a.product-title, .card__heading a, "
        ".product h2 a, .product h3 a, .product-card a[href]"
    )
    # Prefer marked product cards, then discover title/image links in unknown
    # storefront layouts. The destination still has to supply its own offer.
    anchors = list(soup.select(selectors))
    anchors.extend(soup.select("h2 a[href], h3 a[href], a[href]:has(img), a[itemprop='url']"))
    for anchor in anchors:
        target = urljoin(page_url, str(anchor.get("href") or ""))
        if target in seen or urlsplit(target).hostname != urlsplit(page_url).hostname:
            continue
        if re.search(r"(?:cart|basket|checkout|wishlist|login|add-to|remove)[/?=\-]", target, re.I):
            continue
        title = anchor.get_text(" ", strip=True) or str(anchor.get("title") or "")
        if not title:
            image = anchor.find("img", alt=True)
            title = str(image.get("alt", "")) if image else ""
        if len(title) < 2 or (query and not matches_query(query, title, queries)) or not is_public_http_url(target):
            continue
        links.append({"url": target, "title": title[:500]})
        seen.add(target)
        if len(links) >= get_settings().per_store_product_limit:
            break
    return links


def _fetch_product_html(url: str) -> tuple[str, str]:
    settings = get_settings()
    if not is_public_http_url(url):
        raise ValueError("The candidate URL is not a public HTTP(S) address.")

    current_url = url
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
    timeout = httpx.Timeout(settings.http_timeout_seconds, connect=min(settings.http_timeout_seconds, 4.0))
    with httpx.Client(
        timeout=timeout, follow_redirects=False, headers=headers, verify=tls_context()
    ) as client:
        for _ in range(4):
            with client.stream("GET", current_url) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("The shop returned a redirect without a destination.")
                    next_url = urljoin(current_url, location)
                    if not is_public_http_url(next_url):
                        raise ValueError("The shop redirected to a non-public address.")
                    current_url = next_url
                    continue
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").lower()
                if "html" not in content_type:
                    raise ValueError("The candidate did not return an HTML product page.")
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > settings.max_page_bytes:
                        raise ValueError("The product page exceeded the download limit.")
                    chunks.append(chunk)
                return b"".join(chunks).decode(response.encoding or "utf-8", errors="replace"), str(
                    response.url
                )
    raise ValueError("The shop redirected too many times.")


@tool("extract_product")
def extract_product(url: str, discovered_title: str = "") -> dict[str, Any]:
    """Fetch one public product page and extract its title, price, currency, photo, link, and stock status."""
    html, final_url = _fetch_product_html(url)
    return parse_product_page(html, final_url, discovered_title)
