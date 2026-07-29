from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
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
    model_secret_key: SecretStr = SecretStr("development-only-change-this-model-secret-key")
    qianfan_api_key: SecretStr | None = None
    qianfan_base_url: str = "https://qianfan.baidubce.com/v2"
    model_max_retries: int = Field(default=2, ge=0, le=5)
    model_max_response_bytes: int = Field(default=5 * 1024 * 1024, ge=1024, le=50 * 1024 * 1024)
    model_circuit_failure_threshold: int = Field(default=3, ge=1, le=20)
    model_circuit_recovery_seconds: int = Field(default=30, ge=1, le=600)
    access_token_expire_minutes: int = 480
    jwt_algorithm: str = "HS256"
    log_level: str = "INFO"
    cors_origins: list[str] = [
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:8080",
        "http://localhost:8080",
    ]

    model_config = SettingsConfigDict(env_file=".env", env_prefix="COAL_")

    @model_validator(mode="after")
    def validate_production_baseline(self) -> "Settings":
        if self.environment != "production":
            return self
        errors = []
        if self.seed_demo_data:
            errors.append("COAL_SEED_DEMO_DATA must be false")
        if self.store_backend != "database" or self.database_url.startswith("sqlite"):
            errors.append("COAL_DATABASE_URL must use the production database")
        if self.storage_backend != "minio":
            errors.append("COAL_STORAGE_BACKEND must be minio")
        if len(self.secret_key) < 32 or self.secret_key.startswith("development-only"):
            errors.append("COAL_SECRET_KEY must be a random value of at least 32 characters")
        model_secret = self.model_secret_key.get_secret_value()
        if len(model_secret) < 32 or model_secret.startswith("development-only"):
            errors.append("COAL_MODEL_SECRET_KEY must be a random value of at least 32 characters")
        if any("localhost" in origin or "127.0.0.1" in origin for origin in self.cors_origins):
            errors.append("COAL_CORS_ORIGINS must not contain local development origins")
        if errors:
            raise ValueError("invalid production configuration: " + "; ".join(errors))
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
