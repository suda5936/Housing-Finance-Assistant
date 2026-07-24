from functools import lru_cache
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables or .env."""

    app_env: str = "development"
    app_name: str = "homefit-ai"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:3000"
    trusted_hosts: str = "localhost,127.0.0.1,testserver"
    max_request_bytes: int = Field(default=12 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024)
    enable_hsts: bool = False
    database_url: str = "postgresql+psycopg://homefit:local_homefit_password@localhost:5432/homefit"
    data_repository: str = "memory"
    upload_dir: str = ".local-data/uploads"
    document_retention_hours: int = 24
    retention_purge_interval_seconds: int = Field(default=300, ge=30, le=86_400)
    llm_provider: str = "ollama"
    llm_enabled: bool = True
    llm_model: str = "qwen3:4b"
    ollama_base_url: str = "http://127.0.0.1:11434"
    llm_context_tokens: int = 8192
    llm_max_output_tokens: int = 1024
    llm_timeout_seconds: int = 30
    ocr_enabled: bool = False
    ocr_provider: str = "tesseract"
    tesseract_command: str = "tesseract"
    tesseract_languages: str = "kor+eng"
    document_max_bytes: int = 10 * 1024 * 1024
    document_max_pages: int = 10
    document_max_pixels: int = 25_000_000
    document_processing_timeout_seconds: int = 30
    map_provider: str = "manual"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def trusted_host_list(self) -> list[str]:
        return [host.strip() for host in self.trusted_hosts.split(",") if host.strip()]

    @property
    def ollama_is_local(self) -> bool:
        hostname = urlparse(self.ollama_base_url).hostname
        if hostname == "localhost":
            return True
        try:
            return bool(hostname and ip_address(hostname).is_loopback)
        except ValueError:
            return False

    @property
    def resolved_upload_dir(self) -> Path:
        return Path(self.upload_dir).resolve()

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
