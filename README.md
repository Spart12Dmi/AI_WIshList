# Wantnote — local AI product discovery & wishlists

A local-first web application: accounts, regional product search, comparison of
merchant offers, and private wishlists. Product cards arrive progressively after
validation. No paid model API is required.

## Run on Windows

Already have the environment from the previous version? Stop the server with
Ctrl+C and restart:

```bat
scripts\run_app.bat
```

Open **http://127.0.0.1:8000**, then choose **Create an account**. Passwords require
at least 10 characters. The server stays in the terminal; Ctrl+C stops it.
SQLite initializes automatically. There are no default passwords or demo accounts.

Fresh install: install Miniconda and [Ollama](https://ollama.com/download/windows),
then use a terminal where `conda` is available:

```bat
scripts\setup_conda_env.bat --pull-model --install-browser
scripts\run_app.bat
```

Setup uses Python 3.11, PyTorch 2.10.0 with CUDA 12.8 wheels and `qwen3:4b`.
For an existing installation, run `ollama pull qwen3:4b` once before restarting.
The app uses a 4096-token context with thinking disabled. `scripts\run_qwen.bat`
explicitly selects Qwen even if an older `.env` selects Llama. To keep Llama,
set `OLLAMA_MODEL=llama3.2:3b` in `.env` instead; it is not uninstalled.
Ollama runs inference in its own runtime: PyTorch's CUDA installation does not
control Ollama's GPU use. Its usual Windows path is
`%LOCALAPPDATA%\Programs\Ollama\ollama.exe`; setup also checks that path.

Optional: copy `.env.example` to `.env` with your editor. Existing `.env` files
are never overwritten. To change models, download the model and set `OLLAMA_MODEL`
to the same name. Setup's `--model` flag selects the download, not application config.

Batch scripts set `PYTHONNOUSERSITE=1` so unrelated packages in the Windows user
profile cannot override Conda. If upgrading on another PC, rerun setup to install
all dependencies in that environment. HTTPS uses a system trust context (or an
explicit `SSL_CERT_FILE`/`SSL_CERT_DIR`) and never disables certificate validation.
See [HTTPX SSL configuration](https://www.python-httpx.org/advanced/ssl/).

## Features

- Registration/login/logout with revocable cookie sessions; persistent private
  wishlists and per-account search history.
- Create/rename/delete lists, save products, add unpriced ideas, edit notes and
  target prices, and remove items.
- Regional discovery of up to 15 stores, bounded parallel per-store searches,
  structured HTTP extraction and Chromium rendering fallback.
- SSE product updates, progress, source links, warnings, cancellation and heartbeats.
- Product details with shop links, images when supplied, stock status, observation
  timestamps and minima **per currency**, never an invalid EUR-versus-CZK comparison.
- Grouping by GTIN, brand + manufacturer part number (MPN), or exact normalized
  title, preserving explicit size/colour. MPNs are displayed in comparison cards.
  Missing identifiers still limit confidence in cross-shop variant matching.
- The semantic validator also returns a reviewed canonical product identity for
  cross-store grouping. Cosmetic colour/condition wording can be removed while
  model, edition, capacity, size, strength and pack details stay distinct.
- Scoped Schema.org and Shoptet microdata extraction can produce multiple items
  from one category. Where metadata is absent, bounded product-card navigation
  opens individual pages. A separate price-source link identifies category-based
  observations instead of pretending the price came from the detail page.

## AI architecture

Both JSON search and the streaming UI execute the **same compiled LangGraph**.

```text
Authenticated request (Pydantic)
  -> RAG (LangChain BaseRetriever, SQLite FTS5/BM25)
  -> Local ChatOllama -> structured QueryPlan
  -> Plan-content validation / deterministic fallback
  -> Regional discovery -> search_web tool
  -> Parallel per-store workers
       -> search_store_catalog tool
       -> structured page extraction / Chromium
       -> ProductOffer schema + URL/price/currency validation
       -> structured LLM assessment + deterministic evidence gates
       -> persist offer, group product, emit SSE immediately
  -> Final results and persisted run history
```

LangChain provides real tools, documents, a retriever and `ChatOllama`. LangGraph
owns workflow execution and custom streaming. Store workers are bounded Python
workers **inside a graph node**, not independent GPU models. Model calls are
serialized to avoid exhausting laptop GPU memory.

### RAG and structured generation

Accepted public product observations form the retrieval corpus. SQLite FTS5/BM25
retrieves region-specific documents with source URLs and timestamps for subsequent
query planning. This is **lexical RAG**, not vector/embedding retrieval or
fine-tuning. A fresh database correctly has no retrieved sources. Private account
details and wishlist notes never enter this shared corpus.

Historical prices are not re-emitted as new search results; pages must be fetched
again. Saved observations older than 24 hours remain visible but do not determine
the current minimum. Prices are decimal strings in storage, numeric in API output.

Ollama JSON-schema output generates a `QueryPlan`, one independent `RewriteReview`
per proposed phrase, and single-candidate `ProductMatch` decisions. The model does
not generate/copy source URLs or decide prices/regions.
Pydantic checks types, extra fields and finite numeric prices. Additional gates
reject invented numeric query constraints, accessories instead of main products,
generic pages, regional mismatches and missing original-query identity/variant terms. Failures
produce explicit warnings and a deterministic fallback. The LLM never invents
prices or executes SQL. Product/category translations are proposed and reviewed
by the local model; deterministic matching only checks the original request and
those approved variants.

Every search also exposes a privacy-safe planner diagnostic: whether the local
model or direct fallback ran, how many candidate rewrites were reviewed, and how
many were approved or rejected. It contains counts only (never prompts, model
reasons, account data or source URLs), and is available as the `planner` SSE event
and `planner_diagnostics` field in the JSON response. This makes a zero-result
search debuggable without treating model output as trusted evidence.

Search discovery URLs (not prices) are cached in memory for five minutes, scoped
by query, region and provider settings. Product pages are still fetched on every
search. Set `SEARCH_CACHE_TTL_SECONDS=0` to disable this. Relevant historical RAG
source URLs are refetched first, avoiding needless rediscovery of a known page.
Category navigation filters product links before applying the page budget.
Search providers are queried independently with bounded concurrency, then fused
and ranked for the selected region **before** truncation. A failing provider does
not discard another provider's results. Discovery rank is preserved when selecting
stores; repeated shopping keywords do not boost a merchant.

Purchase searches reject rental/day-rate listings. Offer URLs use the same
structured Offer record as the price (not a category's `#product_1` identifier).
Known tracking parameters are removed without removing product/variant query
parameters. Corrected source observations update/remove earlier streamed cards.

## Tests and diagnostics

### Visual discovery and quick search

The discovery screen includes six illustrated categories with editable example
queries, region selection, streamed progress and elapsed time. Categories collapse
after starting a search and can be reopened with **Explore categories**. Illustrations
are local SVGs, not fictitious merchant offers or external image dependencies.

Both search modes use the same category-independent local LLM planner and a separate
structured rewrite-review call. No product-specific query substitution table is used.
The planner proposes localized synonyms, quotes identity anchors from the request,
and keeps the original query. Code rejects changed model/numeric literals; the review
rejects added/dropped attributes, changed product types and ambiguous-intent narrowing.
Approved phrases reach discovery, catalogue links, browser extraction and final matching,
instead of being discarded by a later literal-only check. If planning/review fails,
the app reports the fallback and uses only the literal request plus shopping terms.

The UI defaults to **Quick search** with up to three discovery queries. Approved rewrites
get a retry even when the literal query found only broad catalogues. Quick mode searches
at most eight stores, two seed pages per store and two browser-fallback candidates.
If seeds yield no matching candidates, a store-catalogue search can examine up to
two additional pages, within the same extraction time budget.
**More stores** increases discovery and extraction budgets, not reasoning capabilities.
Planned phrases appear under search progress.
Brand, model, size and other original constraints still gate every returned offer.
The LLM can still miss translations or misjudge meaning. This is a general-purpose
workflow, not a guarantee of correct results for every query or access to every shop.
Both modes use the same price, semantic, region and product validation. Quick mode
trades coverage for less work; it cannot guarantee a fixed internet response time.
The 60-second quick extraction budget is a scheduling budget, not a hard wall-clock
deadline for already-running network/browser calls. API clients may pass
`search_mode: "quick"`; their backward-compatible default is `"thorough"`.

The **Save products to** selector chooses a destination wishlist for one-click
card saving. Product photos and titles open the multi-store comparison. The detailed
save dialog still supports notes and target prices. Repeated saves are idempotent.

Equivalent title spellings, word order, age notation and volume units are normalized
for grouping. A reviewed canonical identity groups equivalent offers across shops
for every category; it can omit cosmetic colour/condition wording but preserves
model, edition, capacity, size, strength and pack details. If the model is unavailable
or uncertain, the app falls back to conservative title/identifier grouping. Existing
account data and saved items are not deleted or bulk-rewritten; new searches use the
updated grouping when offers are refreshed.

Result filters work immediately on products already found: photo presence,
currency, price limit and sorting. A price limit requires a currency; cross-currency
prices are not silently compared or converted. These are display filters, not
promises that the entire market has been searched under that budget.

```bat
scripts\evaluate_live.bat --queries logitech --stores 15 --mode quick --cold-search
scripts\evaluate_live.bat --queries logitech --stores 15 --mode thorough --cold-search
```

### Run checks

```bat
scripts\check_app.bat
scripts\test_app.bat
scripts\test_app.bat -m browser
scripts\check_app.bat --live "whiskey" --region czechia --stores 5
scripts\evaluate_search.bat --strict
scripts\evaluate_search.bat --models llama3.2:3b qwen3:4b
scripts\evaluate_search.bat --models qwen3:4b --repeat 2 --strict
scripts\evaluate_live.bat --queries lagavulin logitech sony lego coffee ssd camera shoes --repeat 2 --stores 15 --cold-search
```

Default tests cover account isolation, CSRF/sessions, wishlist persistence,
currency minima, staleness, RAG isolation, real graph streaming, slow-store
independence, extraction and semantic regression cases. The browser test launches
real Chromium and a local API; shop results use fixtures. It covers registration,
search, comparison, saving, re-login and a mobile viewport. Screenshots go to
`data/ui-tests/`.

`--live` calls the actual local model and public websites, prints graph events and
uses a temporary database. Its result count is not a deterministic assertion.
`--verbose` prints full source offer records. `constraints.txt` records tested
direct dependency versions; it is not a complete transitive lockfile.
The included GitHub Actions workflow runs lint and unit/browser tests if the
repository is later pushed to GitHub; local use needs no remote service.

See [evaluation guide](evals/README.md) for labelled datasets, false-positive and
false-negative reports, cold versus cached discovery, and live result snapshots.
A zero-result live search fails the evaluation; it is never called perfect precision.
The current frozen corpus has 120 hand-labelled candidate cases, plus six reduced
observed source-metadata fixtures and whitespace/case/arrival-order regression tests.
Passing these does not imply complete or stable internet coverage.

## API and code

Machine-readable API schema: `/openapi.json`.

| Route | Purpose |
| --- | --- |
| `/api/auth/register`, `/api/auth/login`, `/api/auth/me`, `/api/auth/logout` | Account/session |
| `/api/wishlists` and nested `/{id}/items` | Private lists/items |
| `/api/products/{id}?region=czechia` | Grouped offers and minima |
| `/api/search/stream` | Authenticated POST returning SSE |
| `/api/search` | Same graph returning JSON |
| `/api/history` | Current user's search runs |
| `/api/health`, `/api/regions` | Model status and available markets |

Authenticated mutations require the session cookie and `X-CSRF-Token` returned
by login or `/api/auth/me`. The frontend handles these automatically.

`app/graph.py`: orchestration; `agents.py`: model/discovery logic; `tools/`:
adapters; `retrieval.py`: RAG; `schemas.py`/`matching.py`: validation;
`database.py`/`catalog.py`/`auth.py`/`wishlists.py`: persistence and API;
`static/`: responsive vanilla-JavaScript GUI.

## Data, security and honest limits

The database is `data/wishlist.sqlite3`. Keep it private: it contains account and
wishlist data. Stop the server before copying the entire `data` folder as a
backup. Databases/screenshots are excluded from Git. Passwords are scrypt hashes;
raw passwords and session tokens are not stored. Sessions use HttpOnly/SameSite
cookies, CSRF checks, login throttling and ownership checks. Shop HTML is never
inserted into the UI. Network tools check public URLs/redirects, block private
browser requests and bound download size/concurrency.

This is a **local application**, not a production-hardening claim. Public hosting
still needs HTTPS (`SECURE_COOKIES=true`), egress controls against DNS rebinding,
distributed throttling, identity/email recovery, backups, monitoring and security
review. Email verification, password recovery, scheduled price alerts and a durable
job queue are not included. Run history is persisted; LangGraph checkpoint/resume
is not implemented.

Search engines can ignore regional hints. European market discovery also filters
country domains, potentially omitting international `.com` merchants. A storefront
region does **not** prove delivery to your address. Chromium does not bypass
CAPTCHAs, login/age gates or bot protection. Some shops expose no usable metadata.

Search is bounded, not exhaustive; it cannot enumerate every internet product or
guarantee the absolute lowest market price. Delivery, checkout changes and special
discounts may be missing. Merchant metadata/identifiers may be wrong. Compare the
exact variant and confirm at the shop. For dependable commercial coverage, add
authorized merchant/feed/search APIs through the tool adapters.
