"""API de solo lectura del dashboard (API.md, SPEC.md 21).

**Solo lectura, sin excepciones.** No hay ni una ruta que abra o cierre una posicion, cambie
un limite o toque una clave. En Fase 1 no existe siquiera un motor de ejecucion al que
llamar, y esta restriccion es del contrato, no de la implementacion.

La API no calcula nada: sirve lo que el worker ha persistido, y reenvia por WebSocket lo que
el worker publica en Redis.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import redis.asyncio as redis
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import create_async_engine

from mit_api.queries import TokenQueries

CHANNEL_NEW_TOKENS = "mit:tokens.new"
CHANNEL_ANALYSIS = "mit:tokens.analysis"
HEARTBEAT_SECONDS = 15.0


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.engine = create_async_engine(os.environ["DATABASE_URL"], pool_size=5)
    app.state.queries = TokenQueries(app.state.engine)
    app.state.redis = redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))  # type: ignore[no-untyped-call]  # redis no anota from_url
    try:
        yield
    finally:
        await app.state.redis.aclose()
        await app.state.engine.dispose()


app = FastAPI(
    title="Memecoin Intelligence Terminal",
    version="0.1.0",
    description="API de solo lectura. Fase 1: deteccion de tokens, sin trading.",
    lifespan=lifespan,
)

# El dashboard corre en otro puerto en desarrollo. Solo localhost: esta API no se publica.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/status")
async def status() -> dict[str, Any]:
    """Estado del sistema. El modo se declara siempre, para que nunca haya duda."""
    return {
        "mode": "DRY_RUN",
        "phase": 1,
        "trading_enabled": False,
        "live_trading_enabled": os.environ.get("ENABLE_LIVE_TRADING", "false") == "true",
        "signer_mode": os.environ.get("SIGNER_MODE", "disabled"),
    }


@app.get("/v1/tokens")
async def tokens(limit: int = 50) -> dict[str, Any]:
    """Radar: ultimos tokens detectados (SPEC.md 21)."""
    queries: TokenQueries = app.state.queries
    capped = max(1, min(limit, 200))
    rows = await queries.recent_tokens(limit=capped)
    return {"count": len(rows), "tokens": rows}


@app.websocket("/v1/stream")
async def stream(websocket: WebSocket) -> None:
    """Tokens nuevos en vivo.

    Se reenvia lo que el worker publica en Redis. El heartbeat existe porque un WebSocket
    sin trafico lo cierra cualquier proxy intermedio, y el cliente no distinguiria un
    sistema tranquilo de uno muerto.
    """
    await websocket.accept()
    client = app.state.redis
    pubsub = client.pubsub()
    await pubsub.subscribe(CHANNEL_NEW_TOKENS, CHANNEL_ANALYSIS)

    try:
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=HEARTBEAT_SECONDS
            )
            if message is None:
                await websocket.send_text(json.dumps({"channel": "system", "event": "heartbeat"}))
                continue
            data = message["data"]
            payload = data.decode() if isinstance(data, bytes) else str(data)
            raw_channel = message.get("channel")
            channel_name = (
                raw_channel.decode() if isinstance(raw_channel, bytes) else str(raw_channel)
            )
            is_analysis = channel_name == CHANNEL_ANALYSIS
            await websocket.send_text(
                json.dumps(
                    {
                        "channel": "tokens.analysis" if is_analysis else "tokens.new",
                        "event": "analysis" if is_analysis else "token",
                        "payload": json.loads(payload),
                    }
                )
            )
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        with contextlib.suppress(Exception):
            await pubsub.unsubscribe(CHANNEL_NEW_TOKENS, CHANNEL_ANALYSIS)
            await pubsub.aclose()
