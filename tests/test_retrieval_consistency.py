from app.catalog import save_offer
from app.retrieval import OfferRetriever


def store(title, url):
    save_offer({"title": title, "url": url, "shop": "Shop", "price": 1000, "currency": "CZK"}, "czechia")


def test_retrieval_filters_accessory_before_top_k(client):
    store("Replacement cable for Logitech G502", "https://shop.cz/cable")
    store("Logitech G502 HERO High Performance Gaming Mouse Black", "https://other.cz/mouse")
    documents = OfferRetriever(k=1).invoke("Logitech G502")
    assert [doc.metadata["url"] for doc in documents] == ["https://other.cz/mouse"]


def test_repeated_retrieval_is_stable_and_diversifies_merchant_seeds(client):
    for age in range(8, 14):
        store(f"Lagavulin {age}", f"https://a.cz/lagavulin-{age}")
    store("Lagavulin 16", "https://b.cz/lagavulin-16")
    store("Lagavulin 12", "https://c.cz/lagavulin-12")
    retriever = OfferRetriever(k=3)
    first = [doc.metadata["url"] for doc in retriever.invoke("Lagavulin")]
    second = [doc.metadata["url"] for doc in retriever.invoke("Lagavulin")]
    assert first == second
    assert {url.split("/")[2] for url in first} == {"a.cz", "b.cz", "c.cz"}
