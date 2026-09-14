from app.tools.web_search import parse_price, parse_product_page


def test_parse_price_handles_common_formats():
    assert parse_price("$1,299.95") == 1299.95
    assert parse_price("1.299,95 EUR") == 1299.95
    assert parse_price("not a price") is None


def test_parse_price_does_not_merge_unrelated_numbers():
    assert parse_price("Current price: $349.99 Product code 924") == 349.99


def test_parse_product_page_reads_json_ld_offer():
    html = """
    <html><head>
      <script type="application/ld+json">
        {"@context":"https://schema.org","@type":"Product","name":"Example Headphones",
         "image":"https://images.example.test/headphones.jpg",
         "offers":{"@type":"Offer","price":"129.99","priceCurrency":"EUR","availability":"https://schema.org/InStock"}}
      </script>
    </head></html>
    """
    product = parse_product_page(html, "https://shop.example.test/headphones")
    assert product["accepted"] is True
    assert product["title"] == "Example Headphones"
    assert product["price"] == 129.99
    assert product["currency"] == "EUR"
    assert product["availability"] == "InStock"


def test_parse_product_page_rejects_a_price_guide_with_a_visible_price():
    html = """
    <html><head><title>Whiskey Prices (2026) — Complete Price Guide</title></head>
    <body><h1>Whiskey Prices (2026) — Complete Price Guide</h1><p class="price">$217.51</p></body></html>
    """
    product = parse_product_page(html, "https://guide.example.test/whiskey")
    assert product["accepted"] is False
