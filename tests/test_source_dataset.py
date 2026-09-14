import json
from itertools import permutations
from pathlib import Path

import pytest

from app.catalog import identity
from app.tools import web_search

CASES = json.loads((Path(__file__).parents[1] / "evals/source_cases.json").read_text(encoding="utf-8"))[
    "cases"
]


def extract(case):
    html = '<script type="application/ld+json">' + json.dumps(case["input"]) + "</script>"
    return web_search.parse_product_offers(html, case["source"])[0]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_reviewed_source_metadata_replay(monkeypatch, case):
    monkeypatch.setattr(web_search, "is_public_http_url", lambda url: True)
    actual = extract(case)
    assert {key: actual.get(key) for key in case["expected"]} == case["expected"]


def test_observed_shoe_variants_stay_separate_in_every_arrival_order(monkeypatch):
    monkeypatch.setattr(web_search, "is_public_http_url", lambda url: True)
    offers = [extract(case) for case in CASES if case["expected"]["mpn"]]
    for order in permutations(offers):
        assert len({identity(offer) for offer in order}) == 4
