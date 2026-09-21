"""Run `python -m app.diagnostics` or opt in to a real-network smoke search."""

import argparse
import importlib.metadata
import json
import platform
import tempfile
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

from app.config import get_settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", metavar="QUERY", help="Run a real search in a temporary database")
    parser.add_argument("--region", default="czechia")
    parser.add_argument("--stores", type=int, default=4, choices=range(1, 16))
    parser.add_argument("--mode", choices=["quick", "thorough"], default="quick")
    parser.add_argument("--verbose", action="store_true", help="Print complete offer records")
    args = parser.parse_args()
    settings = get_settings()
    print("Python:", platform.python_version(), flush=True)
    for package in ("fastapi", "langgraph", "langchain-core", "langchain-ollama", "playwright"):
        print(f"{package}: {importlib.metadata.version(package)}")
    with sync_playwright() as browser:
        print("Chromium installed:", Path(browser.chromium.executable_path).is_file())
        try:
            instance = browser.chromium.launch(headless=True)
            instance.close()
            print("Chromium launch: OK")
        except Exception as error:
            print("Chromium launch failed:", type(error).__name__)
    try:
        response = httpx.get(settings.ollama_base_url.rstrip("/") + "/api/tags", timeout=5)
        response.raise_for_status()
        print("Ollama models:", ", ".join(item["name"] for item in response.json()["models"]))
    except (httpx.HTTPError, ValueError, KeyError) as error:
        print("Ollama unavailable:", type(error).__name__)
    if not args.live:
        return

    from app.database import initialize
    from app.graph import product_search_graph
    from app.schemas import SearchRequest

    request = SearchRequest(query=args.live, region=args.region, search_mode=args.mode)
    # Does not create test users or mix smoke-search data with real wishlists.
    with tempfile.TemporaryDirectory(prefix="wishwise-smoke-") as temporary:
        settings.database_path = str(Path(temporary) / "smoke.sqlite3")
        settings.store_limit = args.stores
        initialize()
        for event in product_search_graph.stream(request.model_dump(), stream_mode="custom"):
            if not args.verbose:
                payload = event["payload"]
                if event["event"] == "product":
                    product = payload["product"]
                    event = {
                        "event": "product",
                        "title": product["title"],
                        "minimum_prices": product["minimum_prices"],
                        "offer_count": product["offer_count"],
                    }
                elif event["event"] == "complete":
                    event = {
                        "event": "complete",
                        "product_count": len(payload["products"]),
                        "warnings": payload["warnings"],
                        "stores": payload["stores_searched"],
                    }
            print(json.dumps(event, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
