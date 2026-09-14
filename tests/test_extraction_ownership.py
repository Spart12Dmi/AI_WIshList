import pytest

from app.matching import is_individual_product_url
from app.tools import web_search


def test_main_product_link_tag_not_recommendation_anchor(monkeypatch):
    monkeypatch.setattr(web_search, "is_public_http_url", lambda url: True)
    html = """<div itemscope itemtype="https://schema.org/Product">
    <h1 itemprop="name">LEGO Icons 10307 Eiffelova věž</h1>
    <p itemscope itemtype="http://schema.org/Offer"><link itemprop="url" href="/lego-icons-10307"><span itemprop="price" content="17999"></span>
    <meta itemprop="priceCurrency" content="CZK"></p>
    <aside><a itemprop="url" href="/intelino-train">Unrelated recommendation</a></aside></div>"""
    offers = web_search.parse_product_offers(html, "https://shop.cz/lego-icons-10307")
    assert [(offer["title"], offer["url"], offer["price"]) for offer in offers] == [
        ("LEGO Icons 10307 Eiffelova věž", "https://shop.cz/lego-icons-10307", 17999)
    ]


def test_nested_seller_and_product_properties_do_not_contaminate_parent(monkeypatch):
    monkeypatch.setattr(web_search, "is_public_http_url", lambda url: True)
    html = """<div itemscope itemtype="https://schema.org/Product">
    <div itemscope itemtype="https://schema.org/Organization"><span itemprop="name">Seller company</span>
    <a itemprop="url" href="/">Seller homepage</a></div>
    <h1 itemprop="name">Lagavulin 16</h1>
    <aside itemscope itemtype="https://schema.org/Product"><span itemprop="name">Danzka Vodka</span>
    <a itemprop="url" href="/vodka">Vodka</a><meta itemprop="price" content="499">
    <meta itemprop="priceCurrency" content="CZK"></aside>
    <a itemprop="url" href="/lagavulin">Lagavulin</a><meta itemprop="price" content="1699">
    <meta itemprop="priceCurrency" content="CZK"></div>"""
    offers = web_search.parse_product_offers(html, "https://shop.cz/lagavulin")
    main = next(offer for offer in offers if offer["title"] == "Lagavulin 16")
    assert main["url"] == "https://shop.cz/lagavulin"
    assert main["price"] == 1699


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://shop.cz", False),
        ("https://shop.cz/en/", False),
        ("https://shop.cz/?product_id=123", True),
        ("https://shop.cz/product", True),
    ],
)
def test_product_url_not_homepage(url, expected):
    assert is_individual_product_url(url) == expected
