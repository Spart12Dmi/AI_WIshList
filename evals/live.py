"""Opt-in actual web search: preserve results, rejection evidence and repeat stability.

python -m evals.live --queries lagavulin logitech sony --repeat 2
This is a network-dependent coverage check, not a claim of full-market recall.
"""

import argparse
import json
import re
import tempfile
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from app.config import get_settings
from app.database import initialize
from app.graph import product_search_graph


def violations_for(case, products):
    problems = []
    owners = {}
    if not products:
        problems.append("zero_results")
    for index, product in enumerate(products):
        titles = [product["title"]] + [offer["title"] for offer in product["offers"]]
        for title in titles:
            if not all(re.search(pattern, title, re.I) for pattern in case["required"]):
                problems.append("wrong_brand_or_model: " + title)
            if re.search(
                r"\b(rental|rent|hire|půjčovna|pujcovna|pronájem|pronajem|mieten|verleih)\b", title, re.I
            ):
                problems.append("rental_not_purchase: " + title)
        seen_urls = set()
        variants = set()
        for offer in product["offers"]:
            owner = product.get("id", str(index))
            if offer["url"] in owners and owners[offer["url"]] != owner:
                problems.append("offer_in_multiple_product_cards: " + offer["url"])
            owners[offer["url"]] = owner
            parsed = urlsplit(offer["url"])
            if offer.get("mpn"):
                variants.add(offer["mpn"].casefold())
            elif case.get("variant_code_url_pattern"):
                code = re.search(case["variant_code_url_pattern"], parsed.path, re.I)
                if code:
                    variants.add(code[1].casefold())
            if re.search(r"\b(rental|rent|hire|pujcovna|pronajem|mieten|verleih)\b", parsed.path, re.I):
                problems.append("rental_product_link: " + offer["url"])
            if re.search(r"(?:^|&)(?:utm_[^=]*|srsltid|gclid|fbclid|msclkid)=", parsed.query, re.I):
                problems.append("tracking_product_link: " + offer["url"])
            if re.fullmatch(r"product(?:[-_]\d+)?", parsed.fragment, re.I):
                problems.append("schema_identity_product_link: " + offer["url"])
            if offer["url"] in seen_urls:
                problems.append("duplicate_offer_url: " + offer["url"])
            seen_urls.add(offer["url"])
            if not parsed.query and (
                not parsed.path.strip("/") or re.fullmatch(r"/?[a-z]{2}(?:-[a-z]{2})?/?", parsed.path, re.I)
            ):
                problems.append("homepage_product_link: " + offer["url"])
            for pattern in case.get("forbidden_url_patterns", []):
                if re.search(pattern, parsed.path, re.I):
                    problems.append("contradictory_product_link: " + offer["url"])
        if len(variants) > 1:
            problems.append("conflicting_manufacturer_variants: " + product["title"])
        if set(product["minimum_prices"]) != {case["currency"]}:
            problems.append("missing_or_wrong_currency_minimum: " + product["title"])
    return list(dict.fromkeys(problems))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queries", nargs="+", default=["lagavulin", "logitech", "sony"])
    parser.add_argument("--repeat", type=int, choices=range(1, 6), default=1)
    parser.add_argument("--stores", type=int, choices=range(1, 16), default=6)
    parser.add_argument("--model")
    parser.add_argument("--mode", choices=["quick", "thorough"], default="thorough")
    parser.add_argument(
        "--cold-search",
        action="store_true",
        help="Disable the URL discovery cache to measure provider variability",
    )
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args()
    cases = json.loads(Path(__file__).with_name("live_queries.json").read_text(encoding="utf-8"))
    unknown = set(args.queries) - {case["id"] for case in cases}
    if unknown:
        parser.error("Unknown query IDs: " + ", ".join(sorted(unknown)))
    settings = get_settings()
    settings.store_limit = args.stores
    settings.search_timeout_seconds = args.timeout
    if args.cold_search:
        settings.search_cache_ttl_seconds = 0
    if args.model:
        settings.ollama_model = args.model
    settings.use_llm_planner = settings.use_semantic_validation = True
    folder = Path("data/evaluations") / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ-live")
    folder.mkdir(parents=True)
    summaries, review_rows = [], []
    for repetition in range(1, args.repeat + 1):
        for case in cases:
            if case["id"] not in args.queries:
                continue
            started = time.monotonic()
            events, streamed, final, first = [], [], None, None
            error = None
            # Fresh DB for EVERY query/repeat: RAG from an earlier run must not
            # silently inflate repeat stability. Never reads the user's database.
            with tempfile.TemporaryDirectory(prefix="wishwise-eval-") as temp:
                settings.database_path = str(Path(temp) / "offers.sqlite3")
                initialize()
                try:
                    for event in product_search_graph.stream(
                        {
                            "query": case["query"],
                            "region": case["region"],
                            "max_results": 20,
                            "search_mode": args.mode,
                            "evaluation_trace": True,
                        },
                        stream_mode="custom",
                    ):
                        elapsed = round(time.monotonic() - started, 3)
                        events.append({"seconds": elapsed, **event})
                        kind, payload = event["event"], event["payload"]
                        if kind == "product":
                            first = first if first is not None else elapsed
                            streamed.append(payload["product"])
                            print(case["id"], repetition, elapsed, payload["product"]["title"], flush=True)
                        elif kind == "complete":
                            final = payload
                        elif kind == "status":
                            print(case["id"], repetition, payload["message"], flush=True)
                        elif kind == "evaluation":
                            review_rows.append(
                                {
                                    "query": case["query"],
                                    "region": case["region"],
                                    **payload,
                                    "human_relevant": None,
                                    "human_expected": None,
                                }
                            )
                except Exception as exc:
                    error = type(exc).__name__ + ": " + str(exc)[:300]
            products = final.get("products", []) if final else []
            problems = violations_for(case, products)
            # Validate every streamed card too, not just final cards after removal.
            for streamed_product in streamed:
                problems.extend(violations_for(case, [streamed_product]))
            if error:
                problems.append(error)
            summary = {
                "query_id": case["id"],
                "search_mode": args.mode,
                "repeat": repetition,
                "model": settings.ollama_model,
                "discovery_cache_ttl_seconds": settings.search_cache_ttl_seconds,
                "store_limit": min(settings.store_limit, 8) if args.mode == "quick" else settings.store_limit,
                "search_backends": settings.search_backends,
                "browser_candidate_limit": min(settings.browser_candidate_limit, 2)
                if args.mode == "quick"
                else settings.browser_candidate_limit,
                "count": len(products),
                "first_product_seconds": first,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "violations": sorted(set(problems)),
                "urls": sorted({offer["url"] for p in products for offer in p["offers"]}),
            }
            report = {"case": case, "summary": summary, "final": final, "events": events}
            (folder / f"{case['id']}-{repetition}.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            summaries.append(summary)
            print("RESULT", json.dumps(summary, ensure_ascii=False), flush=True)
    groups = defaultdict(list)
    for result in summaries:
        groups[result["query_id"]].append(result)
    stability = {}
    for query, results in groups.items():
        sets = [set(result["urls"]) for result in results]
        union = set.union(*sets)
        stability[query] = {
            "counts": [result["count"] for result in results],
            "all_repeat_url_jaccard": len(set.intersection(*sets)) / len(union)
            if len(sets) > 1 and union
            else None,
        }
    (folder / "summary.json").write_text(
        json.dumps({"runs": summaries, "stability": stability}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (folder / "candidates-for-human-review.json").write_text(
        json.dumps(review_rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("Reports:", folder)
    return 1 if any(result["violations"] for result in summaries) else 0


if __name__ == "__main__":
    raise SystemExit(main())
