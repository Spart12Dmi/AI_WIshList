"""Persist only validated source offers; compare prices within each currency."""

import hashlib
import json
import re
import time
import unicodedata
from decimal import Decimal

from app.database import connection
from app.schemas import ProductOffer


def is_available(value):
    # Extractors may supply a Schema.org URL or a bare enum. Unknown availability
    # stays unknown/eligible; it must not be advertised as verified in-stock.
    name = re.sub(r"[\s_-]", "", str(value).rstrip("/").rsplit("/", 1)[-1].lower())
    return name not in {"outofstock", "discontinued", "soldout"}


def identity(offer):
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
    # Conservative exact normalized title matching preserves model, size and colour.
    title = unicodedata.normalize("NFKC", offer["title"]).casefold()
    title = re.sub(r"\s*[|]\s*.*$", "", title)
    title = " ".join(re.findall(r"\w+", title))
    return (
        "title:"
        + title
        + ("|variant:" + json.dumps(variant, sort_keys=True, ensure_ascii=False) if variant else "")
    )


def save_offer(raw, region):
    offer = ProductOffer.model_validate(raw).model_dump()
    if not offer["currency"]:
        raise ValueError("An explicit currency is required to compare offers")
    key = identity(offer)
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
        db.execute(
            "INSERT OR IGNORE INTO products VALUES(?,?,?,?)", (pid, offer["title"], offer["image_url"], key)
        )
        db.execute(
            "UPDATE products SET image_url=COALESCE(image_url,?) WHERE id=?", (offer["image_url"], pid)
        )
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


def product_detail(pid, region=None):
    with connection() as db:
        product = db.execute("SELECT id,title,image_url FROM products WHERE id=?", (pid,)).fetchone()
        if not product:
            return None
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
