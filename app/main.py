import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.auth import current_user
from app.auth import router as auth_router
from app.catalog import product_detail
from app.config import get_settings
from app.database import connection, initialize
from app.graph import product_search_graph
from app.regions import REGIONS
from app.schemas import ProductRecord, SearchRequest, SearchResponse
from app.streaming import finish_run, start_run, stream_product_search
from app.wishlists import router as wishlist_router

logging.basicConfig(level=logging.INFO)
STATIC_ROOT = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app):
    initialize()
    app.state.search_slots = threading.BoundedSemaphore(get_settings().max_concurrent_searches)
    yield


app = FastAPI(title="Wishwise · Local AI wishlist", version="0.2.0", lifespan=lifespan)
app.include_router(auth_router)
app.include_router(wishlist_router)
app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")


@app.middleware("http")
async def security_headers(request, call_next):
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "Cross-origin requests are not allowed"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' https: http: data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
    )
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(STATIC_ROOT / "index.html")


@app.get("/api/regions")
def regions():
    return [{"key": k, "label": v.label} for k, v in REGIONS.items()]


@app.get("/api/health")
async def health():
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            response = await client.get(settings.ollama_base_url.rstrip("/") + "/api/tags")
            response.raise_for_status()
        models = [item["name"] for item in response.json().get("models", [])]
        ready = settings.ollama_model in models
    except (httpx.HTTPError, ValueError, KeyError):
        ready = False
    return {
        "status": "ok",
        "ollama": "ready" if ready else "model unavailable",
        "model": settings.ollama_model,
        "retrieval": "SQLite FTS5 / BM25",
        "version": app.version,
    }


@app.get("/api/products/{product_id}", response_model=ProductRecord)
def product(product_id: str, region: str = "global", user=Depends(current_user)):
    if region not in REGIONS:
        raise HTTPException(422, "Unknown region")
    detail = product_detail(product_id, region)
    if not detail:
        raise HTTPException(404, "Product not found")
    return detail


@app.get("/api/history")
def history(user=Depends(current_user)):
    with connection() as db:
        return [
            dict(row)
            for row in db.execute(
                "SELECT * FROM search_runs WHERE user_id=? ORDER BY started_at DESC LIMIT 30", (user["id"],)
            )
        ]


@app.post("/api/search/stream")
def search_stream(request: Request, data: SearchRequest, user=Depends(current_user)):
    slots = request.app.state.search_slots
    if not slots.acquire(blocking=False):
        raise HTTPException(429, "Search is busy. Wait a moment and try again.")
    return StreamingResponse(
        stream_product_search(
            data.query.strip(), data.region, data.max_results, user["id"], slots.release, data.search_mode
        ),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


@app.post("/api/search", response_model=SearchResponse)
def search(request: Request, data: SearchRequest, user=Depends(current_user)):
    slots = request.app.state.search_slots
    if not slots.acquire(blocking=False):
        raise HTTPException(429, "Search is busy. Try again shortly.")
    rid = None
    try:
        rid = start_run(user["id"], data.query, data.region)
        state = product_search_graph.invoke(data.model_dump())
        finish_run(rid, "completed", len(state.get("products", [])), state.get("warnings", []))
        return {k: v for k, v in state.items() if k not in {"control", "context"}} | {
            "run_id": rid,
            "stores_searched": [store["domain"] for store in state.get("stores", [])],
        }
    except Exception:
        if rid:
            finish_run(rid, "failed", 0, ["Search failed"])
        raise
    finally:
        slots.release()
