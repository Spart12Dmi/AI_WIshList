import json
import threading
from types import SimpleNamespace

from app import graph


def mocked_search(monkeypatch):
    monkeypatch.setattr(
        graph.StoreDiscoveryAgent,
        "run",
        lambda *args: ([{"domain": "first.cz"}, {"domain": "second.cz"}], []),
    )
    monkeypatch.setattr(
        graph,
        "search_store_catalog",
        SimpleNamespace(
            invoke=lambda args: [{"title": "Example Headphones", "url": f"https://{args['domain']}/product"}]
        ),
    )
    monkeypatch.setattr(
        graph,
        "extract_offers",
        SimpleNamespace(
            invoke=lambda args: [
                {
                    "title": "Example Headphones Black",
                    "url": args["url"],
                    "price": 100 if "first" in args["url"] else 120,
                    "currency": "CZK",
                    "shop": args["url"].split("/")[2],
                }
            ]
        ),
    )


def test_real_langgraph_stream_persists_offers_and_history(client, account, monkeypatch):
    mocked_search(monkeypatch)
    response = client.post("/api/search/stream", json={"query": "Headphones", "region": "czechia"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = []
    for block in response.text.split("\n\n"):
        if block.startswith("event:"):
            name, data = block.split("\n", 1)
            events.append((name[7:], json.loads(data[6:])))
    assert [e[0] for e in events].count("product") == 2
    assert events[-1][0] == "complete"
    product = events[-1][1]["products"][0]
    assert product["minimum_prices"] == {"CZK": 100}
    assert product["offer_count"] == 2
    assert events[-1][1]["pipeline"][0] == "RAG retrieval"
    assert client.get("/api/history").json()[0]["status"] == "completed"
    assert client.get(f"/api/products/{product['id']}").status_code == 200


def test_first_product_emits_before_slow_store_finishes(client, monkeypatch):
    mocked_search(monkeypatch)
    fast_emitted = threading.Event()

    def extract(args):
        if "second" in args["url"]:
            assert fast_emitted.wait(5), "Pipeline waited for all stores before emitting a product"
        return [
            {"title": "Headphones Black", "url": args["url"], "price": 100, "currency": "CZK", "shop": "Test"}
        ]

    monkeypatch.setattr(graph, "extract_offers", SimpleNamespace(invoke=extract))
    for event in graph.product_search_graph.stream(
        {"query": "Headphones", "region": "czechia", "max_results": 20}, stream_mode="custom"
    ):
        if event["event"] == "product":
            fast_emitted.set()
    assert fast_emitted.is_set()


def test_store_failure_does_not_erase_other_offers(client, account, monkeypatch):
    mocked_search(monkeypatch)

    def search(args):
        if args["domain"] == "second.cz":
            raise TimeoutError("Unavailable")
        return [{"url": "https://first.cz/product", "title": "Headphones"}]

    monkeypatch.setattr(graph, "search_store_catalog", SimpleNamespace(invoke=search))
    response = client.post("/api/search", json={"query": "Headphones"})
    assert response.status_code == 200, response.text
    assert len(response.json()["products"]) == 1
    assert any("second.cz" in warning for warning in response.json()["warnings"])
