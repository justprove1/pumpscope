"""Suscripcion resiliente a logs de un programa de Solana (SPEC.md 4.A y 25).

El endpoint publico corta la conexion con frecuencia, y uno de pago tambien acaba
cortandola. La pregunta no es si se va a caer, sino que pasa cuando se caiga. Aqui:

- **Reconexion automatica** con backoff exponencial y jitter. El jitter no es un adorno: sin
  el, N clientes que se caen a la vez reintentan a la vez y rematan al proveedor justo
  cuando se estaba recuperando.
- **Heartbeat por ausencia de datos.** Un socket TCP puede seguir "abierto" mucho despues de
  que el otro extremo haya dejado de enviar nada. Si no llega ningun mensaje en
  `silence_timeout`, se da por muerta y se reconecta. Sin esto el proceso se queda vivo,
  sano de aspecto y ciego.
- **El iterador NO termina** porque se caiga la conexion. Solo termina si se cancela desde
  fuera. Para el consumidor, una caida es invisible.

La deduplicacion NO esta aqui a proposito: vive en el detector, porque tambien hay que
deduplicar entre reinicios del proceso, no solo entre reconexiones.

La conexion se inyecta (`connect`), lo que permite probar cortes y reconexiones de forma
determinista, sin red.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

LOGGER = logging.getLogger("mit.solana.logs_stream")


class LogConnection(Protocol):
    """Lo minimo que necesita el stream de una conexion WebSocket."""

    async def send(self, message: str) -> None: ...

    async def recv(self) -> str | bytes: ...

    async def close(self) -> None: ...


ConnectFactory = Callable[[], Awaitable[LogConnection]]


@dataclass
class StreamStats:
    connects: int = 0
    reconnects: int = 0
    messages: int = 0
    silence_timeouts: int = 0
    subscribe_failures: int = 0


class SubscriptionRejectedError(RuntimeError):
    """El proveedor rechazo la suscripcion.

    NO se reintenta indefinidamente: suele significar credencial invalida o un metodo no
    soportado por el endpoint, y reintentar solo consume cuota.
    """


class ResilientLogStream:
    """Itera notificaciones de `logsSubscribe`, sobreviviendo a cortes de conexion."""

    def __init__(
        self,
        program_id: str,
        connect: ConnectFactory,
        *,
        commitment: str = "confirmed",
        silence_timeout: float = 30.0,
        initial_backoff: float = 0.5,
        max_backoff: float = 30.0,
        backoff_multiplier: float = 2.0,
        max_reconnects: int | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self._program_id = program_id
        self._connect = connect
        self._commitment = commitment
        self._silence_timeout = silence_timeout
        self._initial_backoff = initial_backoff
        self._max_backoff = max_backoff
        self._multiplier = backoff_multiplier
        # `max_reconnects` existe para los tests y para modos de un solo intento. En
        # produccion es None: el proceso no se rinde.
        self._max_reconnects = max_reconnects
        self._rng = rng or random.Random()
        self.stats = StreamStats()

    def _subscribe_payload(self) -> str:
        return json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "logsSubscribe",
                "params": [
                    {"mentions": [self._program_id]},
                    {"commitment": self._commitment},
                ],
            }
        )

    def _next_backoff(self, current: float) -> float:
        """Backoff exponencial con jitter completo, acotado."""
        capped = min(current * self._multiplier, self._max_backoff)
        return self._rng.uniform(0.0, capped)

    async def _drain(self, connection: LogConnection) -> AsyncIterator[dict[str, Any]]:
        """Consume una conexion ya suscrita hasta que muera o se quede en silencio."""
        while True:
            raw = await asyncio.wait_for(connection.recv(), timeout=self._silence_timeout)
            self.stats.messages += 1
            try:
                message = json.loads(raw)
            except (ValueError, TypeError):
                # Un mensaje ilegible no justifica tirar la conexion entera.
                continue
            if message.get("method") == "logsNotification" or "params" in message:
                yield message

    async def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        backoff = self._initial_backoff
        attempts = 0

        while True:
            connection: LogConnection | None = None
            try:
                connection = await self._connect()
                await connection.send(self._subscribe_payload())

                ack_raw = await asyncio.wait_for(connection.recv(), timeout=self._silence_timeout)
                ack = json.loads(ack_raw)
                if "error" in ack:
                    self.stats.subscribe_failures += 1
                    msg = f"logsSubscribe rechazado: {ack['error']}"
                    raise SubscriptionRejectedError(msg)

                self.stats.connects += 1
                # Solo se reinicia el backoff tras una suscripcion CONFIRMADA. Si se
                # reiniciara al conectar, un proveedor que acepta el socket y lo cierra
                # inmediatamente provocaria un bucle de reconexion a toda velocidad.
                backoff = self._initial_backoff
                attempts = 0

                async for notification in self._drain(connection):
                    yield notification

            except (SubscriptionRejectedError, asyncio.CancelledError):
                raise
            except (TimeoutError, OSError, ValueError, ConnectionError):
                self.stats.silence_timeouts += 1
            except Exception:  # el cliente WebSocket concreto puede lanzar lo que sea
                # Se registra en DEBUG y no se propaga: cualquier fallo de transporte
                # es motivo de reconexion, no de terminar el stream.
                LOGGER.debug("conexion perdida, se reconectara", exc_info=True)
            finally:
                if connection is not None:
                    with contextlib.suppress(Exception):
                        await connection.close()

            attempts += 1
            self.stats.reconnects += 1
            if self._max_reconnects is not None and attempts > self._max_reconnects:
                return

            delay = self._next_backoff(backoff)
            backoff = min(backoff * self._multiplier, self._max_backoff)
            await asyncio.sleep(delay)
