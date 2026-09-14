import pytest
from pydantic import ValidationError

from app.schemas import SearchRequest


def test_search_request_accepts_supported_region():
    assert SearchRequest(query="coffee grinder", region="czechia").region == "czechia"


def test_search_request_rejects_unknown_region():
    with pytest.raises(ValidationError):
        SearchRequest(query="coffee grinder", region="moon")
