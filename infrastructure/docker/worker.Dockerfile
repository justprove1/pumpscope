FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY packages/ ./packages/
COPY apps/worker/ ./apps/worker/
RUN pip install --no-cache-dir -e .

RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app

CMD ["python", "-m", "mit_worker.ingest"]
