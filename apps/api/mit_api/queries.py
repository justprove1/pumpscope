"""Consultas de SOLO LECTURA de la API.

Deliberadamente separado del repositorio del worker: la API no escribe nunca, y no debe
poder hacerlo ni por accidente. Que la API importara el repositorio de escritura ademas
acoplaba dos aplicaciones que se despliegan por separado.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

_RECENT_TOKENS = text(
    """
    SELECT mint, symbol, name, uri, creator_address, created_at_slot,
           first_seen_at, detection_latency_ms, status
    FROM tokens
    ORDER BY first_seen_at DESC
    LIMIT :limit
    """
)


class TokenQueries:
    """Lecturas que alimentan el radar (SPEC.md 21)."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def recent_tokens(self, limit: int = 50) -> list[dict[str, Any]]:
        async with self._engine.connect() as connection:
            rows = (await connection.execute(_RECENT_TOKENS, {"limit": limit})).mappings().all()
        return [dict(row) for row in rows]

    async def count(self) -> int:
        async with self._engine.connect() as connection:
            value = (await connection.execute(text("SELECT count(*) FROM tokens"))).scalar()
        return int(value or 0)
