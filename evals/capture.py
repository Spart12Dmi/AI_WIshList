"""Capture small public product-metadata snapshots for MANUAL review, not labels.

python -m evals.capture https://merchant.example/product
Does not save full merchant HTML, cookies, accounts, or private data.
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup

from app.tools.web_search import _fetch_product_html, _is_product, _walk_json, parse_product_offers


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("urls", nargs="+")
    args = parser.parse_args()
    snapshots = []
    fields = {
        "@type",
        "url",
        "name",
        "brand",
        "sku",
        "mpn",
        "gtin",
        "gtin13",
        "gtin14",
        "color",
        "size",
        "offers",
    }
    for url in args.urls:
        try:
            html, final_url = _fetch_product_html(url)
            soup = BeautifulSoup(html, "lxml")
            items = []
            for script in soup.find_all("script", type="application/ld+json"):
                try:
                    data = json.loads(script.get_text())
                except ValueError:
                    continue
                items.extend(
                    {k: v for k, v in item.items() if k in fields}
                    for item in _walk_json(data)
                    if _is_product(item)
                )
            snapshot = {
                "requested_url": url,
                "source_url": final_url,
                "jsonld_products": items,
                "extracted": parse_product_offers(html, final_url),
                "human_labels": None,
            }
            print(json.dumps(snapshot, ensure_ascii=False), flush=True)
        except Exception as exc:
            snapshot = {"requested_url": url, "error": type(exc).__name__ + ": " + str(exc)[:300]}
            print(json.dumps(snapshot), flush=True)
        snapshots.append(snapshot)
    folder = Path("data/evaluations")
    folder.mkdir(parents=True, exist_ok=True)
    output = folder / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-source-metadata.json")
    output.write_text(json.dumps(snapshots, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Report:", output)
    return int(any("error" in snapshot for snapshot in snapshots))


if __name__ == "__main__":
    raise SystemExit(main())
