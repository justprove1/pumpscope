"""Suscripcion a logs de VARIAS cuentas sobre UNA sola conexion (SPEC.md 4.A).

**Por que existe, aparte de `ResilientLogStream`.** Aquel abre una conexion por programa, lo
cual es correcto cuando se sigue uno solo. Para seguir tokens ya graduados hace falta una
suscripcion `mentions=[mint]` POR MINT: el evento de PumpSwap no lleva el mint dentro, asi que
la unica forma de saber de que token es una operacion es que la suscripcion ya venga filtrada.

Abrir una conexion por mint no es viable: medido contra `api.mainnet-beta.solana.com`, seis
conexiones simultaneas devuelven `HTTP 429` y solo prospera una. Con una unica conexion y diez
suscripciones multiplexadas prosperan las diez. De ahi este modulo.

**Como se mapea una notificacion a su mint.** La respuesta a cada `logsSubscribe` trae un
`result` entero: el id de suscripcion. Cada notificacion posterior lo repite en
`params.subscription`. Se mantiene la tabla id -> clave y se resuelve con ella. Las peticiones
llevan ids propios (`_next_request_id`) para poder casar respuesta con peticion.

**Altas y bajas en caliente.** `watch()` y `unwatch()` se pueden llamar mientras el iterador
corre. Si hay conexion viva la suscripcion sale al momento; si no, queda apuntada y se envia
en la siguiente reconexion, que resuscribe todo el conjunto vigente.

Como en `ResilientLogStream`, el iterador NO termina porque se caiga la conexion, y la conexion
se inyecta para poder probar cortes sin red.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from mit_solana.logs_stream import ConnectFactory, LogConnection

LOGGER = logging.getLogger("mit.solana.multi_log_stream")


@dataclass
class MultiStreamStats:
    connects: int = 0
    reconnects: int = 0
    messages: int = 0
    notifications: int = 0
    subscribe_acks: int = 0
    subscribe_errors: int = 0
    unmapped: int = 0


@dataclass(frozen=True, slots=True)
class KeyedNotification:
    """Una notificacion ya atribuida a la clave (mint) cuya suscripcion la produjo."""

    key: str
    logs: list[str]
    signature: str
    slot: int


class MultiLogStream:
    """Multiplexa N suscripciones `logsSubscribe` sobre una unica conexion WebSocket."""

    def __init__(
        self,
        connect: ConnectFactory,
        *,
        commitment: str = "confirmed",
        silence_timeout: float = 60.0,
        initial_backoff: float = 0.5,
        max_backoff: float = 30.0,
        backoff_multiplier: float = 2.0,
        max_reconnects: int | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self._connect = connect
        self._commitment = commitment
        self._silence_timeout = silence_timeout
        self._initial_backoff = initial_backoff
        self._max_backoff = max_backoff
        self._multiplier = backoff_multiplier
        self._max_reconnects = max_reconnects
        self._rng = rng or random.Random()
        self._wanted: set[str] = set()
        self._connection: LogConnection | None = None
        self._request_to_key: dict[int, str] = {}
        self._sub_to_key: dict[int, str] = {}
        self._key_to_sub: dict[str, int] = {}
        self._request_id = 0
        self.stats = MultiStreamStats()

    # --- API publica ------------------------------------------------------------------

    @property
    def watched(self) -> frozenset[str]:
        return frozenset(self._wanted)

    @property
    def active(self) -> frozenset[str]:
        """Las que estan CONFIRMADAS por el proveedor, no solo pedidas."""
        return frozenset(self._key_to_sub)

    async def watch(self, key: str) -> None:
        """Empieza a seguir `key`. Idempotente."""
        if key in self._wanted:
            return
        self._wanted.add(key)
        if self._connection is not None:
            await self._send_subscribe(self._connection, key)

    async def unwatch(self, key: str) -> None:
        """Deja de seguir `key`. Idempotente, y no falla si la conexion esta caida."""
        self._wanted.discard(key)
        subscription = self._key_to_sub.pop(key, None)
        if subscription is None:
            return
        self._sub_to_key.pop(subscription, None)
        if self._connection is None:
            return
        with contextlib.suppress(Exception):
            await self._connection.send(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": self._next_request_id(),
                        "method": "logsUnsubscribe",
                        "params": [subscription],
                    }
                )
            )

    # --- Interno ----------------------------------------------------------------------

    def _next_request_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def _send_subscribe(self, connection: LogConnection, key: str) -> None:
        request_id = self._next_request_id()
        self._request_to_key[request_id] = key
        await connection.send(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "logsSubscribe",
                    "params": [{"mentions": [key]}, {"commitment": self._commitment}],
                }
            )
        )

    def _forget_subscriptions(self) -> None:
        """Tras una caida los ids ya no valen. Lo que se quiere seguir (`_wanted`) se conserva."""
        self._request_to_key.clear()
        self._sub_to_key.clear()
        self._key_to_sub.clear()

    def _handle_ack(self, message: dict[str, Any]) -> None:
        raw_id = message.get("id")
        if not isinstance(raw_id, int):
            return
        key = self._request_to_key.pop(raw_id, None)
        if key is None:
            return
        if "error" in message:
            # Una suscripcion rechazada NO tumba las demas ni la conexion: se anota y se sigue.
            self.stats.subscribe_errors += 1
            LOGGER.warning(
                json.dumps({"event": "subscribe_rejected", "key": key, "error": message["error"]})
            )
            return
        subscription = message.get("result")
        if not isinstance(subscription, int):
            return
        self.stats.subscribe_acks += 1
        self._sub_to_key[subscription] = key
        self._key_to_sub[key] = subscription

    def _to_notification(self, message: dict[str, Any]) -> KeyedNotification | None:
        params = message.get("params")
        if not isinstance(params, dict):
            return None
        subscription = params.get("subscription")
        if not isinstance(subscription, int):
            return None
        key = self._sub_to_key.get(subscription)
        if key is None:
            # Llega tras un `unwatch`: el proveedor aun tenia mensajes en vuelo.
            self.stats.unmapped += 1
            return None
        result = params.get("result")
        if not isinstance(result, dict):
            return None
        value = result.get("value")
        if not isinstance(value, dict):
            return None
        logs = value.get("logs")
        if not isinstance(logs, list):
            return None
        context = result.get("context")
        slot = context.get("slot", 0) if isinstance(context, dict) else 0
        return KeyedNotification(
            key=key,
            logs=[str(line) for line in logs],
            signature=str(value.get("signature", "")),
            slot=int(slot) if isinstance(slot, int) else 0,
        )

    async def _drain(self, connection: LogConnection) -> AsyncIterator[KeyedNotification]:
        while True:
            raw = await asyncio.wait_for(connection.recv(), timeout=self._silence_timeout)
            self.stats.messages += 1
            try:
                message = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if not isinstance(message, dict):
                continue
            if "id" in message:
                self._handle_ack(message)
                continue
            notification = self._to_notification(message)
            if notification is not None:
                self.stats.notifications += 1
                yield notification

    def _next_backoff(self, current: float) -> float:
        capped = min(current * self._multiplier, self._max_backoff)
        return self._rng.uniform(0.0, capped)

    async def __aiter__(self) -> AsyncIterator[KeyedNotification]:
        backoff = self._initial_backoff
        attempts = 0
        while True:
            connection: LogConnection | None = None
            try:
                connection = await self._connect()
                self._connection = connection
                self._forget_subscriptions()
                # Se resuscribe TODO el conjunto vigente: tras una caida el proveedor no
                # recuerda nada, y las altas hechas mientras estaba caida solo existen aqui.
                for key in sorted(self._wanted):
                    await self._send_subscribe(connection, key)
                self.stats.connects += 1
                backoff = self._initial_backoff
                attempts = 0
                async for notification in self._drain(connection):
                    yield notification
            except asyncio.CancelledError:
                raise
            except (TimeoutError, OSError, ValueError, ConnectionError):
                LOGGER.debug("conexion multiplexada perdida", exc_info=True)
            except Exception:
                LOGGER.debug("fallo de transporte en la conexion multiplexada", exc_info=True)
            finally:
                self._connection = None
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
