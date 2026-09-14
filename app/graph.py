"""One LangGraph workflow powers streaming and ordinary search requests."""

import logging
import queue
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TypedDict
from urllib.parse import urlsplit

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from app.agents import QueryPlannerAgent, SemanticValidationAgent, StoreDiscoveryAgent
from app.catalog import product_detail, save_offer
from app.config import get_settings
from app.matching import is_individual_product_url, query_evidence
from app.query_expansion import matches_query
from app.regions import get_region
from app.retrieval import OfferRetriever
from app.schemas import ProductOffer
from app.tools.browser_search import iter_browser_product_extractions
from app.tools.web_search import extract_offers, search_store_catalog

log = logging.getLogger(__name__)
_browser_slots = threading.BoundedSemaphore(2)
_llm_slot = threading.Lock()


class ProductSearchState(TypedDict, total=False):
    query: str
    region: str
    max_results: int
    search_mode: str
    planned_query: str
    query_variants: list[str]
    planner_diagnostics: dict[str, int | bool | str]
    context: str
    sources: list[dict]
    stores: list[dict]
    products: list[dict]
    warnings: list[str]
    pipeline: list[str]
    control: Any
    evaluation_trace: bool


def emit(event, **payload):
    get_stream_writer()({"event": event, "payload": payload})


def retrieve_context(state):
    emit("status", message="Retrieving relevant product evidence...")
    documents = OfferRetriever(region=state["region"]).invoke(state["query"])
    sources = [
        {
            "url": d.metadata["url"],
            "checked_at": d.metadata["checked_at"],
            "title": d.metadata.get("title", ""),
        }
        for d in documents
        if query_evidence(state["query"], d.metadata.get("title", ""))
    ]
    emit("retrieval", sources=sources)
    return {
        "context": "\n".join(d.page_content + " Source: " + d.metadata["url"] for d in documents),
        "sources": sources,
        "pipeline": ["RAG retrieval"],
    }


def plan_query(state):
    emit("status", message="Planning search for your region...")
    with _llm_slot:
        if state.get("control") and state["control"].is_set():
            return {"planned_query": state["query"], "warnings": ["Search cancelled."]}
        planner = QueryPlannerAgent()
        query, warnings = planner.run(
            state["query"], get_region(state["region"]), state["context"]
        )
    query = re.sub(r"\bsite:\S+", "", query)[:300]
    variants = getattr(planner, "variants", [state["query"]])
    diagnostics = getattr(planner, "diagnostics", {})
    emit("queries", queries=variants)
    if diagnostics:
        emit("planner", **diagnostics)
    emit("status", message=f"Search query: {query}")
    return {
        "planned_query": query,
        "query_variants": variants,
        "planner_diagnostics": diagnostics,
        "warnings": warnings,
        "pipeline": state["pipeline"] + ["Structured query planning"],
    }


def discover_stores(state):
    if state.get("control") and state["control"].is_set():
        return {"stores": [], "warnings": state.get("warnings", [])}
    emit("status", message="Finding stores in the selected market...")
    discovery_args = [state["planned_query"], get_region(state["region"]), state["query"]]
    discovery_args.extend([state.get("search_mode") == "quick", state.get("query_variants")])
    stores, warnings = StoreDiscoveryAgent().run(*discovery_args)
    known = {}
    for source in state["sources"]:
        domain = (urlsplit(source["url"]).hostname or "").removeprefix("www.")
        if domain:
            known.setdefault(domain, {"domain": domain, "label": domain, "pages": []})["pages"].append(
                {"url": source["url"], "title": source["title"]}
            )
    # Previously verified source URLs are useful search seeds, not current prices.
    # Refresh these exact pages first, instead of discarding them and asking the
    # unstable search provider to rediscover the same merchant.
    for store in stores:
        if store["domain"] in known:
            known[store["domain"]]["pages"].extend(store.get("pages", []))
    stores = (list(known.values()) + [s for s in stores if s["domain"] not in known])[
        : min(get_settings().store_limit, 8)
        if state.get("search_mode") == "quick"
        else get_settings().store_limit
    ]
    emit("stores", stores=[s["domain"] for s in stores])
    return {
        "stores": stores,
        "warnings": state["warnings"] + warnings,
        "pipeline": state["pipeline"] + ["Store discovery"],
    }


def search_and_validate(state):
    """Workers search then extract each store; fast stores do not wait for slower ones."""
    settings = get_settings()
    quick = state.get("search_mode") == "quick"
    page_limit = min(settings.per_store_product_limit, 2) if quick else settings.per_store_product_limit
    region = get_region(state["region"])
    stop = state.get("control") or threading.Event()
    deadline = time.monotonic() + (
        min(settings.search_timeout_seconds, 60) if quick else settings.search_timeout_seconds
    )
    events = queue.Queue()
    browser_budget = min(settings.browser_candidate_limit, 2) if quick else settings.browser_candidate_limit
    budget_lock = threading.Lock()
    warnings = list(state["warnings"])
    products = {}
    variants = state.get("query_variants", [])

    def audit(candidate, outcome):
        if state.get("evaluation_trace"):
            emit("evaluation", candidate=candidate, outcome=outcome)

    def candidate_pages(store, progress):
        seen_pages = set()
        for page in store.get("pages", []):
            if page["url"] not in seen_pages:
                seen_pages.add(page["url"])
                yield page
            if len(seen_pages) >= page_limit:
                break
        if progress["matched"]:
            return
        if stop.is_set() or time.monotonic() > deadline:
            return
        pages = search_store_catalog.invoke(
            {
                "domain": store["domain"],
                "query": state["query"],
                "region": region.search_region,
                "max_results": page_limit,
                "queries": state.get("query_variants", [state["query"]])[:3 if quick else 4],
            }
        )
        pages.sort(key=lambda page: not matches_query(state["query"], page.get("title", ""), variants))
        catalogue_count = 0
        for page in pages:
            if page["url"] not in seen_pages:
                seen_pages.add(page["url"])
                catalogue_count += 1
                yield page
            if catalogue_count >= page_limit:
                return

    def worker(store):
        nonlocal browser_budget
        domain = store["domain"]
        extracted = 0
        progress = {"matched": 0}
        try:
            if stop.is_set():
                return
            events.put(("store", {"domain": domain, "status": "searching"}))
            for page in candidate_pages(store, progress):
                if stop.is_set() or time.monotonic() > deadline:
                    break
                try:
                    offers = extract_offers.invoke(
                        {"url": page["url"], "discovered_title": page["title"], "query": state["query"], "queries": variants}
                    )
                except Exception as exc:
                    log.info("HTTP extraction failed for %s: %s", domain, type(exc).__name__)
                    offers = []
                use_browser = False
                with budget_lock:
                    if (
                        not any(matches_query(state["query"], item.get("title", ""), variants) for item in offers)
                        and settings.use_browser_fallback
                        and browser_budget > 0
                    ):
                        browser_budget -= 1
                        use_browser = True
                if use_browser and not stop.is_set() and time.monotonic() < deadline:
                    with _browser_slots:
                        if not stop.is_set() and time.monotonic() < deadline:
                            try:
                                offers = [
                                    p
                                    for p in iter_browser_product_extractions(
                                        [{**page, "query": state["query"], "queries": variants}]
                                    )
                                    if p.get("accepted")
                                ]
                            except Exception as exc:
                                events.put(
                                    (
                                        "warning",
                                        {
                                            "message": f"Browser extraction failed for {domain}: {type(exc).__name__}"
                                        },
                                    )
                                )
                for offer in offers:
                    if not stop.is_set():
                        if matches_query(state["query"], offer.get("title", ""), variants):
                            progress["matched"] += 1
                        events.put(("candidate", offer))
                        extracted += 1
            events.put(
                (
                    "store",
                    {
                        "domain": domain,
                        "status": f"{extracted} candidate offers" if extracted else "no readable offers",
                    },
                )
            )
        except Exception as exc:
            log.info("Store failed %s: %s", domain, type(exc).__name__)
            events.put(
                ("warning", {"message": f"Store search unavailable for {domain}: {type(exc).__name__}"})
            )
        finally:
            events.put(("worker_done", {}))

    stores = state["stores"]
    if not stores:
        return {"products": [], "warnings": warnings}
    emit("status", message=f"Searching {len(stores)} stores; matched products appear as they finish...")
    seen = {}
    completed = 0
    with ThreadPoolExecutor(max_workers=max(1, min(settings.store_search_workers, len(stores)))) as pool:
        for store in stores:
            pool.submit(worker, store)
        while completed < len(stores):
            try:
                kind, payload = events.get(timeout=1)
            except queue.Empty:
                continue
            if kind == "worker_done":
                completed += 1
            elif kind == "candidate" and not stop.is_set() and time.monotonic() < deadline:
                try:
                    offer = ProductOffer.model_validate(payload).model_dump()
                    quality = 1 if not offer["source_url"] or offer["source_url"] == offer["url"] else 0
                    if seen.get(offer["url"], -1) >= quality:
                        continue
                    if not is_individual_product_url(offer["url"]):
                        audit(offer, "homepage_instead_of_product_url")
                        continue
                    if not matches_query(state["query"], offer["title"], variants):
                        audit(offer, "query_mismatch")
                        warnings.append(
                            "Some candidates were excluded because their category or model did not match the query."
                        )
                        continue
                    with _llm_slot:
                        accepted, notes = SemanticValidationAgent().run([offer], state["query"], region, variants)
                    warnings.extend(note for note in notes if note not in warnings)
                    if not accepted:
                        audit(offer, "semantic_or_region_rejection")
                        continue
                    product = save_offer(accepted[0], state["region"], query=state["query"])
                    # A richer/corrected observation may reassign a URL to a
                    # new identity. Remove it from earlier streamed cards too.
                    for previous_id, previous in list(products.items()):
                        if previous_id != product["id"] and any(
                            item["url"] == offer["url"] for item in previous["offers"]
                        ):
                            refreshed = product_detail(previous_id, state["region"])
                            if refreshed and refreshed["minimum_prices"]:
                                products[previous_id] = refreshed
                                emit("product", product=refreshed, provisional=False)
                            else:
                                del products[previous_id]
                                emit("product_removed", product_id=previous_id)
                    # Rejected observations cannot reserve a URL. A detail-page
                    # observation may replace a category observation, independent
                    # of worker arrival order, but not the other way around.
                    seen[offer["url"]] = quality
                    if not matches_query(state["query"], product["title"], variants) or not product["minimum_prices"]:
                        audit(offer, "no_available_minimum_or_identity_mismatch")
                        if product["id"] in products:
                            del products[product["id"]]
                            emit("product_removed", product_id=product["id"])
                        continue
                    if product["id"] in products or len(products) < state["max_results"]:
                        audit(offer, "accepted")
                        products[product["id"]] = product
                        emit("product", product=product, provisional=False)
                except (ValueError, TypeError):
                    audit(payload, "invalid_output")
                    warnings.append("An offer failed output validation and was excluded.")
            elif kind == "warning":
                warnings.append(payload["message"])
                emit("warning", **payload)
            elif kind == "store":
                emit("store", **payload)
    if time.monotonic() > deadline:
        warnings.append("Search time budget reached; showing the offers collected so far.")
    return {
        "products": list(products.values()),
        "warnings": list(dict.fromkeys(warnings)),
        "pipeline": state["pipeline"]
        + [
            "Parallel store search + browser",
            "Structured output validation",
            "Semantic matching",
            "Catalogue persistence",
        ],
    }


def finalize(state):
    emit(
        "complete",
        query=state["query"],
        region=state["region"],
        products=state.get("products", []),
        warnings=state.get("warnings", []),
        pipeline=state.get("pipeline", []),
        sources=state.get("sources", []),
        stores_searched=[s["domain"] for s in state.get("stores", [])],
        planner_diagnostics=state.get("planner_diagnostics", {}),
    )
    return {}


def build_product_graph():
    builder = StateGraph(ProductSearchState)
    nodes = [
        ("retrieve_context", retrieve_context),
        ("plan_query", plan_query),
        ("discover_stores", discover_stores),
        ("search_and_validate", search_and_validate),
        ("finalize", finalize),
    ]
    previous = START
    for name, function in nodes:
        builder.add_node(name, function)
        builder.add_edge(previous, name)
        previous = name
    builder.add_edge(previous, END)
    return builder.compile()


product_search_graph = build_product_graph()
