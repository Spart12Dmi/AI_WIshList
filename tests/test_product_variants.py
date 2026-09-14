import json

from app.catalog import save_offer
from app.tools import web_search


def variant(code, url="https://shop.cz/item", **kwargs):
    return {
        "title": "NIKE AIR MAX 90",
        "brand": "Nike",
        "mpn": code,
        "shop": "Store",
        "url": url,
        "price": 3290,
        "currency": "CZK",
        **kwargs,
    }


def test_matching_names_with_different_mpns_never_share_minimum(client):
    grey = save_offer(variant("DM0029-025"), "czechia")
    white = save_offer(variant("DM0029-123", "https://shop.cz/white", price=1000), "czechia")
    assert grey["id"] != white["id"]
    assert grey["minimum_prices"] == {"CZK": 3290}


def test_same_manufacturer_variant_compares_across_shops(client):
    first = save_offer(variant("DM0029-025"), "czechia")
    second = save_offer(
        variant("DM0029-025", "https://other.cz/item", title="Nike Air Max 90 Grey", price=2990), "czechia"
    )
    assert second["id"] == first["id"]
    assert second["minimum_prices"] == {"CZK": 2990}
    assert second["offer_count"] == 2
    assert all(o["mpn"] == "DM0029-025" for o in second["offers"])


def test_size_constraint_not_lost_with_same_style_number(client):
    first = save_offer(variant("DM0029-025", size="42"), "czechia")
    second = save_offer(variant("DM0029-025", "https://other.cz/item", size="44"), "czechia")
    assert first["id"] != second["id"]


def test_refresh_without_identifiers_does_not_erase_known_variant(client):
    first = save_offer(variant("DM0029-025"), "czechia")
    refreshed = save_offer(variant(None, brand=None, price=2990), "czechia")
    assert refreshed["id"] == first["id"]
    assert refreshed["offers"][0]["mpn"] == "DM0029-025"


def test_jsonld_preserves_manufacturer_not_merchant_sku(monkeypatch):
    monkeypatch.setattr(web_search, "is_public_http_url", lambda url: True)
    item = {
        "@type": "Product",
        "name": "NIKE AIR MAX 90",
        "brand": {"@type": "Brand", "name": "Nike"},
        "mpn": "DM0029-025",
        "sku": "534530",
        "color": "Grey",
        "size": "42",
        "offers": {"@type": "Offer", "price": 3290, "priceCurrency": "CZK"},
    }
    html = '<script type="application/ld+json">' + json.dumps(item) + "</script>"
    html = (
        '<html><head><meta property="og:title" content="Nike Air Max 90"><meta property="product:price:amount" content="3290"><meta property="product:price:currency" content="CZK">'
        + html
        + "</head></html>"
    )
    result = web_search.parse_product_offers(html, "https://shop.cz/item")[0]
    assert (result["brand"], result["mpn"], result["color"], result["size"]) == (
        "Nike",
        "DM0029-025",
        "Grey",
        "42",
    )
