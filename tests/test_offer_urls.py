import json

import pytest

from app.catalog import save_offer
from app.tools import web_search
from app.urls import canonical_offer_url
from evals.live import violations_for


@pytest.mark.parametrize(
    "suffix,expected",
    [
        ("?utm_source=x&srsltid=123", ""),
        ("?variant=red&utm_medium=cpc&id=4", "?variant=red&id=4"),
        ("?token=a%2Fb&variant=red%20blue", "?token=a%2Fb&variant=red%20blue"),
        ("#product-1652", ""),
        ("#product_1", ""),
        ("#/product/123", "#/product/123"),
        ("#size-42", "#size-42"),
    ],
)
def test_offer_url_dedup_preserves_real_variants(suffix, expected):
    assert canonical_offer_url("https://shop.cz/item" + suffix) == "https://shop.cz/item" + expected


def test_offer_link_belongs_to_same_jsonld_price(monkeypatch):
    monkeypatch.setattr(web_search, "is_public_http_url", lambda url: True)
    items = [
        {
            "@type": "Product",
            "name": f"Lagavulin {age}",
            "url": f"https://shop.cz/lagavulin/#product_{i}",
            "offers": {
                "@type": "Offer",
                "price": price,
                "priceCurrency": "CZK",
                "url": f"/products/lagavulin-{age}",
            },
        }
        for i, (age, price) in enumerate([(8, 1400), (16, 1800)])
    ]
    html = '<script type="application/ld+json">' + json.dumps(items) + "</script>"
    offers = web_search.parse_product_offers(html, "https://shop.cz/lagavulin/")
    assert [(o["url"], o["price"]) for o in offers] == [
        ("https://shop.cz/products/lagavulin-8", 1400),
        ("https://shop.cz/products/lagavulin-16", 1800),
    ]
    for item in items:
        del item["offers"]["url"]
    html = '<script type="application/ld+json">' + json.dumps(items) + "</script>"
    assert web_search.parse_product_offers(html, "https://shop.cz/lagavulin/") == []


def test_tracking_does_not_create_duplicate_persisted_offer(client):
    raw = {
        "title": "Canon EOS R50",
        "price": 15000,
        "currency": "CZK",
        "shop": "Shop",
        "url": "https://shop.cz/canon?utm_source=search",
    }
    first = save_offer(raw, "czechia")
    second = save_offer({**raw, "url": "https://shop.cz/canon?srsltid=abc"}, "czechia")
    assert first["id"] == second["id"]
    assert second["offer_count"] == 1
    assert second["offers"][0]["url"] == "https://shop.cz/canon"


def test_independent_live_checks_catch_previously_missed_rental_and_fragment():
    products = [
        {
            "title": "Canon EOS R50 - Půjčovna",
            "minimum_prices": {"CZK": 100},
            "offers": [{"title": "Canon EOS R50", "url": "https://shop.cz/pujcovna/canon#product_1"}],
        }
    ]
    problems = violations_for({"required": ["Canon", "R50"], "currency": "CZK"}, products)
    assert any(p.startswith("rental_not_purchase") for p in problems)
    assert any(p.startswith("rental_product_link") for p in problems)
    assert any(p.startswith("schema_identity_product_link") for p in problems)


def test_live_checker_catches_same_offer_in_different_cards():
    product = {
        "title": "Canon EOS R50",
        "minimum_prices": {"CZK": 15000},
        "offers": [{"title": "Canon EOS R50", "url": "https://shop.cz/canon", "price": 15000}],
    }
    problems = violations_for(
        {"required": ["Canon", "R50"], "currency": "CZK"},
        [{**product, "id": "old"}, {**product, "id": "new"}],
    )
    assert problems == ["offer_in_multiple_product_cards: https://shop.cz/canon"]
