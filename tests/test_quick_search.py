from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app import agents, graph
from app.config import get_settings
from app.regions import get_region
from app.schemas import SearchRequest


def test_quick_discovery_uses_one_query_and_eight_stores(client, monkeypatch):
    calls = []
    monkeypatch.setattr(get_settings(), "store_limit", 15)

    def search(args):
        calls.append(args)
        return [
            {"url": f"https://store{i}.cz/sony-wh1000xm5", "title": "Sony WH-1000XM5", "snippet": ""}
            for i in range(20)
        ]

    monkeypatch.setattr(agents, "search_web", SimpleNamespace(invoke=search))
    stores, _ = agents.StoreDiscoveryAgent().run("Sony WH-1000XM5", get_region("czechia"), quick=True)
    assert len(calls) == 1
    assert len(stores) == 8
    assert get_settings().store_limit == 15


def test_quick_skips_planner_but_keeps_validation(client, monkeypatch):
    monkeypatch.setattr(
        agents.QueryPlannerAgent,
        "run",
        lambda *args: pytest.fail("Quick search must not wait for a planner model call"),
    )
    monkeypatch.setattr(
        agents.StoreDiscoveryAgent,
        "run",
        lambda *args: (
            [{"domain": "shop.cz", "pages": [{"url": "https://shop.cz/sony", "title": "Sony WH-1000XM5"}]}],
            [],
        ),
    )
    monkeypatch.setattr(graph, "search_store_catalog", SimpleNamespace(invoke=lambda args: []))

    def extract(args):
        return [
            {
                "title": title,
                "price": price,
                "url": f"https://shop.cz/{url}",
                "shop": "Store",
                "currency": "CZK",
            }
            for title, price, url in [
                ("Sony WH-1000XM5", 5000, "sony"),
                ("Unrelated vodka", 200, "vodka"),
                ("Sony WH-1000XM5", -1, "invalid"),
            ]
        ]

    monkeypatch.setattr(graph, "extract_offers", SimpleNamespace(invoke=extract))
    events = list(
        graph.product_search_graph.stream(
            {"query": "Sony WH-1000XM5", "region": "czechia", "max_results": 20, "search_mode": "quick"},
            stream_mode="custom",
        )
    )
    final = next(e["payload"] for e in events if e["event"] == "complete")
    assert [p["title"] for p in final["products"]] == ["Sony WH-1000XM5"]


def test_search_mode_is_validated():
    assert SearchRequest(query="Sony").search_mode == "thorough"
    with pytest.raises(ValidationError):
        SearchRequest(query="Sony", search_mode="unlimited")
