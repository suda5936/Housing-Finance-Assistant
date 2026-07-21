from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables or .env."""

    app_env: str = "development"
    app_name: str = "homefit-ai"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:3000"
    database_url: str = "postgresql+psycopg://homefit:local_homefit_password@localhost:5432/homefit"
    upload_dir: str = ".local-data/uploads"
    document_retention_hours: int = 24
    llm_provider: str = "ollama"
    llm_enabled: bool = True
    llm_model: str = "qwen3:4b"
    ollama_base_url: str = "http://127.0.0.1:11434"
    llm_context_tokens: int = 8192
    llm_max_output_tokens: int = 1024
    llm_timeout_seconds: int = 30
    ocr_enabled: bool = False
    ocr_provider: str = "mock"
    map_provider: str = "manual"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
