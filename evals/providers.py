"""Compare public search backends before changing discovery policy.

python -m evals.providers --queries lego coffee --repeat 2
All output is public result metadata. Nothing is posted to merchant sites.
"""

import argparse
import json
import re
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from ddgs import DDGS

from app.regions import get_region


def probe(case, provider, variant, repetition):
    region = get_region(case["region"])
    query = (
        f"{case['query']} {region.shopping_terms.split()[-1]}"
        if variant == "local-word"
        else f'"{case["query"]}" site:{region.country_domains[0]}'
    )
    started = time.monotonic()
    result = {
        "id": case["id"],
        "provider": provider,
        "variant": variant,
        "query": query,
        "repeat": repetition,
        "results": [],
        "error": None,
    }
    try:
        rows = DDGS(timeout=8).text(query, region=region.search_region, max_results=30, backend=provider)
        for row in rows:
            url = str(row.get("href") or row.get("url") or "")
            host = (urlsplit(url).hostname or "").lower()
            text = str(row.get("title", "")) + " " + str(row.get("body", ""))
            result["results"].append(
                {
                    "url": url,
                    "title": row.get("title", ""),
                    "snippet": row.get("body", ""),
                    "regional": any(host.endswith(suffix) for suffix in region.country_domains),
                    "anchor_match": all(re.search(pattern, text, re.I) for pattern in case["required"]),
                }
            )
    except Exception as exc:
        result["error"] = type(exc).__name__ + ": " + str(exc)[:200]
    result["seconds"] = round(time.monotonic() - started, 3)
    result["regional_matching_count"] = sum(
        row["regional"] and row["anchor_match"] for row in result["results"]
    )
    print(
        case["id"],
        provider,
        variant,
        repetition,
        result["regional_matching_count"],
        result["error"] or "OK",
        flush=True,
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queries", nargs="+", default=["lego", "coffee"])
    parser.add_argument(
        "--providers", nargs="+", default=["google", "bing", "brave", "yahoo", "yandex", "mojeek"]
    )
    parser.add_argument("--repeat", type=int, choices=range(1, 4), default=1)
    args = parser.parse_args()
    cases = json.loads(Path(__file__).with_name("live_queries.json").read_text(encoding="utf-8"))
    if set(args.queries) - {case["id"] for case in cases}:
        parser.error("Unknown query ID")
    rows = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [
            pool.submit(probe, case, provider, variant, repetition)
            for repetition in range(1, args.repeat + 1)
            for case in cases
            if case["id"] in args.queries
            for provider in args.providers
            for variant in ("local-word", "quoted-country")
        ]
        for future in as_completed(futures):
            rows.append(future.result())
    summary = defaultdict(
        lambda: {"calls": 0, "errors": 0, "regional_matching_results": 0, "zero_matching_calls": 0}
    )
    for row in rows:
        item = summary[row["provider"]]
        item["calls"] += 1
        item["errors"] += bool(row["error"])
        item["regional_matching_results"] += row["regional_matching_count"]
        item["zero_matching_calls"] += row["regional_matching_count"] == 0
    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "runs": rows,
        "summary": dict(summary),
        "scope": "Anchor/region checks on discovery results, not verified merchant offers",
    }
    path = Path("data/evaluations") / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-providers.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2), "\nReport:", path)


if __name__ == "__main__":
    main()
