from types import SimpleNamespace

import pytest

from app import graph


def streamed_candidates(monkeypatch, offers):
    monkeypatch.setattr(
        graph.StoreDiscoveryAgent,
        "run",
        lambda *args: (
            [{"domain": "shop.cz", "pages": [{"url": "https://shop.cz/category", "title": "Lagavulin"}]}],
            [],
        ),
    )
    monkeypatch.setattr(graph, "search_store_catalog", SimpleNamespace(invoke=lambda args: []))
    monkeypatch.setattr(graph, "extract_offers", SimpleNamespace(invoke=lambda args: offers))
    return list(
        graph.product_search_graph.stream(
            {"query": "lagavulin", "region": "czechia", "max_results": 20}, stream_mode="custom"
        )
    )


def offer(title="Lagavulin 16", price=1600, source="https://shop.cz/lagavulin"):
    return {
        "title": title,
        "price": price,
        "currency": "CZK",
        "url": "https://shop.cz/lagavulin",
        "shop": "Shop",
        "source_url": source,
    }


@pytest.mark.parametrize("reverse", [False, True])
def test_rejected_candidate_does_not_poison_url_deduplication(client, monkeypatch, reverse):
    offers = [offer("Unrelated vodka"), offer()]
    events = streamed_candidates(monkeypatch, list(reversed(offers)) if reverse else offers)
    final = next(e["payload"] for e in events if e["event"] == "complete")
    assert len(final["products"]) == 1
    assert final["products"][0]["title"] == "Lagavulin 16"


@pytest.mark.parametrize("reverse", [False, True])
def test_detail_price_wins_over_category_observation_independent_of_arrival(client, monkeypatch, reverse):
    offers = [offer(price=1000, source="https://shop.cz/category"), offer(price=1600)]
    events = streamed_candidates(monkeypatch, list(reversed(offers)) if reverse else offers)
    final = next(e["payload"] for e in events if e["event"] == "complete")
    assert final["products"][0]["minimum_prices"] == {"CZK": 1600}
    assert final["products"][0]["offers"][0]["source_url"] == "https://shop.cz/lagavulin"


def test_corrected_identity_removes_obsolete_streamed_card(client, monkeypatch):
    offers = [offer(title="Lagavulin 8", source="https://shop.cz/category"), offer(title="Lagavulin 16")]
    events = streamed_candidates(monkeypatch, offers)
    final = next(e["payload"] for e in events if e["event"] == "complete")
    assert [product["title"] for product in final["products"]] == ["Lagavulin 16"]
    assert any(e["event"] == "product_removed" for e in events)
