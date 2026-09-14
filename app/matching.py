"""Conservative, product-independent evidence gates for retrieved listings.

This module intentionally contains no product catalogue or language synonym
dictionary. The local LLM proposes and reviews semantic query variants; this
module only normalizes measurements and enforces hard identity/safety checks.
"""

import re
import unicodedata
from decimal import Decimal
from urllib.parse import urlsplit

from app.urls import canonical_offer_url


def is_individual_product_url(url):
    parsed = urlsplit(canonical_offer_url(url))
    # A bare merchant homepage (possibly with a locale) is not a product link.
    path = parsed.path.strip("/")
    return bool(parsed.query or (path and not re.fullmatch(r"[a-z]{2}(?:[-_][a-z]{2})?", path, re.I)))


def words(value):
    """Return normalized lexical tokens while preserving numeric constraints.

    Unit conversion and punctuation handling are universal and deterministic;
    no product or category names are special-cased here.
    """
    value = str(value).replace("\u00c3\u2014", "×").casefold()
    value = unicodedata.normalize("NFKD", value)
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = " ".join(value.split())
    value = re.sub(r"\b([a-z]+\d+)\s*\+(?=\W|$)", r"\1 plus ", value)
    value = re.sub(r"\b(\d+)\s*(?:x|×|Ã—)\s*(?=\d)", r" pack\1 ", value)

    def volume(match):
        amount = Decimal(match[1].replace(",", ".")) * {"ml": 1, "cl": 10, "l": 1000}[match[2]]
        return " volume" + format(amount.normalize(), "f").replace(".", "p") + "ml "

    value = re.sub(r"\b(\d+(?:[.,]\d+)?)\s*(ml|cl|l)\b", volume, value)

    def mass(match):
        amount = Decimal(match[1].replace(",", ".")) * {"g": 1, "kg": 1000}[match[2]]
        return " mass" + format(amount.normalize(), "f").replace(".", "p") + "g "

    value = re.sub(r"\b(\d+(?:[.,]\d+)?)\s*(kg|g)\b", mass, value)

    def length(match):
        amount = Decimal(match[1].replace(",", ".")) * {"mm": 1, "cm": 10, "m": 1000}[match[2]]
        return " length" + format(amount.normalize(), "f").replace(".", "p") + "mm "

    value = re.sub(r"\b(\d+(?:[.,]\d+)?)\s*(mm|cm|m)\b", length, value)
    value = re.sub(r'(?<=\d)\s*(?:inches\b|inch\b|palcu\b|palce\b|palec\b|")', " inch ", value)
    # Keep decimals as one value: 10.9 and 9.10 must not become the same set.
    value = re.sub(
        r"\b\d+[.,]\d+\b",
        lambda match: format(Decimal(match[0].replace(",", ".")).normalize(), "f").replace(".", "p"),
        value,
    )
    value = re.sub(r"\b(\d+)\s*(gb|tb)\b", r"\1\2", value)
    # Common age notation is a measurement-like constraint, independent of
    # what the product is (for example, a model revision or age rating).
    value = re.sub(r"\b(\d+)\s*(?:y\s*\.?\s*o\.?|yo|y|years?(?:\s+old)?|let|jahre?)\b", r"\1", value)
    value = re.sub(r"(?<=[a-z])-(?=\d)", "", value)
    return set(re.findall(r"[^\W_]+", value))


# Generic shopping words are not product identity. This list does not encode
# any product family; translations of the product itself come from the LLM.
SHOPPING_WORDS = {
    "preis", "kaufen", "kup", "kupit", "buy", "price", "prices", "for",
    "the", "a", "an", "and", "with", "of", "in", "koupit", "cena", "ceny",
    "produkt", "product", "products", "online", "shop", "store", "na", "v",
    "best", "cheap",
}

# A listing led by one of these terms is normally a part, packaging item or
# add-on. Keep this small and generic; it is not a semantic category model.
ACCESSORY_WORDS = {
    "accessory", "accessories", "replacement", "spare", "protective", "cover",
    "case", "cable", "adapter", "manual", "instructions", "refill", "empty",
    "sample", "stand", "holder", "mount", "glass", "battery", "filter",
    "laces", "brush", "sleeve", "dock", "antenna", "kit", "lighting", "bits",
    "flavoured", "flavored", "scented",
}


def accessory_mismatch(query, title):
    """Reject an obvious accessory/replacement title for a main product query."""
    requested, observed = words(query), words(title)
    if observed & {"empty", "prazdna", "prazdny", "leere"}:
        return not requested & {"empty", "prazdna", "prazdny", "leere"}
    if observed & ACCESSORY_WORDS and not requested & ACCESSORY_WORDS:
        normal = unicodedata.normalize("NFKD", str(title).casefold())
        normal = "".join(c for c in normal if not unicodedata.combining(c))
        # "case for X", "cable pro X", and similar constructions identify the
        # add-on as the lead item. A bundle such as "X with stand" is kept.
        first = normal.split()[0] if normal.split() else ""
        # A leading accessory remains an accessory even when it mentions a
        # bundle or compatible part later in the title.
        if first in ACCESSORY_WORDS:
            return True
        if re.search(r"\b(?:with|including|vcetne)\b", normal):
            return False
        if observed & {"replacement", "spare", "protective", "refill", "empty", "sample"} \
                and not requested & {"replacement", "spare", "protective", "refill", "empty", "sample"}:
            return True
        return True
    return False


def query_evidence(query, title):
    """Require every non-shopping token from a query to appear in a title.

    This is deliberately lexical and fail-closed. ``matches_query`` in
    ``query_expansion.py`` calls it for the original query and each separately
    LLM-reviewed translation/synonym, so no hard-coded product cases are
    needed here.
    """
    if re.search(
        r"\b(ignore\s+(?:all\s+|previous\s+)?instructions|system\s+prompt|mark\s+this\b.*\brelevant)\b",
        str(title),
        re.I,
    ):
        return False
    if accessory_mismatch(query, title):
        return False
    requested, observed = words(query), words(title)
    anchors = requested - SHOPPING_WORDS
    return bool(anchors) and anchors.issubset(observed)


def lexical_match(query, title):
    return query_evidence(query, title)


def purchase_intent_matches(query, title, url=""):
    """A rental/day rate is not a purchase offer, even when the model matches."""
    rental_terms = {"rental", "rent", "hire", "pujcovna", "pronajem", "mieten", "verleih"}
    requested = bool(words(query) & rental_terms)
    observed = bool(words(title) & rental_terms or words(urlsplit(url).path) & rental_terms)
    return requested == observed
