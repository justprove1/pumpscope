"""Resiliencia de la ingesta: cortes, silencio y reconexion (SPEC.md 25).

La conexion se inyecta, asi que se puede cortar a voluntad y de forma determinista, sin red
ni esperas reales. Un test de resiliencia que dependa de que internet falle no es un test.
"""

from __future__ import annotations

import asyncio
import json
import random
from typing import Any

import pytest
from mit_pumpfun.detector import NewTokenDetector
from mit_solana.logs_stream import ResilientLogStream, StreamStats, SubscriptionRejectedError

PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
ACK = json.dumps({"jsonrpc": "2.0", "result": 1, "id": 1})


def _notification(signature: str) -> str:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "method": "logsNotification",
            "params": {
                "result": {
                    "context": {"slot": 100},
                    "value": {"signature": signature, "err": None, "logs": ["x"]},
                }
            },
        }
    )


class FakeConnection:
    """Conexion que entrega una lista de mensajes y luego hace lo que se le diga."""

    def __init__(self, messages: list[str], *, then: str = "drop") -> None:
        self._messages = list(messages)
        self._then = then
        self.closed = False
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> str:
        if self._messages:
            return self._messages.pop(0)
        if self._then == "drop":
            msg = "conexion cerrada por el proveedor"
            raise ConnectionError(msg)
        if self._then == "silence":
            # Nunca responde: dispara el timeout de silencio.
            await asyncio.sleep(3600)
        msg = "conexion cerrada"
        raise ConnectionError(msg)

    async def close(self) -> None:
        self.closed = True


def _stream(connections: list[FakeConnection], **kwargs: Any) -> ResilientLogStream:
    queue = list(connections)

    async def connect() -> FakeConnection:
        if not queue:
            msg = "sin conexiones disponibles"
            raise ConnectionError(msg)
        return queue.pop(0)

    defaults: dict[str, Any] = {
        "initial_backoff": 0.001,
        "max_backoff": 0.002,
        "silence_timeout": 0.05,
        "rng": random.Random(0),
    }
    defaults.update(kwargs)
    return ResilientLogStream(PROGRAM, connect, **defaults)


async def _collect(stream: ResilientLogStream, count: int, limit_seconds: float = 5.0) -> list[Any]:
    received: list[Any] = []

    async def run() -> None:
        async for notification in stream:
            received.append(notification)
            if len(received) >= count:
                return

    await asyncio.wait_for(run(), timeout=limit_seconds)
    return received


# --- Suscripcion --------------------------------------------------------------------------


async def test_sends_a_well_formed_subscription() -> None:
    connection = FakeConnection([ACK, _notification("a")])
    stream = _stream([connection])
    await _collect(stream, 1)

    payload = json.loads(connection.sent[0])
    assert payload["method"] == "logsSubscribe"
    assert payload["params"][0]["mentions"] == [PROGRAM]
    assert payload["params"][1]["commitment"] == "confirmed"


async def test_rejected_subscription_is_not_retried_forever() -> None:
    """Una credencial invalida no mejora reintentando: solo quema cuota."""
    error = json.dumps({"jsonrpc": "2.0", "error": {"code": -32601, "message": "no"}, "id": 1})
    stream = _stream([FakeConnection([error])])

    with pytest.raises(SubscriptionRejectedError, match="rechazado"):
        await _collect(stream, 1)
    assert stream.stats.subscribe_failures == 1


# --- Reconexion ---------------------------------------------------------------------------


async def test_reconnects_after_the_connection_drops() -> None:
    """Una caida es invisible para el consumidor: el iterador no termina."""
    first = FakeConnection([ACK, _notification("a")])
    second = FakeConnection([ACK, _notification("b"), _notification("c")])
    stream = _stream([first, second])

    received = await _collect(stream, 3)

    signatures = [n["params"]["result"]["value"]["signature"] for n in received]
    assert signatures == ["a", "b", "c"]
    assert stream.stats.connects == 2
    assert stream.stats.reconnects >= 1
    assert first.closed, "la conexion muerta debe cerrarse, no quedar colgando"


async def test_survives_several_consecutive_drops() -> None:
    connections = [FakeConnection([ACK, _notification(f"sig-{i}")]) for i in range(5)]
    stream = _stream(connections)

    received = await _collect(stream, 5)

    assert len(received) == 5
    assert stream.stats.connects == 5


async def test_silent_connection_is_treated_as_dead() -> None:
    """Un socket abierto que no envia nada es peor que uno cerrado: parece sano.

    Sin heartbeat el proceso se queda vivo y ciego, que es el fallo que no se detecta.
    """
    silent = FakeConnection([ACK], then="silence")
    healthy = FakeConnection([ACK, _notification("tras-el-silencio")])
    stream = _stream([silent, healthy], silence_timeout=0.05)

    received = await _collect(stream, 1)

    assert received[0]["params"]["result"]["value"]["signature"] == "tras-el-silencio"
    assert stream.stats.silence_timeouts >= 1
    assert silent.closed


async def test_gives_up_after_max_reconnects() -> None:
    """Modo acotado: sin el, un test podria girar para siempre."""
    stream = _stream([], max_reconnects=2)
    received = [n async for n in stream]
    assert received == []
    assert stream.stats.reconnects == 3


# --- Backoff ------------------------------------------------------------------------------


def test_backoff_grows_and_is_capped() -> None:
    stream = _stream([], initial_backoff=1.0, max_backoff=8.0, rng=random.Random(1))
    delays = []
    current = 1.0
    for _ in range(8):
        delays.append(stream._next_backoff(current))
        current = min(current * 2, 8.0)

    # Jitter completo: cada espera esta entre 0 y el techo vigente, nunca por encima.
    assert all(0.0 <= d <= 8.0 for d in delays)
    assert len(set(delays)) > 1, "sin jitter, N clientes reintentarian a la vez"


# --- Extremo a extremo: reconexion + deduplicacion ------------------------------------------


async def test_reconnection_with_replay_loses_nothing_and_duplicates_nothing() -> None:
    """El escenario real: al reconectar, el proveedor reenvia parte de la ventana.

    Es el requisito exacto de la Fase 1: recuperacion sin duplicados ni perdida.
    """
    first = FakeConnection([ACK, _notification("s1"), _notification("s2")])
    # Tras reconectar llegan s2 (repetido) y s3 (nuevo).
    second = FakeConnection([ACK, _notification("s2"), _notification("s3")])
    stream = _stream([first, second])

    detector = NewTokenDetector()
    seen: list[str] = []

    async def run() -> None:
        async for notification in stream:
            signature = notification["params"]["result"]["value"]["signature"]
            # El detector descarta por logs; aqui se comprueba solo la deduplicacion.
            if detector._dedup.add(signature):
                seen.append(signature)
            if len(seen) >= 3:
                return

    await asyncio.wait_for(run(), timeout=5.0)

    assert seen == ["s1", "s2", "s3"], "ni se pierde s3 ni se duplica s2"
    assert stream.stats.connects == 2


def test_stream_stats_start_at_zero() -> None:
    assert StreamStats() == StreamStats(0, 0, 0, 0, 0)
