import re
import unicodedata
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app import agents
from app.agents import model_options
from app.catalog import save_offer
from app.matching import query_evidence
from app.regions import get_region
from app.schemas import ProductMatch
from evals.benchmark import CORPORA, decision, load_cases, metrics, run
from evals.live import violations_for


@pytest.mark.parametrize(
    "case", [case for path in CORPORA for case in load_cases(path)], ids=lambda case: case.id
)
def test_labelled_acceptance_corpus(case):
    assert decision(case) == case.expected, case.reason


@pytest.mark.parametrize(
    "case", [case for path in CORPORA for case in load_cases(path)], ids=lambda case: case.id
)
def test_acceptance_is_invariant_to_case_accents_and_whitespace(case):
    def normalize(text):
        text = unicodedata.normalize("NFKD", text.upper())
        text = "".join(char for char in text if not unicodedata.combining(char))
        return re.sub(r"\s+", "   ", text)

    variant = case.model_copy(update={"query": normalize(case.query), "title": normalize(case.title)})
    assert decision(variant) == case.expected, case.reason


def test_no_predictions_is_not_perfect_precision():
    result = metrics([{"predicted": False, "expected": True}])
    assert result["precision"] is None
    assert result["recall"] == 0


def test_corpus_has_cross_category_console_recovery_and_unique_ids():
    cases = [case for path in CORPORA for case in load_cases(path)]
    assert len(cases) >= 140
    assert any(case.query == "PlayStation 5 Pro" for case in cases)
    assert len({case.id for case in cases}) == len(cases)
    assert len({case.category for case in cases}) >= 8


def test_benchmark_reports_metrics_by_category():
    cases = [case for path in CORPORA for case in load_cases(path)]
    result = run(cases, [])
    assert result["runs"]["rules"]["metrics"]["count"] == len(cases)
    assert set(result["runs"]["rules"]["by_category"]) >= {
        "gaming", "computers", "photography", "kitchen", "networking"
    }
    assert all(item["count"] for item in result["runs"]["rules"]["by_category"].values())


def test_model_error_is_never_a_successful_negative():
    result = metrics([{"predicted": None, "expected": False}])
    assert result["tn"] == 0
    assert result["invalid"] == 1
    assert result["accuracy"] == 0


def test_corpus_availability_matches_real_minima(client):
    case = next(case for case in load_cases() if case.id == "lag-schema-unavailable")
    product = save_offer(case.offer(), case.region)
    assert product["minimum_prices"] == {}


def test_accessory_gate_does_not_reject_included_cable():
    assert query_evidence("Logitech G502", "Logitech G502 mouse with cable")
    assert query_evidence("Lagavulin", "Lagavulin 16 whisky with glasses gift set")


def test_local_models_use_bounded_context_without_thinking():
    options = model_options("qwen3:4b")
    assert options["num_ctx"] == 4096
    assert options["reasoning"] is False
    assert options["seed"] == 42


def test_single_product_structured_output_is_strict():
    with pytest.raises(ValidationError):
        ProductMatch(relevant="false", reason="Wrong type")
    with pytest.raises(ValidationError):
        ProductMatch(relevant=True, reason="Match", url="https://invented.cz/product")


def test_classifier_does_not_generate_or_receive_source_urls(monkeypatch):
    calls = []

    def invoke(prompt):
        calls.append(str(prompt))
        return ProductMatch(relevant=True, reason="Brand matches")

    monkeypatch.setattr(
        agents,
        "ChatOllama",
        lambda **kwargs: SimpleNamespace(
            with_structured_output=lambda *args, **kwargs: SimpleNamespace(invoke=invoke)
        ),
    )
    url = "https://shop.cz/do-not-regenerate-this-url"
    result = agents.SemanticValidationAgent().assess(
        [{"url": url, "title": "Lagavulin 16", "currency": "CZK"}], "lagavulin", get_region("czechia")
    )
    assert set(result) == {url}
    assert result[url][0] is True
    assert all(url not in prompt for prompt in calls)


def test_semantic_classifier_exposes_canonical_identity_for_grouping(monkeypatch):
    result = ProductMatch(relevant=True, reason="Same product family", canonical_product="Acme brewer")

    monkeypatch.setattr(agents.httpx, "get", lambda *a, **kw: SimpleNamespace(raise_for_status=lambda: None))
    monkeypatch.setattr(
        agents,
        "ChatOllama",
        lambda **kwargs: SimpleNamespace(
            with_structured_output=lambda *args, **kwargs: SimpleNamespace(invoke=lambda prompt: result)
        ),
    )
    url = "https://shop.cz/brewer"
    accepted, warnings = agents.SemanticValidationAgent().run(
        [{"url": url, "title": "Acme brewer black", "price": 100, "currency": "CZK", "shop": "Shop"}],
        "Acme brewer",
        get_region("czechia"),
    )
    assert not warnings
    assert accepted[0]["canonical_product"] == "Acme brewer"


def test_live_checker_does_not_accept_zero_results_or_wrong_brand():
    case = {"required": [r"\blagavulin\b"], "currency": "CZK"}
    assert violations_for(case, []) == ["zero_results"]
    assert violations_for(case, [{"title": "Vodka", "offers": [], "minimum_prices": {"CZK": 100}}])
