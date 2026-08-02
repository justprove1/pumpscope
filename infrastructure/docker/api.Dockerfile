# STUB Fase 0: la imagen se construye pero mit_api todavia no expone una app.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY packages/ ./packages/
COPY apps/api/ ./apps/api/
RUN pip install --no-cache-dir -e .

RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app

EXPOSE 8000
CMD ["uvicorn", "mit_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
