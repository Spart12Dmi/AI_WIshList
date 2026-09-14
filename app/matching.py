"""Deterministic evidence gates complement (and can overrule) small-model judgements."""

import re
import unicodedata
from decimal import Decimal
from urllib.parse import urlsplit

from app.urls import canonical_offer_url


def is_individual_product_url(url):
    parsed = urlsplit(canonical_offer_url(url))
    # A bare merchant homepage (possibly with a locale) is not a product link.
    # Query-based product routes are allowed; source/category URLs are separate.
    path = parsed.path.strip("/")
    return bool(parsed.query or (path and not re.fullmatch(r"[a-z]{2}(?:[-_][a-z]{2})?", path, re.I)))


def words(value):
    value = unicodedata.normalize("NFKD", value.casefold())
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = " ".join(value.split())
    for phrase, token in {
        "drill bits": "drillbits",
        "drill bit": "drillbits",
        "cistici gel": "cleanser",
        "lighting kit": "lightingkit",
        "remote control": "remote",
        "heat sink": "heatsink",
    }.items():
        value = re.sub(r"\b" + re.escape(phrase) + r"\b", token, value)
    value = re.sub(r"\b([a-z]+\d+)\s*\+(?=\W|$)", r"\1 plus ", value)
    value = re.sub(r"\b(\d+)\s*[x×]\s*(?=\d)", r" pack\1 ", value)

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
    # Keep decimals as one value: 10.9 and 9.10 must not become the same {9,10} set.
    value = re.sub(
        r"\b\d+[.,]\d+\b",
        lambda match: format(Decimal(match[0].replace(",", ".")).normalize(), "f").replace(".", "p"),
        value,
    )
    value = re.sub(r"\b(\d+)\s*(gb|tb)\b", r"\1\2", value)
    value = re.sub(r"\b(\d+)\s*(?:yo|y|years?(?:\s+old)?|let|jahre?)\b", r"\1", value)
    value = re.sub(r"(?<=[a-z])-(?=\d)", "", value)
    return set(re.findall(r"[^\W_]+", value))


# Explicit multilingual aliases, not an attempt to pretend lexical matching is an LLM.
CATEGORIES = [
    (
        {"whisky", "whiskey"},
        {
            "whisky",
            "whiskey",
            "bourbon",
            "scotch",
            "macallan",
            "glenfiddich",
            "glenmorangie",
            "lagavulin",
            "jameson",
            "blantons",
        },
        ("johnnie walker", "jack daniel", "single malt"),
    ),
    (
        {"headphones", "earphones", "earbuds", "sluchatka", "kopfhorer"},
        {"headphones", "earphones", "earbuds", "sluchatka", "kopfhorer", "airpods"},
        (),
    ),
]

# Generic multilingual terms can be translated. All other query words remain
# mandatory identity anchors, including previously unseen brands, not a whitelist.
TERM_GROUPS = [
    {"rental", "rent", "hire", "pujcovna", "pronajem", "mieten", "verleih"},
    {"coffee", "kava", "kavy", "kavu", "kaffee"},
    {"grinder", "grinders", "mlynek", "mlynky", "muhle"},
    {"mouse", "mys", "maus"},
    {"keyboard", "klavesnice", "tastatur"},
    {"wireless", "bezdratova", "bezdratove", "bezdratovy", "kabellos", "bluetooth"},
    {"black", "cerna", "cerne", "cerny", "schwarz"},
    {"white", "bila", "bile", "bily", "weiss"},
    {"cable", "kabel"},
    {"case", "cover", "kryt", "obal", "pouzdro", "hulle"},
    {"cream", "krem", "kremy"},
    {"drill", "drills", "vrtacka", "vrtacky", "bohrmaschine"},
    {"drillbits", "vrtak", "vrtaky", "bohrer"},
    {"laptop", "laptops", "notebook", "notebooks"},
    {"tv", "television", "televize", "fernseher"},
]

ACCESSORY_GROUPS = [
    {"cable", "kabel"},
    {"case", "cover", "kryt", "obal", "pouzdro", "hulle"},
    {"earpads", "nausniky"},
    {"glass", "glasses", "sklenice", "sklenicka"},
    {"heatsink"},
    {"antenna"},
    {"battery", "baterie"},
    {"filter", "filtr"},
    {"dock"},
    {"instructions", "manual", "navod"},
    {"lightingkit"},
    {"laces", "tkanicky"},
    {"drillbits", "vrtaky", "vrtak"},
    {"sleeve"},
    {"brush", "kartacek"},
    {"remote", "ovladac"},
]


def accessory_mismatch(query, title):
    """Explicit part/packaging titles cannot stand in for the main product.

    A bundle 'mouse with cable' is different from 'replacement cable for mouse'.
    This is a conservative evidence gate, not a general semantic classifier.
    """
    requested, observed = words(query), words(title)
    if observed & {"empty", "prazdna", "prazdny", "leere"} and observed & {"bottle", "lahev", "flasche"}:
        return not requested & {"empty", "prazdna", "prazdny", "leere"}
    normal = unicodedata.normalize("NFKD", title.casefold())
    normal = "".join(c for c in normal if not unicodedata.combining(c))
    # Flavoured/scented products are not the beverage itself.
    other_categories = {"chocolate", "chocolates", "candle", "candles", "perfume", "sauce"}
    if (
        requested & {"coffee", "kava", "whisky", "whiskey"}
        and observed & other_categories
        and not requested & other_categories
    ):
        return True
    for accessories in ACCESSORY_GROUPS:
        if observed & accessories and not requested & accessories:
            accessory_pattern = r"\b(?:" + "|".join(re.escape(term) for term in accessories) + r")\b"
            accessory_position = re.search(accessory_pattern, normal)
            bundle_position = re.search(r"\b(with|vcetne|including)\b", normal)
            if (
                accessory_position
                and bundle_position
                and bundle_position.start() < accessory_position.start()
            ):
                continue
            if (
                observed & {"replacement", "spare", "nahradni", "protective", "ochranny"}
                or re.search(r"\b(for|pro|fur)\b", normal)
                or "glass" in accessories
                and not observed & {"bottle", "lahev"}
                or normal.split()[0] in accessories
                or observed & {"drillbits", "lightingkit", "instructions"}
                or "brush" in accessories
                and "cleaning" in observed
            ):
                return True
    return False


SHOPPING_WORDS = {
    "buy",
    "price",
    "prices",
    "for",
    "the",
    "a",
    "an",
    "and",
    "with",
    "of",
    "in",
    "koupit",
    "cena",
    "ceny",
    "produkt",
    "product",
    "products",
    "online",
    "shop",
    "store",
    "na",
    "v",
    "best",
    "cheap",
}


def query_evidence(query, title):
    """Require evidence for EVERY identity/variant constraint in the original query.

    A model may reject a candidate but cannot waive a missing brand or model.
    Unknown category translations fail closed; explicitly add aliases when tested.
    """
    if re.search(
        r"\b(ignore\s+(?:all\s+|previous\s+)?instructions|system\s+prompt|mark\s+this\b.*\brelevant)\b",
        title,
        re.I,
    ):
        return False
    if accessory_mismatch(query, title):
        return False
    requested, observed = words(query), words(title)
    anchors = requested - SHOPPING_WORDS
    if not anchors:
        return False
    for triggers, aliases, phrases in CATEGORIES:
        if requested & triggers:
            normalized = " ".join(re.findall(r"\w+", title.casefold()))
            if not (observed & aliases or any(phrase in normalized for phrase in phrases)):
                return False
            anchors -= triggers
    for synonyms in TERM_GROUPS:
        if requested & synonyms:
            if not observed & synonyms:
                return False
            anchors -= synonyms
    return anchors.issubset(observed)


def lexical_match(query, title):
    return query_evidence(query, title)


def purchase_intent_matches(query, title, url=""):
    """A rental/day rate is not a purchase offer, even when the model matches."""
    rental_terms = {"rental", "rent", "hire", "pujcovna", "pronajem", "mieten", "verleih"}
    requested = bool(words(query) & rental_terms)
    observed = bool(words(title) & rental_terms or words(urlsplit(url).path) & rental_terms)
    return requested == observed
