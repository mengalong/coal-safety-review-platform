import pytest
from pydantic import ValidationError

from coal_platform.config import Settings


def production_settings(**overrides) -> Settings:
    values = {
        "environment": "production",
        "store_backend": "database",
        "seed_demo_data": False,
        "database_url": "postgresql+psycopg://coal:secret@postgres:5432/coal",
        "storage_backend": "minio",
        "secret_key": "a" * 32,
        "metrics_token": "c" * 32,
        "model_secret_key": "b" * 32,
        "cors_origins": ["https://coal-review.example.com"],
    }
    values.update(overrides)
    return Settings(**values)


def test_production_settings_accept_secure_external_services() -> None:
    settings = production_settings()
    assert settings.environment == "production"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"seed_demo_data": True}, "COAL_SEED_DEMO_DATA"),
        ({"database_url": "sqlite:///coal.db"}, "COAL_DATABASE_URL"),
        ({"storage_backend": "local"}, "COAL_STORAGE_BACKEND"),
        ({"secret_key": "short"}, "COAL_SECRET_KEY"),
        ({"metrics_token": "short"}, "COAL_METRICS_TOKEN"),
        ({"model_secret_key": "short"}, "COAL_MODEL_SECRET_KEY"),
        ({"cors_origins": ["http://localhost:8080"]}, "COAL_CORS_ORIGINS"),
    ],
)
def test_production_settings_reject_insecure_baseline(overrides: dict, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        production_settings(**overrides)
