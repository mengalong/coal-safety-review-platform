from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "煤矿安标技术文档智能审核平台"
    environment: str = "development"
    store_backend: Literal["database", "demo"] = "database"
    seed_demo_data: bool = True
    api_v1_prefix: str = "/api/v1"
    database_url: str = "sqlite+pysqlite:///./coal.db"
    redis_url: str = "redis://localhost:6379/0"
    dispatch_jobs: bool = False
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "coal"
    minio_secret_key: str = "coal-local-secret"
    minio_bucket: str = "coal-review"
    minio_secure: bool = False
    storage_backend: Literal["local", "minio"] = "local"
    local_storage_path: str = "./data/uploads"
    ocr_backend: Literal["disabled", "tesseract"] = "disabled"
    ocr_languages: str = Field(default="chi_sim+eng", min_length=1, max_length=64)
    ocr_dpi: int = Field(default=200, ge=72, le=600)
    ocr_timeout_seconds: int = Field(default=120, ge=1, le=600)
    ocr_minimum_confidence: float = Field(default=0.35, ge=0, le=1)
    secret_key: str = "development-only-change-this-secret-key"
    access_token_expire_minutes: int = 480
    jwt_algorithm: str = "HS256"
    log_level: str = "INFO"
    cors_origins: list[str] = ["http://127.0.0.1:65513", "http://localhost:65513"]

    model_config = SettingsConfigDict(env_file=".env", env_prefix="COAL_")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
