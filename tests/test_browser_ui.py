"""Full UI journey against a real API; external store tools use deterministic fixtures."""

import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import uvicorn
from playwright.sync_api import expect, sync_playwright

from app import graph
from app.main import app


@pytest.mark.browser
def test_register_stream_compare_save_relogin(client, monkeypatch):
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
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.emulate_media(reduced_motion="reduce")
            artifacts = Path("data/ui-tests")
            artifacts.mkdir(parents=True, exist_ok=True)
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"http://127.0.0.1:{port}")
            page.screenshot(path=str(artifacts / "auth-desktop.png"), full_page=True)
            page.locator("#auth-toggle").click()
            page.locator("#auth-name").fill("Browser tester")
            page.locator("#auth-email").fill("browser@example.com")
            page.locator("#auth-password").fill("browser-test-password")
            page.locator("#auth-submit").click()
            expect(page.locator("#workspace")).to_be_visible()
            artifacts = Path("data/ui-tests")
            artifacts.mkdir(parents=True, exist_ok=True)
            expect(page.locator(".category-card")).to_have_count(6)
            page.get_by_role("button", name="Audio & headphones", exact=True).click()
            expect(page.locator("#category-ideas")).to_be_visible()
            page.get_by_role("button", name="Sony WH-1000XM5", exact=True).click()
            expect(page.locator("#query")).to_have_value("Sony WH-1000XM5")
            assert page.locator(".category-card img").evaluate_all(
                "(images)=>images.every(i=>i.complete && i.naturalWidth>0)"
            )
            page.screenshot(path=str(artifacts / "discover-desktop.png"), full_page=True)
            for width in (320, 390, 768, 1024):
                page.set_viewport_size({"width": width, "height": 900})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
                assert page.evaluate(
                    "document.querySelector('#nav').getBoundingClientRect().bottom <= document.querySelector('.topbar').getBoundingClientRect().bottom"
                ), width
            page.set_viewport_size({"width": 1440, "height": 1000})
            page.locator("#query").fill("Headphones")
            page.locator("#search-button").click()
            expect(page.locator("#status")).to_contain_text("Search complete", timeout=15000)
            expect(page.locator("#results .product-card")).to_have_count(1)
            page.evaluate("""() => {
                upsertProduct({id:'test-cheap',title:'Fixture headphones',minimum_prices:{CZK:50},offers:[],offer_count:1});
                upsertProduct({id:'test-euro',title:'Fixture euro headphones',minimum_prices:{EUR:1},offers:[],offer_count:1});
            }""")
            page.locator("#result-sort").select_option("price-low")
            expect(page.locator("#results .product-card").first).to_have_attribute(
                "data-product-id", "test-cheap"
            )
            expect(page.locator("#results .product-card").last).to_have_attribute(
                "data-product-id", "test-euro"
            )
            page.locator("#filter-currency").select_option("EUR")
            expect(page.locator("#results .product-card")).to_have_count(1)
            expect(page.locator("#results .product-card")).to_have_attribute("data-product-id", "test-euro")
            page.evaluate("""() => {
                streamEvent('product_removed',{product_id:'test-cheap'});
                streamEvent('product_removed',{product_id:'test-euro'});
            }""")
            page.locator("#filter-currency").select_option("CZK")
            page.locator("#filter-budget").fill("90")
            expect(page.locator("#results .product-card")).to_have_count(0)
            expect(page.locator("#results")).to_contain_text("No products match these filters")
            page.locator("#filter-budget").fill("110")
            expect(page.locator("#results .product-card")).to_have_count(1)
            page.locator("#filter-photos").check()
            expect(page.locator("#results .product-card")).to_have_count(0)
            page.locator("#filter-photos").uncheck()
            expect(page.locator("#results .product-card")).to_have_count(1)
            expect(page.locator("#browse-categories")).not_to_be_visible()
            page.locator("#browse-toggle").click()
            expect(page.locator("#browse-categories")).to_be_visible()
            page.locator("#browse-toggle").click()
            page.set_viewport_size({"width": 390, "height": 844})
            page.screenshot(path=str(artifacts / "results-mobile.png"), full_page=True)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.set_viewport_size({"width": 1440, "height": 1000})
            page.get_by_role("button", name="Compare ↗", exact=True).click()
            expect(page.locator(".offer-row")).to_have_count(2)
            page.screenshot(path=str(artifacts / "comparison-desktop.png"), full_page=True)
            page.get_by_role("button", name="+ Save to wishlist").click()
            page.locator('[name="notes"]').fill("Birthday present")
            page.locator('[name="target_price"]').fill("110")
            page.locator("#edit-form .primary").click()
            expect(page.locator("#edit-dialog")).not_to_be_visible()
            page.locator("#close-product").click()
            page.get_by_role("button", name="My wishlists", exact=True).click()
            expect(page.locator("#wishlist-items")).to_contain_text("Birthday present")
            expect(page.locator("#wishlist-items")).to_contain_text("Within your target")
            artifacts = Path("data/ui-tests")
            artifacts.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(artifacts / "wishlist-desktop.png"), full_page=True)
            page.locator("#logout").click()
            expect(page.locator("#auth-view")).to_be_visible()
            page.locator("#auth-toggle").click()
            page.locator("#auth-password").fill("browser-test-password")
            page.locator("#auth-submit").click()
            expect(page.locator("#workspace")).to_be_visible()
            page.get_by_role("button", name="My wishlists", exact=True).click()
            expect(page.locator("#wishlist-items .product-card")).to_have_count(1)
            page.set_viewport_size({"width": 390, "height": 844})
            page.screenshot(path=str(artifacts / "wishlist-mobile.png"), full_page=True)
            page.get_by_role("button", name="Discover", exact=True).click()
            page.screenshot(path=str(artifacts / "discover-mobile.png"), full_page=True)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert not errors, errors
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
