"""Persistencia de tokens detectados (SPEC.md 23) con trazabilidad (SPEC.md 5).

Se usa SQLAlchemy Core y no ORM: en Fase 1 no hay modelos declarativos todavia, y escribir
el INSERT explicito deja ver exactamente que columnas se tocan.

Toda escritura es IDEMPOTENTE (`ON CONFLICT DO NOTHING`). No es una precaucion decorativa:
tras una reconexion el proveedor reenvia eventos, el proceso puede reiniciarse a mitad de
lote, y el mismo token puede llegar por dos proveedores distintos. La deduplicacion en
memoria del detector no sobrevive a un reinicio; la base de datos sI.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from mit_pumpfun.detector import DetectedToken
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

# Cada observacion que viene de un proveedor lleva el envelope de SPEC.md 5.
_INSERT_CREATOR = text(
    """
    INSERT INTO creators (address, first_seen_at, tokens_created)
    VALUES (:address, :first_seen_at, 1)
    ON CONFLICT (address) DO UPDATE
        SET tokens_created = creators.tokens_created + 1,
            updated_at = now()
    """
)

_INSERT_TOKEN = text(
    """
    INSERT INTO tokens (
        mint, creator_address, symbol, name, uri, total_supply,
        platform, status, created_at_slot, created_at, first_seen_at,
        detection_latency_ms
    ) VALUES (
        :mint, :creator, :symbol, :name, :uri, :total_supply,
        'pumpfun', 'new', :slot, :created_at, :first_seen_at,
        :detection_latency_ms
    )
    ON CONFLICT (mint) DO NOTHING
    RETURNING mint
    """
)

_INSERT_CURVE_SNAPSHOT = text(
    """
    INSERT INTO bonding_curve_snapshots (
        observed_at, mint,
        virtual_sol_reserves, virtual_token_reserves, real_token_reserves,
        is_complete,
        provider, provider_timestamp, received_timestamp, blockchain_slot,
        confidence, latency_ms, raw_reference
    ) VALUES (
        :observed_at, :mint,
        :virtual_sol, :virtual_token, :real_token,
        false,
        :provider, :provider_timestamp, :received_timestamp, :slot,
        :confidence, :latency_ms, :raw_reference
    )
    ON CONFLICT (observed_at, mint) DO NOTHING
    """
)


@dataclass(frozen=True, slots=True)
class SaveResult:
    """Resultado de persistir una deteccion."""

    inserted: bool
    latency_ms: float


class TokenRepository:
    """Escribe tokens detectados y su trazabilidad."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def save_detection(self, token: DetectedToken) -> SaveResult:
        """Guarda creador, token y snapshot inicial de la curva en una transaccion.

        Devuelve `inserted=False` si el mint ya existia. No es un error: es el caso normal
        cuando el proveedor reenvia una ventana ya procesada.
        """
        started = time.perf_counter()
        event = token.event

        async with self._engine.begin() as connection:
            # El creador va primero: `tokens.creator_address` tiene FK contra `creators`.
            await connection.execute(
                _INSERT_CREATOR,
                {"address": event.creator, "first_seen_at": token.received_timestamp},
            )

            result = await connection.execute(
                _INSERT_TOKEN,
                {
                    "mint": event.mint,
                    "creator": event.creator,
                    "symbol": event.symbol,
                    "name": event.name,
                    "uri": event.uri,
                    "total_supply": event.token_total_supply,
                    "slot": token.slot,
                    "created_at": token.received_timestamp,
                    "first_seen_at": token.received_timestamp,
                    # SPEC.md 6 mide la deteccion DESDE que el evento llega al
                    # proveedor, no desde que entra en nuestro proceso. Lo que domina
                    # es el retraso del proveedor (segundos), no nuestro pipeline
                    # (decimas de milisegundo). Truncar este ultimo a entero daba 0 en
                    # todas las filas: una columna llena de ceros no mide nada.
                    "detection_latency_ms": _detection_latency_ms(token),
                },
            )
            inserted = result.first() is not None

            if inserted:
                await connection.execute(
                    _INSERT_CURVE_SNAPSHOT,
                    {
                        "observed_at": token.received_timestamp,
                        "mint": event.mint,
                        "virtual_sol": event.virtual_sol_reserves,
                        "virtual_token": event.virtual_token_reserves,
                        "real_token": event.real_token_reserves,
                        "provider": token.provider,
                        "provider_timestamp": _to_timestamp(event.timestamp),
                        "received_timestamp": token.received_timestamp,
                        "slot": token.slot,
                        # Dato leido del propio evento del programa: no hay intermediario
                        # que pueda haberlo alterado, asi que la confianza es maxima.
                        "confidence": 1.0,
                        "latency_ms": _detection_latency_ms(token),
                        "raw_reference": token.raw_reference,
                    },
                )

        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return SaveResult(inserted=inserted, latency_ms=elapsed_ms)

    async def recent_tokens(self, limit: int = 50) -> list[dict[str, object]]:
        """Ultimos tokens detectados, para el radar de solo lectura."""
        query = text(
            """
            SELECT mint, symbol, name, uri, creator_address, created_at_slot,
                   first_seen_at, detection_latency_ms, status
            FROM tokens
            ORDER BY first_seen_at DESC
            LIMIT :limit
            """
        )
        async with self._engine.connect() as connection:
            rows = (await connection.execute(query, {"limit": limit})).mappings().all()
        return [dict(row) for row in rows]

    async def count(self) -> int:
        async with self._engine.connect() as connection:
            value = (await connection.execute(text("SELECT count(*) FROM tokens"))).scalar()
        return int(value or 0)


def _detection_latency_ms(token: DetectedToken) -> int:
    """Latencia de deteccion end-to-end, en milisegundos.

    Usa el retraso on-chain cuando existe: es el numero contra el que se mide el
    objetivo de SPEC.md 6. Su resolucion es de segundos, porque el evento trae el
    tiempo en segundos; no se finge precision que el dato no tiene.

    Si no hay retraso on-chain, cae al del pipeline propio redondeado hacia arriba,
    para no registrar 0 en algo que si tardo.
    """
    if token.onchain_lag_seconds is not None and token.onchain_lag_seconds >= 0:
        return token.onchain_lag_seconds * 1000
    return max(1, round(token.pipeline_latency_ms))


def _to_timestamp(unix_seconds: int) -> object:
    """Convierte segundos epoch a datetime con zona, o None si no hay dato."""
    if unix_seconds <= 0:
        return None
    from datetime import UTC, datetime

    return datetime.fromtimestamp(unix_seconds, tz=UTC)
