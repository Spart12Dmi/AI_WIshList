import time
from types import SimpleNamespace

import pytest

from app.agents import select_relevant_stores
from app.config import get_settings
from app.tools import web_search
from app.tools import browser_search


def test_failed_fast_provider_does_not_discard_later_success(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "search_backends", "failed,bing")
    monkeypatch.setattr(get_settings(), "search_cache_ttl_seconds", 0)
    monkeypatch.setattr(web_search, "is_public_http_url", lambda url: True)

    def search(query, backend, **kwargs):
        if backend == "failed":
            raise RuntimeError("Provider failed early")
        time.sleep(0.02)
        return [{"href": "https://shop.cz/lego-10307", "title": "LEGO 10307"}]

    monkeypatch.setattr(web_search, "DDGS", lambda **kwargs: SimpleNamespace(text=search))
    results = web_search._run_web_search("LEGO 10307 koupit", 3, "cz-cs")
    assert [row["url"] for row in results] == ["https://shop.cz/lego-10307"]


def test_foreign_results_do_not_consume_limit_before_regional_ranking(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "search_backends", "foreign,local")
    monkeypatch.setattr(get_settings(), "search_cache_ttl_seconds", 0)
    monkeypatch.setattr(web_search, "is_public_http_url", lambda url: True)

    def search(query, backend, **kwargs):
        domain = "shop.com" if backend == "foreign" else "shop.cz"
        return [{"href": f"https://{domain}/lego-10307", "title": "LEGO 10307"}]

    monkeypatch.setattr(web_search, "DDGS", lambda **kwargs: SimpleNamespace(text=search))
    results = web_search._run_web_search("LEGO 10307 koupit", 1, "cz-cs")
    assert results[0]["url"] == "https://shop.cz/lego-10307"


def test_fusion_deduplicates_sources_and_has_stable_order(monkeypatch):
    monkeypatch.setattr(web_search, "is_public_http_url", lambda url: True)
    shared = {"href": "https://shop.cz/lego-10307", "title": "LEGO 10307"}
    batches = {"a": [shared, shared], "b": [shared]}
    rows = web_search.rank_provider_results(batches, "LEGO 10307", "cz-cs")
    assert len(rows) == 1
    assert rows[0]["providers"] == "a,b"


def test_all_provider_failures_are_reported_not_cached(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "search_backends", "failed")
    monkeypatch.setattr(get_settings(), "search_cache_ttl_seconds", 0)
    monkeypatch.setattr(web_search, "DDGS", lambda **kwargs: SimpleNamespace(text=lambda *a, **kw: []))
    with pytest.raises(RuntimeError, match="No usable results"):
        web_search._run_web_search("No product", 3, "cz-cs")


def test_generic_fallback_recovers_when_configured_engines_are_empty(client, monkeypatch):
    """A provider outage must not hide a valid result from another DDGS engine."""
    monkeypatch.setattr(get_settings(), "search_backends", "failed,bing")
    monkeypatch.setattr(get_settings(), "search_cache_ttl_seconds", 0)
    monkeypatch.setattr(web_search, "is_public_http_url", lambda url: True)
    calls = []

    def search(query, backend, **kwargs):
        calls.append(backend)
        if backend == "duckduckgo":
            return [
                {
                    "href": "https://shop.cz/playstation-5-pro",
                    "title": "Sony PlayStation 5 Pro 2TB",
                    "body": "Herní konzole PlayStation 5 Pro",
                }
            ]
        return []

    monkeypatch.setattr(web_search, "DDGS", lambda **kwargs: SimpleNamespace(text=search))
    rows = web_search._run_web_search("PlayStation 5 Pro koupit", 3, "cz-cs")
    assert rows[0]["url"] == "https://shop.cz/playstation-5-pro"
    assert "duckduckgo" in calls


def test_browser_discovery_is_last_resort_after_search_engines_fail(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "search_backends", "failed")
    monkeypatch.setattr(get_settings(), "search_cache_ttl_seconds", 0)
    monkeypatch.setattr(get_settings(), "use_browser_fallback", True)
    monkeypatch.setattr(web_search, "is_public_http_url", lambda url: True)
    monkeypatch.setattr(web_search, "DDGS", lambda **kwargs: SimpleNamespace(text=lambda *a, **kw: []))
    monkeypatch.setattr(
        browser_search,
        "search_browser",
        lambda query, max_results, region: [
            {"url": "https://merchant.cz/product", "title": "A product", "snippet": ""}
        ],
    )
    rows = web_search._run_web_search("A product", 3, "cz-cs")
    assert rows[0]["url"] == "https://merchant.cz/product"


def test_browser_search_unwraps_engine_redirects(monkeypatch):
    monkeypatch.setattr(browser_search, "is_public_http_url", lambda url: True)
    assert browser_search._unwrap_search_link(
        "https://duckduckgo.com/l/?uddg=https%3A%2F%2Fmerchant.cz%2Fproduct",
        "https://html.duckduckgo.com/html/?q=product",
    ) == "https://merchant.cz/product"


def test_store_selection_preserves_upstream_relevance_instead_of_seo_word_counts():
    results = [
        {"url": "https://relevant.cz/lego-10307", "title": "LEGO 10307", "snippet": "17999 CZK"},
        {
            "url": "https://seo.cz/shop",
            "title": "LEGO shop buy store product price cena koupit",
            "snippet": "",
        },
    ]
    assert select_relevant_stores(results, limit=1) == [{"domain": "relevant.cz", "label": "relevant.cz"}]
