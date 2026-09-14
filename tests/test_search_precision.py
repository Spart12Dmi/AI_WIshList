"""Search quality regressions: a nonempty result list is not a successful search."""

from types import SimpleNamespace

import pytest

from app import agents, graph
from app.catalog import product_detail, save_offer
from app.config import get_settings
from app.matching import query_evidence
from app.regions import get_region
from app.schemas import ProductMatch
from app.tools import web_search
from app.tools.web_search import parse_product_offers


@pytest.mark.parametrize(
    "query,title,expected",
    [
        ("lagavulin", "PILSNER URQUELL PLZEN 30l KEG /4,4%", False),
        ("lagavulin", "Vodka Black Currant 40% 50ml Danzka miniatura", False),
        ("lagavulin", "Vodka Black Currant 40% 50ml x12 Danzka miniatur", False),
        ("lagavulin", "Whisky Lagavulin 16y 43% 0,2l v sada č. 2", True),
        ("lagavulin", "Lagavulin 16 Years Old 0.7l 43%", True),
        ("lagavulin whisky", "Johnnie Walker Blue Label whisky", False),
        ("Lagavulin 16", "Lagavulin 8yo 0.7l", False),
        ("Lagavulin 16", "Lagavulin 16 Years Old 0.7l", True),
        ("Lagavulin 16 0.7l", "Lagavulin 16yo 43% 700ml", True),
        ("Lagavulin 16 0.7l", "Lagavulin 16yo 43% 0.2l", False),
        ("Logitech", "Razer DeathAdder gaming mouse", False),
        ("Logitech G502", "Logitech G305 mouse", False),
        ("Logitech G502", "Logitech G502 HERO herní myš", True),
        ("Sony headphones", "JBL wireless headphones", False),
        ("Sony headphones", "Bezdrátová sluchátka Sony WH-1000XM5", True),
        ("headphones", "Bezdrátová sluchátka Sony WH-1000XM5", True),
        ("coffee grinder", "Elektrický mlýnek na kávu Baratza Encore", True),
        ("coffee grinder", "Coffee mug 350ml", False),
        ("whiskey", "Johnnie Walker Blue Label 1l", True),
    ],
)
def test_original_query_constraints(query, title, expected):
    assert query_evidence(query, title) is expected


def test_positive_llm_cannot_override_brand_mismatch(client, monkeypatch):
    get_settings().use_semantic_validation = True
    monkeypatch.setattr(agents.httpx, "get", lambda *a, **kw: SimpleNamespace(raise_for_status=lambda: None))
    result = ProductMatch(relevant=True, reason="Intentionally incorrect classifier for gate regression")
    fake = SimpleNamespace(
        with_structured_output=lambda *a, **kw: SimpleNamespace(invoke=lambda prompt: result)
    )
    monkeypatch.setattr(agents, "ChatOllama", lambda **kw: fake)
    products, _ = agents.SemanticValidationAgent().run(
        [
            {
                "title": "PILSNER URQUELL PLZEN 30l KEG",
                "url": "https://shop.cz/beer",
                "price": 2261.17,
                "currency": "CZK",
                "shop": "Shop",
            }
        ],
        "lagavulin",
        get_region("czechia"),
    )
    assert products == []


def test_stream_never_emits_wrong_brand_even_with_accept_all_classifier(client, monkeypatch):
    monkeypatch.setattr(graph.StoreDiscoveryAgent, "run", lambda *a: ([{"domain": "shop.cz"}], []))
    monkeypatch.setattr(
        graph,
        "search_store_catalog",
        SimpleNamespace(invoke=lambda args: [{"url": "https://shop.cz/lagavulin", "title": "Lagavulin"}]),
    )
    titles = ["PILSNER URQUELL KEG", "Danzka Vodka", "Lagavulin 16 Years Old 700ml", "Lagavulin 8yo 0.7l"]
    monkeypatch.setattr(
        graph,
        "extract_offers",
        SimpleNamespace(
            invoke=lambda args: [
                {
                    "title": title,
                    "url": f"https://shop.cz/item-{i}",
                    "shop": "Shop",
                    "price": 1500 + i,
                    "currency": "CZK",
                }
                for i, title in enumerate(titles)
            ]
        ),
    )
    monkeypatch.setattr(graph.SemanticValidationAgent, "run", lambda self, products, *a: (products, []))
    events = list(
        graph.product_search_graph.stream(
            {"query": "lagavulin", "region": "czechia", "max_results": 20}, stream_mode="custom"
        )
    )
    emitted = [event["payload"]["product"]["title"] for event in events if event["event"] == "product"]
    assert set(emitted) == set(titles[2:])


def test_detail_product_is_not_hidden_by_recommendation_microdata(monkeypatch):
    monkeypatch.setattr(web_search, "is_public_http_url", lambda url: url.startswith("https://shop.cz/"))
    html = """<html><head><meta property="og:title" content="Lagavulin 16 Years Old 0.7l">
        <meta property="product:price:amount" content="1699"><meta property="product:price:currency" content="CZK"></head>
        <body><h1>Lagavulin 16</h1><aside><div data-micro="product">
        <a data-micro="url" href="/vodka"><span data-micro="name">Danzka Vodka</span></a>
        <div data-micro="offer" data-micro-price="500" data-micro-price-currency="CZK"></div></div></aside></body></html>"""
    offers = parse_product_offers(html, "https://shop.cz/lagavulin-16")
    actual = [offer for offer in offers if offer["url"] == "https://shop.cz/lagavulin-16"]
    assert len(actual) == 1
    assert actual[0]["title"] == "Lagavulin 16 Years Old 0.7l"
    assert actual[0]["price"] == 1699


def test_corrected_page_does_not_reuse_old_wrong_catalogue_title(client):
    wrong = save_offer(
        {
            "title": "Pilsner Urquell",
            "url": "https://shop.cz/lagavulin",
            "price": 2000,
            "currency": "CZK",
            "shop": "Shop",
        },
        "czechia",
    )
    corrected = save_offer(
        {
            "title": "Lagavulin 16 0.7l",
            "url": "https://shop.cz/lagavulin",
            "price": 1600,
            "currency": "CZK",
            "shop": "Shop",
        },
        "czechia",
    )
    assert corrected["title"] == "Lagavulin 16 0.7l"
    assert corrected["id"] != wrong["id"]
    assert product_detail(wrong["id"])["offers"] == []


def test_only_current_available_prices_are_streamed(client, monkeypatch):
    monkeypatch.setattr(graph.StoreDiscoveryAgent, "run", lambda *a: ([{"domain": "shop.cz"}], []))
    monkeypatch.setattr(
        graph,
        "search_store_catalog",
        SimpleNamespace(invoke=lambda args: [{"url": "https://shop.cz/lagavulin", "title": "Lagavulin"}]),
    )
    monkeypatch.setattr(
        graph,
        "extract_offers",
        SimpleNamespace(
            invoke=lambda args: [
                {
                    "title": "Lagavulin 16" if available else "Lagavulin 8",
                    "url": f"https://shop.cz/{available}",
                    "shop": "Shop",
                    "price": 1600,
                    "currency": "CZK",
                    "availability": "InStock" if available else "OutOfStock",
                }
                for available in [True, False]
            ]
        ),
    )
    monkeypatch.setattr(graph.SemanticValidationAgent, "run", lambda self, products, *a: (products, []))
    events = list(
        graph.product_search_graph.stream(
            {"query": "lagavulin", "region": "czechia", "max_results": 20}, stream_mode="custom"
        )
    )
    emitted = [event["payload"]["product"]["title"] for event in events if event["event"] == "product"]
    assert emitted == ["Lagavulin 16"]


def test_category_budget_counts_matching_products_not_first_unrelated_cards(client, monkeypatch):
    monkeypatch.setattr(web_search, "is_public_http_url", lambda url: url.startswith("https://shop.cz/"))
    get_settings().per_store_product_limit = 2
    titles = ["Beer", "Vodka", "Rum", "Lagavulin 16", "Lagavulin 8"]
    html = "".join(
        f'<div class="product"><a class="name" href="/item-{i}">{title}</a></div>'
        for i, title in enumerate(titles)
    )
    pages = web_search.parse_catalog_links(html, "https://shop.cz/category", "lagavulin")
    assert [p["title"] for p in pages] == titles[3:]


def test_unrelated_metadata_does_not_hide_relevant_catalog_links(client, monkeypatch):
    monkeypatch.setattr(web_search, "is_public_http_url", lambda url: url.startswith("https://shop.cz/"))
    category = """<div data-micro="product"><a data-micro="url" href="/beer"><span data-micro="name">Beer</span></a>
    <div data-micro="offer" data-micro-price="30" data-micro-price-currency="CZK"></div></div>
    <div class="product"><a class="name" href="/lagavulin">Lagavulin 16</a></div>"""
    detail = """<head><meta property="og:title" content="Lagavulin 16">
    <meta property="product:price:amount" content="1699"><meta property="product:price:currency" content="CZK"></head>"""
    calls = []

    def fetch(url):
        calls.append(url)
        return (category if url.endswith("category") else detail), url

    monkeypatch.setattr(web_search, "_fetch_product_html", fetch)
    offers = web_search.extract_offers.invoke({"url": "https://shop.cz/category", "query": "lagavulin"})
    assert "https://shop.cz/lagavulin" in calls
    assert any(offer["title"] == "Lagavulin 16" for offer in offers)


def test_observed_alternative_currency_metadata(monkeypatch):
    """Minimal metadata shape observed on Alkosklad, not an invented currency."""
    monkeypatch.setattr(web_search, "is_public_http_url", lambda url: True)
    html = """<head><meta property="og:title" content="Lagavulin 16 Y.O. 43% 0,7 l">
    <meta content="2590.00" property="product:price:amount">
    <meta content="CZK" property="og:price:currency"></head>"""
    offers = web_search.parse_product_offers(html, "https://www.alkosklad.cz/lagavulin-16-y-o-43-0-7-l/")
    assert offers[0]["currency"] == "CZK"
    assert offers[0]["price"] == 2590


def test_discovery_cache_is_scoped_and_does_not_share_mutable_rows(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "search_backends", "bing")
    web_search._search_cache.clear()
    monkeypatch.setattr(web_search, "is_public_http_url", lambda url: True)
    calls = []

    def search(query, **kwargs):
        calls.append(query)
        return [{"title": "Logitech G502", "href": "https://shop.cz/g502"}]

    monkeypatch.setattr(web_search, "DDGS", lambda **kwargs: SimpleNamespace(text=search))
    first = web_search._run_web_search("Logitech G502", 3, "cz-cs")
    first[0]["title"] = "mutated"
    assert web_search._run_web_search("Logitech G502", 3, "cz-cs")[0]["title"] == "Logitech G502"
    assert len(calls) == 1
    web_search._run_web_search("Logitech G502", 3, "de-de")
    assert len(calls) == 2
    monkeypatch.setattr(get_settings(), "search_cache_ttl_seconds", 0)
    web_search._run_web_search("Logitech G502", 3, "cz-cs")
    assert len(calls) == 3
    web_search._search_cache.clear()


def test_retrieved_source_is_refetched_without_rediscovery(client, monkeypatch):
    save_offer(
        {
            "title": "Lagavulin 16",
            "url": "https://shop.cz/lagavulin-16",
            "price": 1600,
            "currency": "CZK",
            "shop": "Shop",
        },
        "czechia",
    )
    monkeypatch.setattr(graph.StoreDiscoveryAgent, "run", lambda *args: ([], []))
    monkeypatch.setattr(graph, "search_store_catalog", SimpleNamespace(invoke=lambda args: []))
    fetched = []

    def extract(args):
        fetched.append(args["url"])
        return [
            {"title": "Lagavulin 16", "url": args["url"], "price": 1700, "currency": "CZK", "shop": "Shop"}
        ]

    monkeypatch.setattr(graph, "extract_offers", SimpleNamespace(invoke=extract))
    events = list(
        graph.product_search_graph.stream(
            {"query": "lagavulin", "region": "czechia", "max_results": 20}, stream_mode="custom"
        )
    )
    assert "https://shop.cz/lagavulin-16" in fetched
    products = [event["payload"]["product"] for event in events if event["event"] == "product"]
    assert products[0]["minimum_prices"] == {"CZK": 1700.0}
