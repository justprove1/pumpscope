"""Servicio de ingesta: stream -> detector -> base de datos -> Redis (SPEC.md 6).

Une las piezas y no hace nada mas. Toda la logica interesante vive en modulos que se pueden
probar sin red ni base de datos; aqui solo se cablea.

Publica cada token nuevo en Redis para que la API lo reenvie por WebSocket al dashboard. Se
usa pub/sub y no que la API consulte la base en bucle porque el objetivo es tiempo real: un
sondeo cada segundo anadiria hasta un segundo de retraso al presupuesto de SPEC.md 6, que ya
es de un segundo en total.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
from dataclasses import dataclass
from typing import Any

import redis.asyncio as redis
import websockets
from mit_observability.metrics import IngestMetrics
from mit_pumpfun.constants import PUMPFUN_PROGRAM_ID
from mit_pumpfun.detector import DetectedToken, NewTokenDetector
from mit_solana.logs_stream import ResilientLogStream
from mit_solana.rpc import RpcLimits, SolanaRpc
from sqlalchemy.ext.asyncio import create_async_engine

from mit_worker.analysis import AnalysisPipeline
from mit_worker.repository import TokenRepository

LOGGER = logging.getLogger("mit.ingest")
CHANNEL_NEW_TOKENS = "mit:tokens.new"

DEFAULT_WSS = "wss://api.mainnet-beta.solana.com"


@dataclass(frozen=True, slots=True)
class IngestConfig:
    database_url: str
    redis_url: str
    wss_url: str
    provider: str

    @classmethod
    def from_env(cls) -> IngestConfig:
        # HELIUS_WSS_URL solo se usa si hay clave: sin ella la URL trae un `api-key=`
        # vacio y el proveedor rechaza la conexion. Mejor caer al endpoint publico, que
        # funciona, que fallar por una credencial a medio poner.
        helius_key = os.environ.get("HELIUS_API_KEY", "").strip()
        helius_wss = os.environ.get("HELIUS_WSS_URL", "").strip()
        if helius_key and helius_wss:
            wss_url, provider = helius_wss, "helius"
        else:
            wss_url = os.environ.get("SOLANA_FALLBACK_WSS_URL", DEFAULT_WSS)
            provider = "solana-public-rpc"

        return cls(
            database_url=os.environ["DATABASE_URL"],
            redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
            wss_url=wss_url,
            provider=provider,
        )


def token_payload(token: DetectedToken) -> dict[str, Any]:
    """Representacion del token para el dashboard. Solo lectura, sin nada accionable."""
    return {
        "mint": token.mint,
        "name": token.event.name,
        "symbol": token.event.symbol,
        "uri": token.event.uri,
        "creator": token.event.creator,
        "user": token.event.user,
        "bonding_curve": token.event.bonding_curve,
        "slot": token.slot,
        "signature": token.signature,
        "provider": token.provider,
        "received_timestamp": token.received_timestamp.isoformat(),
        "pipeline_latency_ms": round(token.pipeline_latency_ms, 3),
        "onchain_lag_seconds": token.onchain_lag_seconds,
    }


class IngestService:
    """Bucle de ingesta. Se detiene limpiamente al recibir SIGINT/SIGTERM."""

    def __init__(self, config: IngestConfig) -> None:
        self._config = config
        self._engine = create_async_engine(config.database_url, pool_size=5, max_overflow=5)
        self._repository = TokenRepository(self._engine)
        self._redis = redis.from_url(config.redis_url)  # type: ignore[no-untyped-call]  # redis no anota from_url
        self._detector = NewTokenDetector(provider=config.provider)
        # Ritmo del analisis MUY por debajo del limite del endpoint publico: la ingesta
        # tiene prioridad, y perder el WebSocket por agotar cuota analizando seria un
        # mal negocio.
        self._rpc = SolanaRpc(limits=RpcLimits(requests_per_second=2.0))
        self._analysis = AnalysisPipeline(self._rpc, self._redis)
        self.metrics = IngestMetrics()
        self._stop = asyncio.Event()

    def request_stop(self) -> None:
        self._stop.set()

    async def close(self) -> None:
        await self._rpc.close()
        await self._redis.aclose()
        await self._engine.dispose()

    async def _connect(self) -> Any:
        return await websockets.connect(self._config.wss_url, ping_interval=20, max_size=20_000_000)

    async def _handle(self, notification: dict[str, Any]) -> None:
        self.metrics.events_received += 1
        token = self._detector.observe(notification)
        if token is None:
            self.metrics.events_discarded += 1
            return

        self.metrics.tokens_detected += 1
        self.metrics.pipeline_latency.record(token.pipeline_latency_ms)

        try:
            result = await self._repository.save_detection(token)
            self.metrics.persistence_latency.record(result.latency_ms)
        except Exception:
            # Un fallo de escritura no puede tumbar la ingesta: se cuenta, se registra y se
            # sigue. Perder un token es malo; dejar de escuchar es peor.
            self.metrics.persistence_errors += 1
            LOGGER.exception("fallo al persistir %s", token.mint)
            return

        if not result.inserted:
            self.metrics.duplicates += 1
            return

        payload = token_payload(token)
        LOGGER.info(json.dumps({"event": "token_detected", **payload}))
        # Se encola para analisis. Si la cola esta llena se descarta y se cuenta: el token
        # queda registrado igual, solo sin veredicto.
        self._analysis.submit(token)
        with contextlib.suppress(Exception):
            await self._redis.publish(CHANNEL_NEW_TOKENS, json.dumps(payload))

    async def run(self) -> None:
        stream = ResilientLogStream(PUMPFUN_PROGRAM_ID, self._connect)
        LOGGER.info(
            json.dumps(
                {
                    "event": "ingest_started",
                    "provider": self._config.provider,
                    "program": PUMPFUN_PROGRAM_ID,
                }
            )
        )
        consumer = asyncio.create_task(self._consume(stream))
        analyst = asyncio.create_task(self._analysis.run())
        stopper = asyncio.create_task(self._stop.wait())
        done, pending = await asyncio.wait(
            {consumer, stopper, analyst}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        for task in done:
            if task is consumer and not task.cancelled():
                task.result()

    async def _consume(self, stream: ResilientLogStream) -> None:
        async for notification in stream:
            self.metrics.reconnections = stream.stats.reconnects
            self.metrics.connection_failures = stream.stats.silence_timeouts
            await self._handle(notification)


async def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(message)s",
    )
    service = IngestService(IngestConfig.from_env())

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, service.request_stop)

    try:
        await service.run()
    finally:
        await service.close()
        LOGGER.info(json.dumps({"event": "ingest_stopped", **service.metrics.snapshot()}))


if __name__ == "__main__":
    asyncio.run(main())
