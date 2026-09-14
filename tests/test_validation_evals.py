"""Small deterministic regression/evaluation set, including failures seen in live searches."""

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app import agents
from app.config import get_settings
from app.matching import lexical_match, query_evidence
from app.regions import get_region
from app.schemas import OfferAssessment, OfferAssessments, ProductOffer
from app.tools import web_search


@pytest.mark.parametrize(
    "query,title,expected",
    [
        ("whiskey", "Sierra Tequila Blanco 1l 35%", False),
        ("whiskey", "GOLD WELL whisky 51.5% 0.5L", True),
        ("whiskey", "Johnnie Walker Blue Label 1 l", True),
        ("whisky", "Blanton's Original Bourbon 0.7L", True),
        ("headphones", "Bezdrátová sluchátka Sony", True),
        ("headphones", "Logitech keyboard", False),
        ("Logitech G502", "Logitech G305 mouse", False),
    ],
)
def test_category_and_model_evidence(query, title, expected):
    assert query_evidence(query, title) is expected


def test_offline_matching_does_not_accept_unrelated_brand():
    assert not lexical_match("Logitech", "Sierra Tequila Blanco")


@pytest.mark.parametrize(
    "field,value",
    [
        ("price", float("nan")),
        ("price", float("inf")),
        ("price", -5),
        ("url", "javascript:alert(1)"),
        ("url", "http://127.0.0.1/"),
        ("url", "http://localhost/"),
        ("url", "http://10.0.0.1/admin"),
        ("url", "https://shop.cz:8080/"),
        ("currency", "not-currency"),
    ],
)
def test_offer_schema_rejects_invalid_output(field, value):
    data = {
        "title": "Example product",
        "price": 100,
        "currency": "CZK",
        "shop": "Shop",
        "url": "https://shop.cz/product",
    }
    data[field] = value
    with pytest.raises(ValidationError):
        ProductOffer.model_validate(data)


def test_semantic_booleans_are_not_truthy_strings():
    with pytest.raises(ValidationError):
        OfferAssessment(url="https://shop.cz/item", relevant="false", region_match=True, confidence=0.8)


def test_hallucinated_assessment_url_triggers_conservative_fallback(client, monkeypatch):
    get_settings().use_semantic_validation = True
    monkeypatch.setattr(agents.httpx, "get", lambda *a, **kw: SimpleNamespace(raise_for_status=lambda: None))
    result = OfferAssessments(
        assessments=[
            OfferAssessment(
                url="https://invented.cz/product", relevant=True, region_match=True, confidence=0.99
            )
        ]
    )
    fake = SimpleNamespace(
        with_structured_output=lambda *a, **kw: SimpleNamespace(invoke=lambda prompt: result)
    )
    monkeypatch.setattr(agents, "ChatOllama", lambda **kw: fake)
    offers, warnings = agents.SemanticValidationAgent().run(
        [
            {
                "title": "Tequila Blanco",
                "price": 500,
                "currency": "CZK",
                "shop": "Shop",
                "url": "https://shop.cz/item",
            }
        ],
        "whiskey",
        get_region("czechia"),
    )
    assert not offers
    assert any("invalid output" in message for message in warnings)


def test_multiple_structured_products_keep_individual_links(monkeypatch):
    monkeypatch.setattr(web_search, "is_public_http_url", lambda value: value.startswith("https://shop.cz/"))
    items = [
        {
            "@type": "Product",
            "name": f"Whisky {i}",
            "url": f"/whisky-{i}",
            "offers": {"@type": "Offer", "price": i * 100, "priceCurrency": "CZK"},
        }
        for i in (1, 2)
    ]
    html = '<script type="application/ld+json">' + json.dumps(items) + "</script>"
    offers = web_search.parse_product_offers(html, "https://shop.cz/whisky")
    assert [offer["url"] for offer in offers] == ["https://shop.cz/whisky-1", "https://shop.cz/whisky-2"]
    assert [offer["price"] for offer in offers] == [100, 200]


def test_aggregate_price_range_is_not_an_exact_product_offer():
    html = (
        '<script type="application/ld+json">'
        + json.dumps(
            {
                "@type": "Product",
                "name": "Assorted headphones",
                "offers": {
                    "@type": "AggregateOffer",
                    "lowPrice": 100,
                    "highPrice": 500,
                    "priceCurrency": "CZK",
                },
            }
        )
        + "</script>"
    )
    assert not web_search.parse_product_offers(html, "https://shop.cz/category")


def test_guide_metadata_does_not_make_it_a_product():
    html = '<title>Whiskey price guide</title><meta property="product:price:amount" content="500">'
    assert not web_search.parse_product_offers(html, "https://shop.cz/guide")


def test_private_redirect_is_blocked_before_fetch(monkeypatch):
    monkeypatch.setattr(web_search.socket, "getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", ("127.0.0.1", 0))])
    assert not web_search.is_public_http_url("https://public-looking.example/")


def test_plan_rejects_invented_sizes_and_repetition():
    with pytest.raises(ValueError):
        agents.validate_planned_query("whiskey", "whiskey 750ml 40%")
    with pytest.raises(ValueError):
        agents.validate_planned_query("whiskey", "whisky " * 25)
    agents.validate_planned_query("Logitech G502", "Logitech G502 cena koupit")


def test_czech_domain_does_not_make_gbp_price_valid():
    _, matches, _ = agents.SemanticValidationAgent._deterministic_assessment(
        {"title": "Jameson whiskey", "url": "https://shop.cz/product", "currency": "GBP"},
        get_region("czechia"),
    )
    assert not matches


def test_scoped_shoptet_product_cards_do_not_mix_prices(monkeypatch):
    monkeypatch.setattr(web_search, "is_public_http_url", lambda value: value.startswith("https://shop.cz/"))
    html = '<p class="price">Shipping 49 CZK</p>' + "".join(
        f'<div data-micro="product"><a data-micro="url" href="/item-{i}"><span data-micro="name">Whisky {i}</span></a>'
        f'<div data-micro="offer" data-micro-price="{i * 100}" data-micro-price-currency="CZK"></div></div>'
        for i in (1, 2)
    )
    offers = web_search.parse_product_offers(html, "https://shop.cz/category")
    assert [item["price"] for item in offers] == [100, 200]
    assert all(item["source_url"] == "https://shop.cz/category" for item in offers)
    assert offers[0]["url"] == "https://shop.cz/item-1"


def test_category_navigation_follows_only_product_cards(monkeypatch):
    monkeypatch.setattr(web_search, "is_public_http_url", lambda value: value.startswith("https://shop.cz/"))
    html = '<a href="/account">My account</a><div class="product"><a class="name" href="/bottle">Whisky bottle</a></div>'
    assert web_search.parse_catalog_links(html, "https://shop.cz/category") == [
        {"url": "https://shop.cz/bottle", "title": "Whisky bottle"}
    ]


def test_regional_discovery_retries_when_provider_ignores_region(client, monkeypatch):
    calls = []

    def search(args):
        calls.append(args["query"])
        if len(calls) == 1:
            return [{"url": "https://foreign.ru/whisky", "title": "Whisky shop", "snippet": "buy"}]
        return [{"url": "https://merchant.cz/whisky", "title": "Whisky shop", "snippet": "koupit"}]

    monkeypatch.setattr(agents, "search_web", SimpleNamespace(invoke=search))
    stores, _ = agents.StoreDiscoveryAgent().run("whisky", get_region("czechia"), "whiskey")
    assert [store["domain"] for store in stores] == ["merchant.cz"]
    assert "site:.cz" in calls[1]
