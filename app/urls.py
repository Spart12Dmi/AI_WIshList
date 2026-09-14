"""Conservative offer identity: drop attribution, never product/variant selectors."""

import re
from urllib.parse import unquote_plus, urlsplit, urlunsplit

TRACKING_KEYS = {"gclid", "dclid", "fbclid", "msclkid", "srsltid", "gbraid", "wbraid"}


def canonical_offer_url(url):
    parsed = urlsplit(url)
    parts = []
    for part in parsed.query.split("&"):
        key = unquote_plus(part.split("=", 1)[0]).casefold()
        if part and not key.startswith("utm_") and key not in TRACKING_KEYS:
            parts.append(part)
    # Schema identity fragments are not separate buyable variants. Preserve
    # other fragments (including actual client-side #/product/123 routes).
    fragment = parsed.fragment
    if re.fullmatch(r"product(?:[-_]\d+)?", fragment, re.I):
        fragment = ""
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "&".join(parts), fragment))
