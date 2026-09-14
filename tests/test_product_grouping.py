from types import SimpleNamespace

import pytest

from app import graph
from app.catalog import identity, product_detail, save_offer
from app.database import connection, initialize
from app.matching import query_evidence


def offer(title, host="first", price=1700):
    return {"title": title, "url": f"https://{host}.cz/lagavulin", "price": price,
            "currency": "CZK", "shop": host}


def phone_offer(title, host="first", price=29990):
    return {"title": title, "url": f"https://{host}.cz/iphone", "price": price,
            "currency": "CZK", "shop": host}


@pytest.mark.parametrize("title", ["Lagavulin 16 y.o. 0,7 l 43%", "New Lagavulin 16 years old 70cl 43% product",
                                 "Lagavulin 16 let 43% 700 ml", "Lagavulin 16 YO 43% 0.7L | Shop"])
def test_age_and_volume_spellings_group_across_stores(client, title):
    first = save_offer(offer("Lagavulin 16 yo 0.7l 43%"), "czechia")
    second = save_offer(offer(title, "second", 1600), "czechia")
    assert first["id"] == second["id"]
    assert second["offer_count"] == 2
    assert second["minimum_prices"] == {"CZK": 1600}


@pytest.mark.parametrize("title", ["Lagavulin 8 yo 0.7l 43%", "Lagavulin 16 yo 0.2l 43%",
                                 "Lagavulin 16 yo 0.7l 48%", "Lagavulin 16 yo 0.7l 43% gift set",
                                 "Lagavulin 16 yo", "Lagavulin 16 yo 2x0.7l 43%"])
def test_materially_different_or_unknown_variants_stay_separate(title):
    assert identity(offer(title)) != identity(offer("Lagavulin 16 yo 0.7l 43%"))


def test_duplicate_product_streams_one_card_with_two_offers(client, account, monkeypatch):
    monkeypatch.setattr(graph.StoreDiscoveryAgent, "run", lambda *args: ([{"domain": "first.cz", "pages": [
        {"url": "https://first.cz/lagavulin", "title": "Lagavulin 16"}]}], []))
    monkeypatch.setattr(graph, "search_store_catalog", SimpleNamespace(invoke=lambda args: []))
    monkeypatch.setattr(graph, "extract_offers", SimpleNamespace(invoke=lambda args: [
        offer("Lagavulin 16 yo 0.7l 43%"), offer("New Lagavulin 16 years old 70cl 43% product", "second", 1600)]))
    events = list(graph.product_search_graph.stream(
        {"query": "Lagavulin", "region": "czechia", "max_results": 20, "search_mode": "quick"}, stream_mode="custom"))
    updates = [event["payload"]["product"] for event in events if event["event"] == "product"]
    assert len({product["id"] for product in updates}) == 1
    final = next(event["payload"] for event in events if event["event"] == "complete")
    assert len(final["products"]) == 1
    product = final["products"][0]
    assert product["offer_count"] == 2
    assert client.get(f'/api/products/{product["id"]}?region=czechia').json()["offer_count"] == 2
    list_id = client.get("/api/wishlists").json()[0]["id"]
    for _ in range(2):
        assert client.post(f"/api/wishlists/{list_id}/items", json={"product_id": product["id"]}).status_code == 201
    assert len(client.get(f"/api/wishlists/{list_id}").json()["items"]) == 1


def test_old_saved_ids_keep_combined_offers_and_notes_after_grouping(client, account):
    with connection() as db:
        for pid, title, price in [("old-a", "Lagavulin 16 yo 0.7l 43%", "1700"),
                                  ("old-b", "New Lagavulin 16 years old 70cl 43% product", "1600")]:
            db.execute("INSERT INTO products VALUES(?,?,?,?)", (pid, title, None, "title:" + title))
            db.execute("""INSERT INTO offers(url,product_id,title,shop,price,currency,region,checked_at)
                          VALUES(?,?,?,?,?,'CZK','czechia',strftime('%s','now'))""",
                       (f"https://{pid}.cz/item", pid, title, pid, price))
    list_id = client.get("/api/wishlists").json()[0]["id"]
    assert client.post(f"/api/wishlists/{list_id}/items", json={"product_id": "old-b", "notes": "Keep this note"}).status_code == 201
    initialize()
    initialize()
    detail = product_detail("old-b", "czechia")
    assert detail["id"] == product_detail("old-a")["id"]
    assert detail["offer_count"] == 2
    assert detail["minimum_prices"] == {"CZK": 1600}
    new = save_offer(offer("Lagavulin 16 y.o. 700ml 43%", "third", 1550), "czechia")
    assert new["id"] == detail["id"]
    assert new["offer_count"] == 3
    response = client.post(f"/api/wishlists/{list_id}/items", json={"product_id": new["id"]})
    assert response.json()["already_saved"]
    items = client.get(f"/api/wishlists/{list_id}").json()["items"]
    assert len(items) == 1
    assert items[0]["notes"] == "Keep this note"
    assert items[0]["product"]["offer_count"] == 3


def test_age_spelling_also_matches_search_query():
    assert query_evidence("Lagavulin 16 y o", "Lagavulin 16 years old 70cl 43%")


def test_concrete_model_search_groups_offer_dimensions_for_comparison(client):
    query = "Apple iPhone 17 Pro"
    first = save_offer(phone_offer("Apple iPhone 17 Pro 256GB Silver"), "czechia", query=query)
    second = save_offer(phone_offer("Apple iPhone 17 Pro 256GB Blue", "second", 28990), "czechia", query=query)
    third = save_offer(phone_offer("Apple iPhone 17 Pro 512GB Orange", "third", 33990), "czechia", query=query)

    assert first["id"] == second["id"] == third["id"]
    assert third["offer_count"] == 3
    assert third["minimum_prices"] == {"CZK": 28990}
    assert {item["shop"] for item in third["offers"]} == {"first", "second", "third"}


def test_broad_category_requests_do_not_collapse_every_listing(client):
    first = save_offer(phone_offer("Acme wireless headphones Silver", price=1000), "czechia")
    second = save_offer(phone_offer("Acme wireless headphones Blue", "second", 1100), "czechia")
    assert first["id"] != second["id"]
