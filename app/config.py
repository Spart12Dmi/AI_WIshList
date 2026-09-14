from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables or a local .env file."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Local Product Finder"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3:4b"
    llm_context_tokens: int = Field(default=4096, ge=2048, le=16384)
    use_llm_planner: bool = True
    llm_timeout_seconds: float = Field(default=60.0, ge=1, le=120)
    database_path: str = "data/wishlist.sqlite3"
    session_days: int = Field(default=7, ge=1, le=30)
    secure_cookies: bool = False
    use_semantic_validation: bool = True
    search_timeout_seconds: int = Field(default=180, ge=10, le=600)
    max_concurrent_searches: int = Field(default=2, ge=1, le=8)

    store_limit: int = Field(default=15, ge=1, le=30)
    search_backends: str = "bing,yandex,brave,yahoo"
    search_cache_ttl_seconds: int = Field(default=300, ge=0, le=3600)
    store_discovery_result_limit: int = Field(default=30, ge=1, le=50)
    store_search_workers: int = Field(default=8, ge=1, le=15)
    per_store_product_limit: int = Field(default=3, ge=1, le=10)
    extraction_workers: int = Field(default=6, ge=1, le=15)
    http_timeout_seconds: float = Field(default=8.0, ge=1, le=30)
    max_page_bytes: int = Field(default=2_000_000, ge=1000, le=10_000_000)

    use_browser_fallback: bool = True
    browser_candidate_limit: int = Field(default=12, ge=0, le=60)
    browser_headless: bool = True
    browser_navigation_timeout_ms: int = Field(default=15_000, ge=1000, le=60000)
    browser_render_wait_ms: int = Field(default=1_200, ge=0, le=10000)


@lru_cache
def get_settings() -> Settings:
    return Settings()
