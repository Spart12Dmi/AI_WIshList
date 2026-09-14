"""Persist only validated source offers; compare prices within each currency."""

import hashlib
import json
import re
import time
from decimal import Decimal

from app.database import connection
from app.matching import SHOPPING_WORDS, words
from app.schemas import ProductOffer

_MEASUREMENT_WORDS = {
    "mm", "cm", "m", "in", "inch", "inches", "palec", "palce", "palcu",
    "g", "kg", "ml", "cl", "l", "gb", "tb", "yo", "y", "years", "year",
}


def is_available(value):
    # Extractors may supply a Schema.org URL or a bare enum. Unknown availability
    # stays unknown/eligible; it must not be advertised as verified in-stock.
    name = re.sub(r"[\s_-]", "", str(value).rstrip("/").rsplit("/", 1)[-1].lower())
    return name not in {"outofstock", "discontinued", "soldout"}


def _specific_model_query(query):
    """Return true for a concrete model search, not a broad category request.

    A query-aware family key is useful for searches such as ``iPhone 17 Pro``:
    colour and storage are offer dimensions, so they should compare together.
    Broad requests such as ``wireless headphones`` must still produce separate
    products. Digits/model codes provide a category-independent specificity
    signal; no brand or product vocabulary is embedded here.
    """
    if not query:
        return False
    requested = words(query) - SHOPPING_WORDS
    if len(requested) < 2:
        return False
    raw_tokens = re.findall(r"[^\W_]+", str(query).casefold())
    model_number = False
    for index, token in enumerate(raw_tokens):
        if not any(char.isdigit() for char in token):
            continue
        # A standalone dimension/age/weight is a search constraint, not a
        # model identity (``15 inch laptop`` must not become one product).
        neighbors = set(raw_tokens[max(0, index - 1):index] + raw_tokens[index + 1:index + 2])
        if token.isdigit() and neighbors & _MEASUREMENT_WORDS:
            continue
        if re.fullmatch(r"\d+(?:mm|cm|m|in|inch|inches|g|kg|ml|cl|l|gb|tb)", token):
            continue
        model_number = True
        break
    return model_number


def identity(offer, query=None):
    if _specific_model_query(query):
        requested = words(query) - SHOPPING_WORDS
        # The graph has already applied original/approved-variant matching.
        # Store only the normalized request in this key, so title-only variant
        # details (colour, condition, storage) become comparable offers.
        return "family:" + " ".join(sorted(requested))
    gtin = offer.get("gtin")
    if gtin and re.fullmatch(r"\d{8}|\d{12,14}", str(gtin)):
        return "gtin:" + str(gtin)
    # Manufacturer part numbers identify variants that share a display name.
    # Merchant-local SKU is deliberately NOT a cross-store identifier.
    variant = {
        key: " ".join(str(offer[key]).casefold().split()) for key in ("color", "size") if offer.get(key)
    }
    if offer.get("brand") and offer.get("mpn"):
        values = {
            "brand": offer["brand"].strip().casefold(),
            "mpn": offer["mpn"].strip().casefold(),
            **variant,
        }
        return "mpn:" + json.dumps(values, sort_keys=True, ensure_ascii=False)
    # Normalize age, equivalent units and word order, not model/size/strength.
    title = offer["title"].casefold()
    title = re.sub(r"\s*[|]\s*.*$", "", title)
    title = re.sub(r"\b(\d+)\s*(?:y\s*\.?\s*o\.?|years?\s+old|yo|let|y)\b", r"\1", title)
    tokens = words(title)
    # Descriptive merchandising boilerplate should not split an otherwise
    # identical item. Product/category words are never removed here.
    tokens -= {"new", "original", "product", "item", "online", "buy", "shop", "store"}
    title = " ".join(sorted(tokens))
    return (
        "title:"
        + title
        + ("|variant:" + json.dumps(variant, sort_keys=True, ensure_ascii=False) if variant else "")
    )


def save_offer(raw, region, query=None):
    offer = ProductOffer.model_validate(raw).model_dump()
    if not offer["currency"]:
        raise ValueError("An explicit currency is required to compare offers")
    key = identity(offer, query)
    pid = hashlib.sha256(key.encode()).hexdigest()[:24]
    now = time.time()
    with connection() as db:
        # A previous parser may have attributed a recommendation to this URL.
        # Do not let an old, wrong product identity overwrite a validated title.
        existing = db.execute(
            "SELECT o.product_id,o.region,o.currency,o.brand,o.mpn,o.color,o.size,o.gtin,p.identity_key,p.title FROM offers o JOIN products p ON p.id=o.product_id WHERE o.url=?",
            (offer["url"],),
        ).fetchone()
        observed_region = region
        if existing:
            same_title = identity({"title": existing["title"]}) == identity({"title": offer["title"]})
            if existing["identity_key"] == key or (
                existing["identity_key"].startswith(("gtin:", "mpn:"))
                and not offer["gtin"]
                and not offer["mpn"]
                and same_title
                and all(
                    not offer[field] or offer[field] == existing[field]
                    for field in ("brand", "mpn", "color", "size")
                )
            ):
                pid = existing["product_id"]
                key = existing["identity_key"]
                for field in ("brand", "mpn", "color", "size", "gtin"):
                    if not offer[field]:
                        offer[field] = existing[field]
            if region == "global" and existing["currency"] == offer["currency"]:
                observed_region = existing["region"]
        mapped = db.execute("SELECT product_id FROM product_keys WHERE identity_key=?", (key,)).fetchone()
        if mapped:
            pid = mapped["product_id"]
        db.execute(
            "INSERT OR IGNORE INTO products VALUES(?,?,?,?)", (pid, offer["title"], offer["image_url"], key)
        )
        db.execute(
            "UPDATE products SET image_url=COALESCE(image_url,?) WHERE id=?", (offer["image_url"], pid)
        )
        db.execute("INSERT OR IGNORE INTO product_keys VALUES(?,?)", (key, pid))
        db.execute("INSERT OR IGNORE INTO product_members VALUES(?,?)", (pid, key))
        db.execute(
            """INSERT INTO offers(url,product_id,title,shop,price,currency,region,image_url,availability,checked_at,source_url)
            VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET
            product_id=excluded.product_id,
            title=excluded.title, shop=excluded.shop, price=excluded.price, currency=excluded.currency,
            region=excluded.region, image_url=excluded.image_url, availability=excluded.availability,
            checked_at=excluded.checked_at, source_url=excluded.source_url""",
            (
                offer["url"],
                pid,
                offer["title"],
                offer["shop"],
                str(Decimal(str(offer["price"]))),
                offer["currency"],
                observed_region,
                offer["image_url"],
                offer["availability"],
                now,
                offer["source_url"] or offer["url"],
            ),
        )
        db.execute("DELETE FROM offer_documents WHERE url=?", (offer["url"],))
        db.execute(
            "UPDATE offers SET brand=?,mpn=?,color=?,size=?,gtin=? WHERE url=?",
            (offer["brand"], offer["mpn"], offer["color"], offer["size"], offer["gtin"], offer["url"]),
        )
        db.execute(
            "INSERT INTO offer_documents VALUES(?,?,?,?,?)",
            (
                offer["title"],
                f"{offer['title']} | {offer['shop']} | {offer['currency']} {offer['price']}",
                offer["url"],
                observed_region,
                now,
            ),
        )
    return product_detail(pid, region)


def index_legacy_products(db):
    """Index old product records without deleting records, offers or saved-list metadata."""
    rows = db.execute("""SELECT p.* FROM products p LEFT JOIN product_members m ON m.product_id=p.id
                         WHERE m.product_id IS NULL ORDER BY p.id""").fetchall()
    for row in rows:
        key = row["identity_key"]
        if key.startswith("title:"):
            variant = json.loads(key.split("|variant:", 1)[1]) if "|variant:" in key else {}
            key = identity({"title": row["title"], **variant})
        db.execute("INSERT OR IGNORE INTO product_keys VALUES(?,?)", (key, row["id"]))
        db.execute("INSERT OR IGNORE INTO product_members VALUES(?,?)", (row["id"], key))


def product_detail(pid, region=None):
    with connection() as db:
        group = db.execute("""SELECT k.identity_key,k.product_id FROM product_members m
                              JOIN product_keys k ON k.identity_key=m.identity_key WHERE m.product_id=?""",
                           (pid,)).fetchone()
        if group:
            pid = group["product_id"]
        product = db.execute("SELECT id,title,image_url FROM products WHERE id=?", (pid,)).fetchone()
        if not product:
            return None
        if group:
            sql, args = "SELECT * FROM offers WHERE product_id IN (SELECT product_id FROM product_members WHERE identity_key=?)", [group["identity_key"]]
        else:
            sql, args = "SELECT * FROM offers WHERE product_id=?", [pid]
        if region and region != "global":
            sql += " AND region=?"
            args.append(region)
        rows = db.execute(sql, args).fetchall()
    offers = [dict(row) for row in rows]
    minimums = {}
    for offer in offers:
        amount = Decimal(offer["price"])
        offer["price"] = float(amount)
        offer["stale"] = time.time() - offer["checked_at"] > 86400
        # Old or unavailable offers remain visible but don't define the current minimum.
        if not offer["stale"] and is_available(offer["availability"]):
            currency = offer["currency"]
            minimums[currency] = min(minimums.get(currency, amount), amount)
    offers.sort(key=lambda row: (row["currency"], row["price"]))
    return {
        **dict(product),
        "offers": offers,
        "minimum_prices": {k: float(v) for k, v in minimums.items()},
        "offer_count": len(offers),
        "region": region,
    }
