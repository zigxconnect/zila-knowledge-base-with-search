from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    api_key: str = "change-me"
    gemini_api_key: str = ""

    redis_url: str = "redis://localhost:6379/0"
    qdrant_url: str = "http://localhost:6333"
    collection: str = "course_content"
    upload_dir: str = "/data/uploads"

    embedding_model: str = "gemini-embedding-001"
    embedding_dim: int = 768

    max_upload_mb: int = 50
    allowed_departments: list[str] = ["ml", "web-frontend", "mobile", "cyber-security", "embedded-system","design","web-backend","gen-ai"]

    # ---- weekly opportunity digest ----
    tavily_api_key: str = ""
    llm_model: str = "gemini-2.5-flash"        # any Gemini text model your key can use

    zigex_api_url: str = ""                    # paginated students endpoint
    zigex_api_token: str = ""
    zigex_webhook_url: str = ""                # where finished digests are pushed
    zigex_webhook_secret: str = ""             # HMAC-SHA256 signing secret

    digest_timezone: str = "Africa/Douala"
    digest_home_country: str = "Cameroon"     # local first: student's city, then this country, then Africa/online
    digest_cron_day: str = "mon"               # mon..sun
    digest_cron_hour: int = 6
    digest_queries_per_segment: int = 6          # local first: 2 city, 2 country, 1 Africa, 1 online
    digest_max_items: int = 6
    digest_auto_publish: bool = True           # False = staff must approve before delivery
    digest_fetch_page_images: bool = True      # read each opportunity's own page image (Open Graph)
    digest_fetch_user_agent: str = "ZigexDigestBot/1.0 (link preview)"
    mock_mode: bool = False                    # fake page images for testing


settings = Settings()
