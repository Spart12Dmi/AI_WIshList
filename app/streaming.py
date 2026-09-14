"""SSE transport for LangGraph, with heartbeats and disconnect cleanup."""

import asyncio
import json
import logging
import queue
import threading
import time
import uuid

from app.database import connection
from app.graph import product_search_graph

log = logging.getLogger(__name__)


def encode_sse(event, payload):
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False, allow_nan=False)}\n\n"


def start_run(user_id, query, region):
    rid = uuid.uuid4().hex
    with connection() as db:
        db.execute(
            "INSERT INTO search_runs(id,user_id,query,region,status,started_at) VALUES(?,?,?,?,?,?)",
            (rid, user_id, query, region, "running", time.time()),
        )
    return rid


def finish_run(rid, status, count, warnings):
    with connection() as db:
        db.execute(
            "UPDATE search_runs SET status=?,finished_at=?,result_count=?,warnings=? WHERE id=?",
            (status, time.time(), count, json.dumps(warnings), rid),
        )


async def stream_product_search(query, region_key, max_results, user_id, release, search_mode="thorough"):
    outgoing = queue.Queue()
    stop = threading.Event()
    try:
        rid = start_run(user_id, query, region_key)
    except Exception:
        release()
        raise

    def produce():
        count, notes, status = 0, [], "failed"
        product_ids = set()
        try:
            for event in product_search_graph.stream(
                {
                    "query": query,
                    "region": region_key,
                    "max_results": max_results,
                    "search_mode": search_mode,
                    "control": stop,
                    "warnings": [],
                    "pipeline": [],
                },
                stream_mode="custom",
            ):
                if stop.is_set():
                    continue
                if event["event"] == "product":
                    product_ids.add(event["payload"]["product"]["id"])
                    count = len(product_ids)
                if event["event"] == "warning":
                    notes.append(event["payload"]["message"])
                if event["event"] == "complete":
                    count = len(event["payload"]["products"])
                    notes = event["payload"]["warnings"]
                    status = "completed"
                    event["payload"]["run_id"] = rid
                outgoing.put(event)
        except Exception:
            log.exception("Search failed: %s", rid)
            outgoing.put(
                {
                    "event": "error",
                    "payload": {
                        "message": "Search interrupted. Already saved offers remain available.",
                        "run_id": rid,
                    },
                }
            )
        finally:
            try:
                finish_run(rid, "cancelled" if stop.is_set() else status, count, notes)
            finally:
                outgoing.put(None)
                release()

    threading.Thread(target=produce, name=f"search-{rid[:8]}", daemon=True).start()
    try:
        yield encode_sse("started", {"run_id": rid})
        while True:
            try:
                event = await asyncio.to_thread(outgoing.get, True, 5)
            except queue.Empty:
                yield ": heartbeat\n\n"
                continue
            if event is None:
                break
            yield encode_sse(event["event"], event["payload"])
    finally:
        stop.set()
