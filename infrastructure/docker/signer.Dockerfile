# Servicio de firma AISLADO (SPEC.md 16, SECURITY.md 2).
# Superficie deliberadamente minima: solo mit_signer y mit_shared, sin el resto del monorepo.
# No publica puertos. No tiene acceso a la base de datos. No conoce estrategias.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY pyproject.toml README.md ./
COPY packages/shared/ ./packages/shared/
COPY packages/data-models/ ./packages/data-models/
COPY packages/solana/ ./packages/solana/
COPY apps/signer/ ./apps/signer/
RUN pip install --no-cache-dir pynacl solders fastapi "uvicorn[standard]" pydantic pydantic-settings

RUN useradd --create-home --uid 10002 signer && chown -R signer /app
USER signer

# STUB Fase 0: SIGNER_MODE=disabled por defecto; el proceso arranca y rechaza todo.
CMD ["python", "-m", "mit_signer"]
