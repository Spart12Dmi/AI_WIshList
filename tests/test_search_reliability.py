"""Regression checks for failures observed across live merchant searches."""

from types import SimpleNamespace

import pytest

from app import graph
from app.agents import SemanticValidationAgent
from app.config import get_settings
from app.regions import get_region
from app.tools import browser_search, web_search


@pytest.mark.parametrize("title,currency,amount", [
    ("PlayStation 5 Pro", "CZK", 23890),
    ("Baratza Encore", "EUR", 149),
    ("Nike Air Max 90", "GBP", 109),
    ("Samsung 990 PRO", "PLN", 499),
    ("Logitech G502", "USD", 39),
])
def test_static_price_extraction_works_across_product_types(monkeypatch, title, currency, amount):
    html = f"<h1>{title}</h1><div class='price-current'>{amount} {currency}</div>"
    monkeypatch.setattr(web_search, "_fetch_product_html", lambda url: (html, url))
    offers = web_search.extract_offers.invoke({"url": "https://shop.example/item", "query": title})
    assert [(offer["title"], offer["price"], offer["currency"]) for offer in offers] == [(title, amount, currency)]


@pytest.mark.parametrize("bad_html", [
    "<div class='recommendations'><span class='price'>10 CZK</span></div>",
    "<del><span class='price'>10 CZK</span></del>",
    "<div class='shipping'><span class='price'>10 CZK</span></div>",
    "<a href='/other-product'><span class='price'>10 CZK</span></a>",
    "<div hidden><span class='price'>10 CZK</span></div>",
    "<div class='price'>20% discount, 10 CZK</div>",
    "<div class='price'>10 CZK or 20 CZK</div>",
])
def test_ancillary_prices_are_not_attached_to_main_product(bad_html):
    html = "<h1>Example product</h1>" + bad_html
    offer = browser_search.parse_rendered_product_page(html, "https://shop.example/item", query="Example product")
    assert not offer["accepted"]
    offer = browser_search.parse_rendered_product_page(
        html + "<div class='current-price'>500 CZK</div>", "https://shop.example/item", query="Example product"
    )
    assert offer["price"] == 500


def test_discovery_title_cannot_turn_error_page_into_product():
    offer = browser_search.parse_rendered_product_page(
        "<div class='price'>500 CZK</div>", "https://shop.example/item", "Example product", "Example product"
    )
    assert not offer["accepted"]


def test_semantic_failure_does_not_turn_keyword_overlap_into_verified_offer(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "use_semantic_validation", True)
    monkeypatch.setattr("app.agents.httpx.get", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline")))
    offers, warnings = SemanticValidationAgent().run([
        {"title": "Example product", "url": "https://shop.cz/item", "currency": "CZK", "price": 500}
    ], "Example product", get_region("czechia"))
    assert offers == []
    assert any("could not be verified" in warning for warning in warnings)


def test_catalogue_heading_and_price_are_not_a_single_offer():
    html = "<h1>Example product</h1><div class='price'>500 CZK</div>"
    assert not browser_search.parse_rendered_product_page(
        html, "https://shop.cz/collections/example", query="Example product"
    )["accepted"]


def test_editorial_product_schema_is_not_a_shop_offer(monkeypatch):
    monkeypatch.setattr(web_search, "is_public_http_url", lambda url: True)
    html = '''<meta property="og:type" content="article">
    <script type="application/ld+json">{"@type":"Product","name":"Example product",
    "offers":{"@type":"Offer","price":500,"priceCurrency":"CZK"}}</script>'''
    assert web_search.parse_product_offers(html, "https://site.example/blog/launch") == []


def test_each_structured_offer_keeps_its_own_link_and_price(monkeypatch):
    monkeypatch.setattr(web_search, "is_public_http_url", lambda url: True)
    html = '''<script type="application/ld+json">{"@type":"Product","name":"Example product",
    "offers":[{"@type":"Offer","price":500,"priceCurrency":"CZK","url":"/variant-a"},
              {"@type":"Offer","price":700,"priceCurrency":"CZK","url":"/variant-b"}]}</script>'''
    offers = web_search.parse_product_offers(html, "https://shop.example/collection")
    assert [(o["url"], o["price"]) for o in offers] == [
        ("https://shop.example/variant-a", 500), ("https://shop.example/variant-b", 700)
    ]


def test_catalogue_image_links_work_without_shop_specific_css(monkeypatch):
    monkeypatch.setattr(web_search, "is_public_http_url", lambda url: True)
    html = '<a href="/item"><img alt="Example product" src="/photo.jpg"></a>'
    assert web_search.parse_catalog_links(html, "https://shop.example/catalogue", "Example product") == [
        {"url": "https://shop.example/item", "title": "Example product"}
    ]


def search_state(stores):
    return {"query": "Example product", "region": "czechia", "search_mode": "quick",
            "max_results": 20, "warnings": [], "pipeline": [], "stores": stores}


def test_first_failing_store_cannot_take_other_stores_browser_attempts(client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "use_browser_fallback", True)
    monkeypatch.setattr(settings, "browser_candidate_limit", 3)
    monkeypatch.setattr(settings, "store_search_workers", 1)
    monkeypatch.setattr(graph, "emit", lambda *args, **kwargs: None)
    monkeypatch.setattr(graph, "extract_offers", SimpleNamespace(invoke=lambda args: []))
    monkeypatch.setattr(graph, "search_store_catalog", SimpleNamespace(invoke=lambda args: []))
    calls = []

    def browser(candidates):
        calls.append(candidates[0]["url"])
        return iter([{"accepted": False, "reason": "browser_http_403"}])

    monkeypatch.setattr(graph, "iter_browser_product_extractions", browser)
    stores = [{"domain": f"shop{i}.cz", "pages": [
        {"url": f"https://shop{i}.cz/item{n}", "title": "Example product"} for n in range(3)
    ]} for i in range(3)]
    graph.search_and_validate(search_state(stores))
    assert calls == [f"https://shop{i}.cz/item0" for i in range(3)]


def test_queued_offers_survive_network_deadline_during_validation(client, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(graph, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(graph, "emit", lambda *args, **kwargs: None)
    monkeypatch.setattr(graph, "search_store_catalog", SimpleNamespace(invoke=lambda args: []))
    monkeypatch.setattr(graph, "extract_offers", SimpleNamespace(invoke=lambda args: [
        {"title": "Example product", "price": 500 + i, "currency": "CZK", "shop": f"shop{i}",
         "url": f"https://shop{i}.cz/item"} for i in range(3)
    ]))

    def validate(self, offers, *args):
        clock[0] = 1000  # First LLM decision finishes after the network deadline.
        return offers, []

    monkeypatch.setattr(graph.SemanticValidationAgent, "run", validate)
    result = graph.search_and_validate(search_state([
        {"domain": "shop0.cz", "pages": [{"url": "https://shop0.cz/item", "title": "Example product"}]}
    ]))
    assert sum(p["offer_count"] for p in result["products"]) == 3
