from app.agents import SemanticValidationAgent
from app.regions import get_region


def test_deterministic_semantic_guard_rejects_generic_foreign_store_page():
    relevant, region_match, _ = SemanticValidationAgent._deterministic_assessment(
        {
            "title": "Buy Whiskey Online | Delivered to Your Door",
            "url": "https://thebarreltap.com/whiskey",
            "currency": "USD",
        },
        get_region("czechia"),
    )
    assert relevant is False
    assert region_match is False


def test_deterministic_semantic_guard_accepts_czech_product_offer():
    relevant, region_match, _ = SemanticValidationAgent._deterministic_assessment(
        {
            "title": "Johnnie Walker Blue Label 1 l",
            "url": "https://example-shop.cz/johnnie-walker-blue-label",
            "currency": "CZK",
        },
        get_region("czechia"),
    )
    assert relevant is True
    assert region_match is True
