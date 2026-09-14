"""Category-independent checks for model-proposed retrieval phrases.

No product names, category recipes or language-specific substitutions live here.
Semantic equivalence is reviewed separately; these checks protect syntax and literals.
"""

import re
import unicodedata

from app.matching import query_evidence, words


def tokens(text):
    return re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", text).casefold())


def validate_anchors(original, anchors):
    source = " " + " ".join(tokens(original)) + " "
    for anchor in anchors:
        if not tokens(anchor) or " " + " ".join(tokens(anchor)) + " " not in source:
            raise ValueError("An identity anchor must be quoted from the original request")


def validate_variant(original, planned, anchors=()):
    if not isinstance(planned, str) or not 2 <= len(planned.strip()) <= 200 or len(planned.split()) > 16:
        raise ValueError("Query plan has invalid length")
    if re.search(r"(?:https?://|\bsite:|[\r\n]|[:;])", planned, re.I):
        raise ValueError("Query plan must not supply URLs or search operators")
    # Model numbers, capacities, sizes and quantities must not be added or lost.
    def numbers(text):
        # Compare normalized measurement tokens, so an LLM may translate
        # ``2 m`` to ``200 cm`` while still preserving the physical constraint.
        return {token for token in words(text) if any(char.isdigit() for char in token)}
    if numbers(original) != numbers(planned):
        raise ValueError("Query plan changed numeric constraints")
    validate_anchors(original, anchors)
    target = " " + " ".join(tokens(planned)) + " "
    for anchor in anchors:
        if " " + " ".join(tokens(anchor)) + " " not in target:
            raise ValueError("Query plan changed an identity anchor")
    # ``numbers`` above already compares normalized model/unit tokens. Do not
    # compare raw mixed tokens here: a valid unit translation such as ``2 m``
    # -> ``200 cm`` necessarily changes its surface form.


def search_variants(query, region, proposed=(), *, anchors=(), reviewed=False):
    """Always retain the original. Only reviewed semantic rewrites may replace words."""
    variants = [query.strip()]
    for candidate in proposed:
        try:
            validate_variant(query, candidate, anchors)
        except (ValueError, TypeError):
            continue
        # Before the independent reviewer runs, do not treat a proposed
        # rewrite as trusted retrieval evidence.  This keeps all semantic
        # decisions in the LLM planner/reviewer instead of recreating a hidden
        # synonym table here.
        if not reviewed:
            continue
        if candidate.casefold() not in {v.casefold() for v in variants}:
            variants.append(candidate.strip())
    return variants[:4]


def matches_query(query, title, variants=()):
    """Approved rewrites are additional evidence, never replacement user intent."""
    return any(query_evidence(phrase, title) for phrase in dict.fromkeys([query, *(variants or [])]))
