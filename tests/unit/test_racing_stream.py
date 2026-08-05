"""Varios motores de ingesta en paralelo, gana el primero (SPEC.md 25).

**Por que existe.** Un solo feed publico tiene colas impredecibles: se midio un retraso de 11 s
en el token que mas rapido crecio de la sesion, justo porque su propio exito congestiono el
endpoint. Con N motores corriendo a la vez, el retraso que cuenta es el del MEJOR de ellos en
ese instante, no el de uno concreto que tuvo mala suerte.

La deduplicacion es obligatoria: el mismo evento llega N veces, una por motor. Sin ella se
contaria cada operacion varias veces y todas las metricas quedarian infladas.

Las conexiones se inyectan, asi que la carrera se prueba de forma determinista, sin red.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

import pytest
from mit_solana.logs_stream import ResilientLogStream
from mit_solana.racing_stream import RacingLogStream

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


class ScriptedConnection:
    """Entrega mensajes con un retraso configurable antes de cada uno."""

    def __init__(self, messages: list[str], *, delay: float = 0.0) -> None:
        self._messages = list(messages)
        self._delay = delay

    async def send(self, message: str) -> None:
        return None

    async def recv(self) -> str:
        if self._messages:
            message = self._messages.pop(0)
            # El ACK no se retrasa: lo que se simula es la latencia de los eventos.
            if message != ACK and self._delay:
                await asyncio.sleep(self._delay)
            return message
        await asyncio.sleep(3600)  # se queda en silencio
        msg = "inalcanzable"
        raise ConnectionError(msg)

    async def close(self) -> None:
        return None


def _stream(messages: list[str], *, delay: float = 0.0) -> ResilientLogStream:
    async def connect() -> ScriptedConnection:
        return ScriptedConnection(messages, delay=delay)

    return ResilientLogStream(
        PROGRAM, connect, silence_timeout=0.5, initial_backoff=0.001, max_backoff=0.01
    )


async def _take(racing: RacingLogStream, count: int, seconds: float = 3.0) -> list[Any]:
    """Recoge hasta `count` eventos, rindiendose al agotarse `timeout`.

    El timeout no es un fallo: varios tests comprueban justamente que NO llegan mas eventos.
    """
    collected: list[Any] = []

    async def consume() -> None:
        async for notification in racing:
            collected.append(notification)
            if len(collected) >= count:
                return

    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(consume(), timeout=seconds)
    return collected


@pytest.mark.asyncio
async def test_the_same_event_is_delivered_only_once() -> None:
    """Dos motores traen lo mismo: el consumidor lo ve UNA vez, no dos."""
    racing = RacingLogStream(
        [
            _stream([ACK, _notification("sig-a")]),
            _stream([ACK, _notification("sig-a")]),
        ]
    )
    received = await _take(racing, 2, seconds=1.0)
    await racing.close()

    signatures = [n["params"]["result"]["value"]["signature"] for n in received]
    assert signatures == ["sig-a"], "el evento duplicado tenia que filtrarse"


@pytest.mark.asyncio
async def test_the_fastest_engine_sets_the_pace() -> None:
    """Con un motor lento y uno rapido, el evento llega al ritmo del RAPIDO."""
    racing = RacingLogStream(
        [
            _stream([ACK, _notification("sig-a")], delay=0.6),
            _stream([ACK, _notification("sig-a")], delay=0.0),
        ]
    )
    started = asyncio.get_running_loop().time()
    received = await _take(racing, 1, seconds=2.0)
    elapsed = asyncio.get_running_loop().time() - started
    await racing.close()

    assert len(received) == 1
    assert elapsed < 0.5, f"se esperaba el ritmo del motor rapido, tardo {elapsed:.2f}s"


@pytest.mark.asyncio
async def test_events_unique_to_one_engine_are_not_lost() -> None:
    """Cada motor puede ver cosas que el otro no: la union es lo que importa."""
    racing = RacingLogStream(
        [
            _stream([ACK, _notification("solo-a")]),
            _stream([ACK, _notification("solo-b")]),
        ]
    )
    received = await _take(racing, 2, seconds=1.5)
    await racing.close()

    signatures = {n["params"]["result"]["value"]["signature"] for n in received}
    assert signatures == {"solo-a", "solo-b"}


@pytest.mark.asyncio
async def test_a_dead_engine_does_not_stop_the_others() -> None:
    """Si un motor no entrega nada, el resto sigue sirviendo eventos."""
    racing = RacingLogStream(
        [
            _stream([ACK]),  # solo hace ACK y se calla
            _stream([ACK, _notification("vivo")]),
        ]
    )
    received = await _take(racing, 1, seconds=1.5)
    await racing.close()

    assert len(received) == 1
    assert received[0]["params"]["result"]["value"]["signature"] == "vivo"


@pytest.mark.asyncio
async def test_the_dedup_memory_is_bounded() -> None:
    """Un proceso 24/7 no puede acumular firmas sin limite."""
    racing = RacingLogStream([_stream([ACK])], dedup_capacity=10)
    for i in range(50):
        racing.mark_seen(f"sig-{i}")
    await racing.close()
    assert racing.tracked <= 10


@pytest.mark.asyncio
async def test_at_least_one_engine_is_required() -> None:
    with pytest.raises(ValueError, match="al menos un motor"):
        RacingLogStream([])
