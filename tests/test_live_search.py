"""Opt-in network evaluations with independent brand/variant assertions and saved evidence.

Run: scripts\\test_app.bat -m live -s
Unlike unit fixtures, these can fail when a search provider or merchant is unavailable.
"""

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.config import get_settings
from app.database import initialize
from app.graph import product_search_graph


@pytest.mark.live
@pytest.mark.parametrize(
    "query,required",
    [
        ("lagavulin", [r"\blagavulin\b"]),
        ("Lagavulin 16", [r"\blagavulin\b", r"(?<!\d)16(?!\d)"]),
        ("Logitech G502", [r"\blogitech\b", r"\bg502\b"]),
    ],
)
def test_live_result_precision(query, required, tmp_path, monkeypatch):
    settings = get_settings()
    for key, value in {
        "database_path": str(tmp_path / "live.sqlite3"),
        "store_limit": 8,
        "use_llm_planner": True,
        "use_semantic_validation": True,
        "search_timeout_seconds": 120,
    }.items():
        monkeypatch.setattr(settings, key, value)
    initialize()
    started = time.monotonic()
    observations, final, first_product = [], None, None
    for event in product_search_graph.stream(
        {"query": query, "region": "czechia", "max_results": 20}, stream_mode="custom"
    ):
        if event["event"] == "product":
            if first_product is None:
                first_product = time.monotonic() - started
            observations.append(event["payload"]["product"])
        if event["event"] == "complete":
            final = event["payload"]
    violations = []
    for product in observations:
        # Explicit expectations, intentionally not the application's relevance function.
        for title in [product["title"]] + [offer["title"] for offer in product["offers"]]:
            if not all(re.search(pattern, title, re.I) for pattern in required):
                violations.append("Wrong brand/variant: " + title)
        if not product["minimum_prices"] or set(product["minimum_prices"]) != {"CZK"}:
            violations.append("No usable Czech price: " + product["title"])
    if not final or not final["products"]:
        violations.append("No products: this is not counted as a successful precision test")
    report = {
        "query": query,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "first_product_seconds": first_product,
        "elapsed_seconds": time.monotonic() - started,
        "violations": violations,
        "final": final,
        "streamed_products": observations,
    }
    directory = Path("data/evaluations")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (re.sub(r"[^a-z0-9]+", "-", query.lower()) + ".json")
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"\n{query}: {len(final['products']) if final else 0} products; first={first_product}; report={path}"
    )
    assert not violations, violations
