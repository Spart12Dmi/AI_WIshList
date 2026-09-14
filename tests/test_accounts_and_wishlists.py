import time

from app.auth import verify_password
from app.catalog import product_detail, save_offer
from app.database import connection
from app.retrieval import OfferRetriever


def offer(url="https://shop.cz/product", currency="CZK", amount=1299):
    return {
        "title": "Example Headphones Black 2026",
        "price": amount,
        "currency": currency,
        "url": url,
        "shop": "Shop",
    }


def test_account_password_session_csrf_and_logout(client, account):
    with connection() as db:
        stored = dict(db.execute("SELECT * FROM users").fetchone())
    assert stored["password_hash"] != "a-strong-test-password"
    assert verify_password("a-strong-test-password", stored["password_hash"])
    assert client.get("/api/auth/me").status_code == 200
    assert (
        client.post("/api/wishlists", json={"name": "Test"}, headers={"X-CSRF-Token": "wrong"}).status_code
        == 403
    )
    assert (
        client.post(
            "/api/wishlists", json={"name": "Test"}, headers={"Origin": "https://evil.test"}
        ).status_code
        == 403
    )
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401
    assert (
        client.post(
            "/api/auth/login", json={"email": "test@example.com", "password": "incorrect-password"}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/auth/login", json={"email": "test@example.com", "password": "a-strong-test-password"}
        ).status_code
        == 200
    )


def test_wishlist_ownership_and_persistence(client, account):
    own = client.get("/api/wishlists").json()[0]["id"]
    product = save_offer(offer(), "czechia")
    body = {
        "product_id": product["id"],
        "target_price": "1500.00",
        "target_currency": "CZK",
        "notes": "Birthday",
    }
    assert client.post(f"/api/wishlists/{own}/items", json=body).status_code == 201
    assert client.post(f"/api/wishlists/{own}/items", json=body).json()["already_saved"]
    assert client.get(f"/api/wishlists/{own}").json()["items"][0]["notes"] == "Birthday"
    client.post("/api/auth/logout")
    response = client.post(
        "/api/auth/register",
        json={"email": "second@example.com", "name": "Other", "password": "another-test-password"},
    )
    client.headers["X-CSRF-Token"] = response.json()["csrf"]
    assert client.get(f"/api/wishlists/{own}").status_code == 404
    assert client.delete(f"/api/wishlists/{own}").status_code == 404
    assert client.post(f"/api/wishlists/{own}/items", json=body).status_code == 404
    client.post("/api/auth/logout")
    response = client.post(
        "/api/auth/login", json={"email": "test@example.com", "password": "a-strong-test-password"}
    )
    client.headers["X-CSRF-Token"] = response.json()["csrf"]
    assert len(client.get(f"/api/wishlists/{own}").json()["items"]) == 1


def test_currency_minima_staleness_and_identity(client):
    product = save_offer(offer(), "czechia")
    second = save_offer(offer("https://other.cz/product", amount=999), "czechia")
    assert second["id"] == product["id"]
    save_offer(offer("https://euro.de/product", currency="EUR", amount=40), "germany")
    combined = product_detail(product["id"])
    assert combined["minimum_prices"] == {"CZK": 999, "EUR": 40}
    assert len(product_detail(product["id"], "czechia")["offers"]) == 2
    with connection() as db:
        db.execute(
            "UPDATE offers SET checked_at=? WHERE url=?", (time.time() - 90000, "https://other.cz/product")
        )
    assert product_detail(product["id"])["minimum_prices"]["CZK"] == 1299
    white = offer()
    white["title"] = "Example Headphones White 2026"
    white["url"] = "https://shop.cz/white"
    assert save_offer(white, "czechia")["id"] != product["id"]


def test_rag_region_isolation_sources_and_reindex(client):
    save_offer(offer(), "czechia")
    save_offer(offer("https://shop.de/product", currency="EUR"), "germany")
    save_offer(offer(amount=1200), "czechia")
    documents = OfferRetriever(region="czechia").invoke("Headphones")
    assert len(documents) == 1
    assert documents[0].metadata["url"] == "https://shop.cz/product"
    assert "1200" in documents[0].page_content
    assert len(OfferRetriever(region="global").invoke("Headphones")) == 2


def test_global_refresh_does_not_erase_known_market_or_new_photo(client):
    product = save_offer(offer(), "czechia")
    refreshed = offer(amount=1100)
    refreshed["image_url"] = "https://shop.cz/image.jpg"
    save_offer(refreshed, "global")
    result = product_detail(product["id"], "czechia")
    assert result["minimum_prices"] == {"CZK": 1100}
    assert result["image_url"] == refreshed["image_url"]
    assert len(OfferRetriever(region="czechia").invoke("Headphones")) == 1


def test_manual_idea_and_api_validation(client, account):
    own = client.get("/api/wishlists").json()[0]["id"]
    assert client.post(f"/api/wishlists/{own}/items", json={"title": "  "}).status_code == 422
    assert (
        client.post(
            f"/api/wishlists/{own}/items", json={"title": "A grinder", "target_price": "100"}
        ).status_code
        == 422
    )
    assert client.post(f"/api/wishlists/{own}/items", json={"title": "A grinder"}).status_code == 201
    assert client.post("/api/search/stream", json={"query": "   "}).status_code == 422
    assert client.post("/api/search/stream", json={"query": "test", "region": "unknown"}).status_code == 422


def test_edit_target_and_notes_without_replacing_product(client, account):
    own = client.get("/api/wishlists").json()[0]["id"]
    product = save_offer(offer(), "czechia")
    item = client.post(f"/api/wishlists/{own}/items", json={"product_id": product["id"]}).json()["id"]
    response = client.patch(
        f"/api/wishlists/{own}/items/{item}",
        json={"notes": "Updated note", "target_price": "1000.00", "target_currency": "CZK"},
    )
    assert response.status_code == 200
    saved = client.get(f"/api/wishlists/{own}").json()["items"][0]
    assert saved["product_id"] == product["id"]
    assert saved["target_price"] == "1000.00"
    assert saved["notes"] == "Updated note"
    assert client.patch(f"/api/wishlists/{own}/items/{item}", json={"target_price": "-1"}).status_code == 422
    assert client.patch(f"/api/wishlists/{own}/items/{item}", json={"notes": "No target"}).status_code == 200
    assert client.get(f"/api/wishlists/{own}").json()["items"][0]["target_price"] is None
