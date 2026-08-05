"""Consultas de SOLO LECTURA de la API.

Deliberadamente separado del repositorio del worker: la API no escribe nunca, y no debe
poder hacerlo ni por accidente. Que la API importara el repositorio de escritura ademas
acoplaba dos aplicaciones que se despliegan por separado.
"""

from __future__ import annotations

from typing import Any

from mit_pumpfun.curve import CurveError, CurveState, market_cap_lamports
from mit_shared.types import LAMPORTS_PER_SOL
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

# Se une el ultimo snapshot de la curva de cada token para poder mostrar la capitalizacion ya
# en la carga inicial, sin esperar a la primera operacion en vivo.
_RECENT_TOKENS = text(
    """
    SELECT t.mint, t.symbol, t.name, t.uri, t.creator_address, t.created_at_slot,
           t.first_seen_at, t.detection_latency_ms, t.status, t.total_supply,
           s.virtual_sol_reserves, s.virtual_token_reserves,
           c.tokens_created AS creator_launches
    FROM tokens t
    LEFT JOIN creators c ON c.address = t.creator_address
    LEFT JOIN LATERAL (
        SELECT virtual_sol_reserves, virtual_token_reserves
        FROM bonding_curve_snapshots b
        WHERE b.mint = t.mint
        ORDER BY b.observed_at DESC
        LIMIT 1
    ) s ON true
    ORDER BY t.first_seen_at DESC
    LIMIT :limit
    """
)


def _market_cap_sol(row: dict[str, Any]) -> float | None:
    """Capitalizacion en SOL desde el snapshot de la fila. None si faltan reservas."""
    vsol = row.get("virtual_sol_reserves")
    vtok = row.get("virtual_token_reserves")
    supply = row.get("total_supply")
    if not vsol or not vtok or not supply:
        return None
    try:
        curve = CurveState(
            virtual_sol_reserves=int(vsol),
            virtual_token_reserves=int(vtok),
            real_token_reserves=0,
            token_total_supply=int(supply),
        )
    except (CurveError, ValueError, TypeError):
        return None
    return round(market_cap_lamports(curve) / LAMPORTS_PER_SOL, 9)


class TokenQueries:
    """Lecturas que alimentan el radar (SPEC.md 21)."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def recent_tokens(self, limit: int = 50) -> list[dict[str, Any]]:
        async with self._engine.connect() as connection:
            rows = (await connection.execute(_RECENT_TOKENS, {"limit": limit})).mappings().all()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["market_cap_sol"] = _market_cap_sol(item)
            # Las reservas crudas no son para el dashboard: se exponen solo como la cap derivada.
            item.pop("virtual_sol_reserves", None)
            item.pop("virtual_token_reserves", None)
            result.append(item)
        return result

    async def count(self) -> int:
        async with self._engine.connect() as connection:
            value = (await connection.execute(text("SELECT count(*) FROM tokens"))).scalar()
        return int(value or 0)
