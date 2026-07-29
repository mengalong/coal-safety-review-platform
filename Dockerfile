ARG PYTHON_BASE_IMAGE=public.ecr.aws/docker/library/python:3.12-slim
FROM ${PYTHON_BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-wqy-zenhei tesseract-ocr tesseract-ocr-chi-sim \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY coal_platform ./coal_platform
COPY alembic.ini ./
COPY migrations ./migrations

RUN python -m pip install --upgrade pip && python -m pip install .

RUN addgroup --system coal && adduser --system --ingroup coal coal && chown -R coal:coal /app
USER coal

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/healthz', timeout=3)"

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && exec uvicorn coal_platform.main:app --host 0.0.0.0 --port 8000"]
