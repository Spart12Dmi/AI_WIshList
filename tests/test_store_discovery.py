from app.agents import select_relevant_stores


def test_store_selection_keeps_unique_shop_domains_and_rejects_social_sites():
    results = [
        {
            "url": "https://www.alza.cz/product-a",
            "title": "Product price",
            "snippet": "Shop with customer reviews and a price",
        },
        {"url": "https://m.alza.cz/product-b", "title": "Product price", "snippet": "Shop with a price"},
        {"url": "https://www.datart.cz/product-c", "title": "Product price", "snippet": "Store price"},
        {"url": "https://www.youtube.com/watch?v=example", "title": "Product video", "snippet": "Video"},
        {"url": "https://news.example.com/review", "title": "Product review", "snippet": "Review"},
    ]

    stores = select_relevant_stores(results, limit=15)

    assert [store["domain"] for store in stores] == ["alza.cz", "datart.cz"]
