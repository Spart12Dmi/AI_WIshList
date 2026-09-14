# Search quality evaluation

There are two different tests. Do not confuse fixture accuracy with live coverage.

## Frozen candidate corpus

`search_cases.json` has 44 manually labelled cases spanning whisky, mice,
headphones, coffee/grinders, phones, cameras and LEGO. The labels distinguish
semantic relevance (`relevant`) from eligibility for display (`expected`), which
also requires a valid price, regional evidence and no explicit unavailable status.
Titles marked `user-screenshot-title` came from the reported failure. Other cases
are synthetic boundary tests; their example URLs/prices are not merchant data.

`observed_cases.json` adds eight manually reviewed snapshots from actual searches
(including rejected candidates), with observed merchant URLs/prices. They are
historical metadata observations, not current prices or live training data.

`extended_cases.json` adds 52 manually labelled boundary cases: model suffixes,
capacities, decimal sizes, packs, translations, accessories and flavour/scent
false matches. `intent_cases.json` adds 16 purchase/rental and URL cases. Total:
**120 candidate cases**. `source_cases.json` separately preserves **six reduced
real JSON-LD observations**, with manually checked URL/price/MPN expectations.
The unit suite replays these without network and permutes shoe arrival order.
Case/diacritic/whitespace transformations are robustness tests, not additional
independent human labels.

## UI and speed check (2026-09-13)

The UI's new quick mode is evaluated with `--mode quick`; the API/evaluation
default remains `--mode thorough`. On one sequential, cold-cache Logitech G502
pair, quick returned 3 products in 33.703s (first at 24.906s), while thorough
returned 10 in 84.188s (first at 11.219s). Both passed the configured live offer
assertions. Reports: `20260913T082912930378Z-live` and
`20260913T083109704961Z-live`. This is not a statistically controlled speedup:
quick reduced total work/coverage but did NOT win time-to-first-result in this pair.
The first quick report lists configured budgets (15 stores/12 browser candidates);
its actual quick caps were 8/2, with eight stores emitted. New reports record the
effective caps. No prices or account records were reused between these runs.

The browser journey checks category illustrations and example selection,
currency-aware sorting, budget/photo filtering, category collapse/reopening,
desktop/mobile overflow, comparison, account login and wishlist persistence.
Its product data is explicitly fixture data, not live merchant coverage.

The regression/challenge splits expose different product families. The challenge
set has been inspected during development and is **not an unseen holdout**.
Nothing here trains/fine-tunes model weights. Prompt/rule tuning can overfit this
small corpus; expand it with manually reviewed real observations.

```bat
scripts\evaluate_search.bat --strict
scripts\evaluate_search.bat --models llama3.2:3b qwen3:4b
scripts\evaluate_search.bat --split challenge --models qwen3:4b
scripts\evaluate_search.bat --dataset evals/observed_cases.json --models qwen3:4b --strict
scripts\evaluate_search.bat --models qwen3:4b --repeat 2 --strict
```

No model download happens implicitly. Missing models are reported as failures.
Models use the app's actual classifier and inference settings. Raw semantic
classification and the guarded pipeline are scored separately. Raw model errors
never silently become deterministic successes in model scores. The production
fallback is tested separately by unit tests.

Reports in `data/evaluations/` include per-case predictions, false positives,
false negatives, invalid responses, precision/recall/F1, model digest, per-call
latency, and dataset/code hashes. No predictions means undefined precision, not
100%. `--strict` exits nonzero on any mismatch or invalid model response.
Do not compare latency measurements taken during competing GPU workloads.

### Actual debugging sequence (2026-09-11)

On the same 44 cases, initial deterministic gates had 6 false positives and
2 false negatives (precision 72.7%, recall 88.9%). After fixing accessory/empty
packaging checks, equivalent storage/mass units and Schema.org availability URLs,
the rules passed 44/44. This is regression coverage, not an internet success rate.

The first model comparison exposed URL-copying failures in Llama and excessive
rejection in Qwen. Splitting source/region validation from semantic generation
removed URL copying entirely. A shorter prompt with broad-brand and accessory
examples corrected Qwen's assumption that every query needs full specifications.

Final v3 guarded results: Qwen 44/44, Llama 42/44 (two equivalent-volume false
negatives). Raw semantic results remain imperfect: Qwen produced 13 false
positives before the guards, versus 4 for Llama. Qwen is chosen here for guarded
recall, **not because it is universally better or safe without output checks**.
Reports `before-quality-fixes.json`, `after-quality-fixes.json`, and
`model-comparison[-v2|-v3].json` preserve these experiments locally.

## Live search and repeat stability

`live_queries.json` defines eight queries with independent expected brand/model
patterns and currencies. These checks catch obvious identity errors; they are
not expert labels for every accessory, price or variant and do not measure
whole-market recall.

```bat
scripts\evaluate_live.bat --queries lagavulin logitech sony lego coffee --stores 8 --repeat 2
scripts\evaluate_live.bat --queries lagavulin logitech sony --repeat 2 --cold-search
scripts\evaluate_live.bat --queries lagavulin logitech sony lego coffee ssd camera shoes --stores 15 --repeat 2 --cold-search
```

Every query/repeat uses a fresh isolated database, not your accounts or wishlists.
By default repeated searches share the app's URL discovery cache; prices are
refetched. `--cold-search` disables that cache to measure search-provider
variability. The report records which mode was used. Changing stores/models/cache
between runs is not a controlled model-only comparison.

Each timestamped directory preserves all graph events, processed candidates and
rejection reasons, every streamed product, final products and source URLs,
first-product/total latency, zero-result failures, and URL overlap across repeats.
Different URLs for genuinely equivalent products can lower the overlap metric.
Network/provider/merchant failures are real failures, not automatically skipped.

Recheck old snapshots against updated assertions without making new requests:

```bat
conda run -n local-product-search python -s -m evals.recheck data/evaluations/TIMESTAMP-live
```

This deliberately does not rewrite old results. In the first five-query run,
title checks passed but inspection found unrelated LEGO recommendation links and
a coffee grinder linked to a seller homepage. Updated checks fail those saved
snapshots. Microdata extraction now includes `link[itemprop=url]` nested in an
Offer, excludes nested seller/recommendation scopes, and rejects homepage product
links. Minimal reproductions live in `tests/test_extraction_ownership.py`.

`candidates-for-human-review.json` deliberately has null human labels. Review the
title, exact variant, source and observed price before promoting a case into the
frozen corpus; never use the app's own decision as ground truth. These public
observations are snapshots, not promises of current price or stock. Reports stay
out of Git by default. Retain useful minimal metadata fixtures rather than entire
merchant pages, cookies, or private account data.

Relevant regression tests also cover recommendation cards hiding the main
product, `og:price:currency`, query-aware category traversal, refreshing historical
RAG URLs, stale/unavailable minima, and poisoned old catalogue identities.

## Expanded debugging cycle (2026-09-11)

The extra 52 cases initially exposed 19 false positives and 9 false negatives in
the rules, despite the original 44 passing. Fixes preserve `S24+`, pack quantities,
decimal sizes, equivalent lengths/masses and multi-word accessory/category terms.
Whitespace transformations exposed five additional bugs, subsequently fixed.

`expanded-120-repeat.json` records 120/120 rule decisions and 240/240 guarded Qwen
decisions over two repeats, with no invalid outputs or changed decisions. Raw
Qwen still produced 68 false-positive decisions across those 240 calls: the output
guards are essential. This is a tuned regression corpus, not an unseen benchmark.

The eight-query cold run `20260911T135354471242Z-live` found products in 15/16
searches, but updated independent checks expose rental pricing, tracking/fragment
duplicates and mixed Nike manufacturer variants. Its one zero-result Canon
repeat remains a failure. URL overlaps ranged from 0 to 0.85; these results do
**not** establish reliable cold-search coverage.

The relevant fixes use the Offer URL associated with its price, preserve MPN /
brand / size / colour, remove only known tracking query keys and reject rentals
from purchase results. Rich JSON-LD is not overwritten by sparse OpenGraph tags.
SQLite migrations only add columns; accounts/wishlist records are not deleted.
If a corrected URL changes product identity, earlier streamed cards are refreshed
or removed. Known metadata on that exact URL survives a sparse refresh.

Provider diagnostics are available with:

```bat
conda run -n local-product-search python -s -m evals.providers
conda run -n local-product-search python -s -m evals.capture https://merchant.example/product
```

The 48-request provider experiment is saved in
`20260911T134141949016Z-providers.json`. Google/Mojeek calls failed in that run;
Bing/Yandex were more available, while Brave/Yahoo sometimes added useful results.
The app now collects providers independently, merges all completed results and
applies regional rank before result limits. These are local observations, not a
universal provider ranking. `capture` saves minimal public metadata for manual
inspection, with null human labels; it does not turn model predictions into labels.
