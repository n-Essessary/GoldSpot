from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="WOW_GOLD_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    g2g_offers_url: str = ""
    refresh_interval_seconds: int = Field(default=300, ge=10)
    use_mock_on_fetch_failure: bool = True


settings = Settings()
