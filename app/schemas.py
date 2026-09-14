import ipaddress
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.regions import REGIONS
from app.urls import canonical_offer_url


class SearchRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    query: str = Field(min_length=2, max_length=200, examples=["wireless noise cancelling headphones"])
    max_results: int = Field(default=20, ge=1, le=20)
    region: str = Field(default="czechia")
    search_mode: Literal["quick", "thorough"] = "thorough"

    @field_validator("region")
    @classmethod
    def validate_region(cls, value: str) -> str:
        if value not in REGIONS:
            raise ValueError("Choose one of the supported shopping regions.")
        return value


class ProductOffer(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, str_strip_whitespace=True)
    title: str = Field(min_length=2, max_length=500)
    price: float = Field(gt=0, lt=10_000_000)
    currency: str | None = None
    shop: str
    url: str
    image_url: str | None = None
    source_url: str | None = None
    availability: str | None = None
    score: float = 0
    gtin: str | None = None
    brand: str | None = Field(default=None, max_length=150)
    mpn: str | None = Field(default=None, max_length=150)
    color: str | None = Field(default=None, max_length=150)
    size: str | None = Field(default=None, max_length=150)

    @field_validator("url", "image_url", "source_url")
    @classmethod
    def safe_url(cls, value, info):
        if value is None:
            return value
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ValueError("A public HTTP(S) source URL is required")
        if parsed.port not in {None, 80, 443} or parsed.hostname.lower().rstrip(".") in {
            "localhost",
            "localhost.localdomain",
        }:
            raise ValueError("Private URLs and non-web ports are not allowed")
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise ValueError("Private IP addresses are not allowed")
        return canonical_offer_url(value) if info.field_name != "image_url" else value

    @field_validator("currency")
    @classmethod
    def currency_code(cls, value):
        if value is not None and (len(value) != 3 or not value.isascii() or not value.isalpha()):
            raise ValueError("Use an ISO currency code")
        return value.upper() if value else None


class ObservedOffer(ProductOffer):
    product_id: str
    region: str
    checked_at: float = Field(gt=0)
    stale: bool


class ProductRecord(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    id: str
    title: str
    image_url: str | None = None
    offers: list[ObservedOffer]
    minimum_prices: dict[str, float]
    offer_count: int = Field(ge=0)
    region: str | None = None


class SearchResponse(BaseModel):
    query: str
    planned_query: str
    region: str
    stores_searched: list[str] = Field(default_factory=list)
    products: list[ProductRecord]
    warnings: list[str] = Field(default_factory=list)
    pipeline: list[str] = Field(default_factory=list)
    planner_diagnostics: dict[str, int | bool | str] = Field(default_factory=dict)
    run_id: str | None = None


class QueryPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    search_query: str = Field(
        min_length=2,
        max_length=300,
        description="A concise web query intended to find product pages with live prices.",
    )
    alternatives: list[str] = Field(min_length=1, max_length=3,
                                    description="One to three equivalent search rewrites. Include a translation into the requested market language when useful.")
    anchors: list[str] = Field(default_factory=list, max_length=12,
                              description="Brands and model identifiers ONLY, quoted exactly from the request. Never include generic product types or descriptions; they must remain translatable.")


class RewriteReview(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    index: int = Field(ge=0, le=3, description="The zero-based rewrite index being reviewed.")
    brands_and_models: list[str] = Field(default_factory=list, max_length=12,
        description="Only brand names and model identifiers quoted from the ORIGINAL request, not product types or descriptions.")
    added_constraints: list[str] = Field(default_factory=list, max_length=12,
        description="Attributes present in the rewrite but absent from the original request. Empty means none.")
    dropped_constraints: list[str] = Field(default_factory=list, max_length=12,
        description="Attributes required by the original but missing from the rewrite. Empty means none.")
    preserves_intent: bool
    reason: str = Field(min_length=1, max_length=160)

class OfferAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    url: str
    relevant: bool
    region_match: bool
    confidence: float = Field(ge=0, le=1)


class OfferAssessments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assessments: list[OfferAssessment] = Field(min_length=1, max_length=30)


class ProductMatch(BaseModel):
    """One semantic decision; source URLs are retained by code, never regenerated."""

    model_config = ConfigDict(extra="forbid", strict=True)
    relevant: bool
    reason: str = Field(min_length=1, max_length=240)
    canonical_product: str | None = Field(
        default=None,
        min_length=2,
        max_length=300,
        description=(
            "Canonical product identity for cross-store grouping. Remove seller boilerplate and cosmetic "
            "colour/condition wording, but preserve model, edition, capacity, size, strength and pack details. "
            "Return null when uncertain."
        ),
    )
