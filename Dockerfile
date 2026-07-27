FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml ./
COPY coal_platform ./coal_platform
COPY alembic.ini ./
COPY migrations ./migrations

RUN python -m pip install --upgrade pip && python -m pip install .

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && exec uvicorn coal_platform.main:app --host 0.0.0.0 --port 8000"]
