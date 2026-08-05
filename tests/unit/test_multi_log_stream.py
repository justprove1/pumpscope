"""Suscripciones multiplexadas: lo que tiene que sobrevivir a un corte de conexion.

La razon de ser del modulo es que el RPC publico devuelve 429 al abrir una conexion por mint.
Si tras una reconexion se perdieran las suscripciones, el seguimiento de los tokens graduados
se apagaria en silencio: el proceso seguiria vivo y no llegaria ni un dato. Eso es lo que
protegen estos tests.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import random
from typing import Any

import pytest
from mit_solana.multi_log_stream import MultiLogStream


class FakeConnection:
    """Conexion controlada: se le encolan mensajes y se le ordena morir cuando convenga."""

    def __init__(self, script: list[str] | None = None) -> None:
        self.sent: list[dict[str, Any]] = []
        self._inbox: asyncio.Queue[str | BaseException] = asyncio.Queue()
        self.closed = False
        for message in script or []:
            self._inbox.put_nowait(message)

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    async def recv(self) -> str:
        item = await self._inbox.get()
        if isinstance(item, BaseException):
            raise item
        return item

    async def close(self) -> None:
        self.closed = True

    def push(self, message: str) -> None:
        self._inbox.put_nowait(message)

    def kill(self, error: BaseException | None = None) -> None:
        self._inbox.put_nowait(error or ConnectionError("caida simulada"))

    # --- ayudas para construir mensajes del protocolo -------------------------------

    def ack_all(self, base: int = 100) -> None:
        """Confirma todas las suscripciones pendientes con ids correlativos."""
        for index, request in enumerate(
            [s for s in self.sent if s.get("method") == "logsSubscribe"]
        ):
            self.push(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": base + index}))

    def notify(self, subscription: int, logs: list[str], signature: str = "sig") -> None:
        self.push(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": "logsNotification",
                    "params": {
                        "subscription": subscription,
                        "result": {
                            "context": {"slot": 42},
                            "value": {"logs": logs, "signature": signature},
                        },
                    },
                }
            )
        )


def _subscribed_keys(connection: FakeConnection) -> list[str]:
    return [
        s["params"][0]["mentions"][0]
        for s in connection.sent
        if s.get("method") == "logsSubscribe"
    ]


async def _collect(stream: MultiLogStream, count: int, limite: float = 2.0) -> list[Any]:
    out: list[Any] = []

    async def run() -> None:
        async for item in stream:
            out.append(item)
            if len(out) >= count:
                return

    await asyncio.wait_for(run(), timeout=limite)
    return out


@pytest.mark.asyncio
async def test_varias_suscripciones_viajan_por_una_sola_conexion() -> None:
    """El motivo del modulo: una conexion, N mints. Abrir una por mint da 429."""
    connection = FakeConnection()
    conexiones = 0

    async def connect() -> FakeConnection:
        nonlocal conexiones
        conexiones += 1
        return connection

    stream = MultiLogStream(connect, max_reconnects=0)
    await stream.watch("mintA")
    await stream.watch("mintB")
    await stream.watch("mintC")

    task = asyncio.create_task(_collect(stream, 1, limite=1.0))
    await asyncio.sleep(0.05)
    connection.ack_all()
    await asyncio.sleep(0.05)
    connection.notify(100, ["Program data: x"])
    result = await task

    assert conexiones == 1
    assert _subscribed_keys(connection) == ["mintA", "mintB", "mintC"]
    assert result[0].key == "mintA"


@pytest.mark.asyncio
async def test_cada_notificacion_se_atribuye_a_su_mint() -> None:
    """Sin este mapeo las operaciones de PumpSwap no se pueden asignar a ningun token."""
    connection = FakeConnection()

    async def connect() -> FakeConnection:
        return connection

    stream = MultiLogStream(connect, max_reconnects=0)
    await stream.watch("mintA")
    await stream.watch("mintB")

    task = asyncio.create_task(_collect(stream, 2, limite=1.0))
    await asyncio.sleep(0.05)
    connection.ack_all()  # mintA -> 100, mintB -> 101
    await asyncio.sleep(0.05)
    connection.notify(101, ["de B"])
    connection.notify(100, ["de A"])
    got = await task

    assert [(n.key, n.logs[0]) for n in got] == [("mintB", "de B"), ("mintA", "de A")]


@pytest.mark.asyncio
async def test_tras_una_caida_se_resuscribe_todo_el_conjunto() -> None:
    """Si esto falla, el seguimiento muere en silencio: proceso vivo y cero datos."""
    conexiones: list[FakeConnection] = []

    async def connect() -> FakeConnection:
        connection = FakeConnection()
        conexiones.append(connection)
        return connection

    stream = MultiLogStream(
        connect, initial_backoff=0.001, max_backoff=0.001, rng=random.Random(0)
    )
    await stream.watch("mintA")
    await stream.watch("mintB")

    task = asyncio.create_task(_collect(stream, 1, limite=2.0))
    await asyncio.sleep(0.05)
    conexiones[0].kill()
    await asyncio.sleep(0.15)
    assert len(conexiones) >= 2, "no reconecto"
    conexiones[1].ack_all()
    await asyncio.sleep(0.05)
    conexiones[1].notify(100, ["tras reconectar"])
    got = await task

    assert _subscribed_keys(conexiones[1]) == ["mintA", "mintB"]
    assert got[0].logs == ["tras reconectar"]


@pytest.mark.asyncio
async def test_un_alta_con_la_conexion_caida_se_envia_al_reconectar() -> None:
    """Un token gradua mientras el socket esta roto: no puede perderse."""
    conexiones: list[FakeConnection] = []

    async def connect() -> FakeConnection:
        connection = FakeConnection()
        conexiones.append(connection)
        return connection

    stream = MultiLogStream(
        connect, initial_backoff=0.001, max_backoff=0.001, rng=random.Random(0)
    )
    task = asyncio.create_task(_collect(stream, 1, limite=2.0))
    await asyncio.sleep(0.05)
    conexiones[0].kill()
    await stream.watch("recien_graduado")  # llega con la conexion ya muerta
    await asyncio.sleep(0.15)

    assert "recien_graduado" in _subscribed_keys(conexiones[-1])
    conexiones[-1].ack_all()
    await asyncio.sleep(0.05)
    conexiones[-1].notify(100, ["ok"])
    got = await task
    assert got[0].key == "recien_graduado"


@pytest.mark.asyncio
async def test_una_suscripcion_rechazada_no_tumba_a_las_demas() -> None:
    connection = FakeConnection()

    async def connect() -> FakeConnection:
        return connection

    stream = MultiLogStream(connect, max_reconnects=0)
    await stream.watch("malo")
    await stream.watch("bueno")

    task = asyncio.create_task(_collect(stream, 1, limite=1.0))
    await asyncio.sleep(0.05)
    # Por id de peticion, no por posicion: el orden de resuscripcion es alfabetico.
    peticion = {
        s["params"][0]["mentions"][0]: s["id"]
        for s in connection.sent
        if s.get("method") == "logsSubscribe"
    }
    connection.push(
        json.dumps({"jsonrpc": "2.0", "id": peticion["malo"], "error": {"code": -32602}})
    )
    connection.push(json.dumps({"jsonrpc": "2.0", "id": peticion["bueno"], "result": 200}))
    await asyncio.sleep(0.05)
    connection.notify(200, ["sigo vivo"])
    got = await task

    assert got[0].key == "bueno"
    assert stream.stats.subscribe_errors == 1
    assert stream.active == frozenset({"bueno"})


@pytest.mark.asyncio
async def test_unwatch_deja_de_entregar_lo_que_llegue_despues() -> None:
    """El proveedor sigue mandando lo que ya tenia en vuelo; no debe reaparecer el token."""
    connection = FakeConnection()

    async def connect() -> FakeConnection:
        return connection

    stream = MultiLogStream(connect, max_reconnects=0)
    await stream.watch("mintA")
    await stream.watch("mintB")

    recibidos: list[Any] = []

    async def run() -> None:
        async for item in stream:
            recibidos.append(item)

    task = asyncio.create_task(run())
    await asyncio.sleep(0.05)
    connection.ack_all()
    await asyncio.sleep(0.05)
    await stream.unwatch("mintA")
    connection.notify(100, ["rezagado de A"])
    connection.notify(101, ["de B"])
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert [n.key for n in recibidos] == ["mintB"]
    assert stream.stats.unmapped == 1
    assert "mintA" not in stream.watched


@pytest.mark.asyncio
async def test_watch_es_idempotente() -> None:
    connection = FakeConnection()

    async def connect() -> FakeConnection:
        return connection

    stream = MultiLogStream(connect, max_reconnects=0)
    await stream.watch("mintA")
    await stream.watch("mintA")
    task = asyncio.create_task(_collect(stream, 1, limite=0.5))
    await asyncio.sleep(0.05)
    assert _subscribed_keys(connection) == ["mintA"]
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, TimeoutError):
        await task
