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

# PYTHONPATH en vez de `pip install -e .`: instalar el proyecto entero arrastraria los 17
# modulos del monorepo a la imagen del signer. Su superficie minima no es una
# comodidad, es su propiedad de seguridad (SECURITY.md 2): cuanto menos codigo tenga el
# unico proceso con acceso a la clave, menos puede salir mal.
ENV PYTHONPATH=/app/apps/signer:/app/packages/shared:/app/packages/data-models:/app/packages/solana

RUN useradd --create-home --uid 10002 signer && chown -R signer /app
USER signer

# Fase 1: SIGNER_MODE=disabled. Arranca, declara su modo y no firma nada.
# La firma real es Fase 6, tras completar LIVE_TRADING_CHECKLIST.md.
CMD ["python", "-m", "mit_signer"]
