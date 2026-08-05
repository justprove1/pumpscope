"""Varios motores de ingesta corriendo a la vez; gana el que llega primero (SPEC.md 25).

**Por que.** Un unico feed publico tiene colas impredecibles. Se midio un retraso de 11 s en el
token que mas rapido crecio de una sesion, precisamente porque su propio exito congestiono el
endpoint: 281 transacciones en 10 s. Con N motores en paralelo, la latencia que sufre el sistema
es la del MEJOR motor en ese instante, no la de uno concreto que tuvo mala suerte.

**Que aporta cada motor.** Pueden diferir en endpoint (proveedores distintos) y en nivel de
compromiso. Medido contra mainnet sobre 7.454 eventos vistos por ambos: `processed` llego antes
que `confirmed` en el 100% de los casos, con una mediana de 105 ms. Para DETECTAR (lectura, sin
dinero en juego) esa es la eleccion correcta; `processed` puede revertirse en teoria, pero no se
firma nada con ello.

**La deduplicacion no es opcional.** El mismo evento llega una vez por motor. Sin filtrar,
cada operacion se contaria N veces y todas las metricas quedarian infladas.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import OrderedDict
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any

from mit_solana.logs_stream import ResilientLogStream

# Cuantas firmas se recuerdan para descartar duplicados. Con ~360 eventos/s del programa, esto
# cubre varios minutos: de sobra para que lleguen las copias lentas de los otros motores.
DEFAULT_DEDUP_CAPACITY = 200_000
# Cota de la cola compartida. Si el consumidor se atasca es preferible descartar lo mas viejo
# que crecer sin limite: en tiempo real, un evento con minutos de retraso ya no sirve.
DEFAULT_QUEUE_SIZE = 10_000


@dataclass
class RacingStats:
    delivered: int = 0
    duplicates: int = 0
    dropped: int = 0
    #  Cuantos eventos gano cada motor, por indice. Sirve para saber si uno sobra.
    wins: dict[int, int] | None = None


class RacingLogStream:
    """Fusiona varios `ResilientLogStream` y entrega cada evento UNA sola vez.

    Cada motor corre en su propia tarea y empuja a una cola compartida. El iterador saca de la
    cola y descarta lo ya visto. Que un motor muera o se quede mudo no afecta a los demas:
    cada uno gestiona su propia reconexion.
    """

    def __init__(
        self,
        streams: Sequence[ResilientLogStream],
        *,
        dedup_capacity: int = DEFAULT_DEDUP_CAPACITY,
        queue_size: int = DEFAULT_QUEUE_SIZE,
    ) -> None:
        if not streams:
            msg = "hace falta al menos un motor"
            raise ValueError(msg)
        self._streams = list(streams)
        self._capacity = max(1, dedup_capacity)
        # OrderedDict como LRU: se descarta lo mas antiguo al llenarse.
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._queue: asyncio.Queue[tuple[int, dict[str, Any]]] = asyncio.Queue(maxsize=queue_size)
        self._tasks: list[asyncio.Task[None]] = []
        self.stats = RacingStats(wins={})

    @property
    def streams(self) -> list[ResilientLogStream]:
        """Los motores que compiten. Se exponen para poder agregar sus metricas."""
        return self._streams

    @property
    def tracked(self) -> int:
        """Cuantas firmas se estan recordando ahora mismo."""
        return len(self._seen)

    def mark_seen(self, signature: str) -> bool:
        """Registra una firma. Devuelve True si es NUEVA, False si ya se habia visto."""
        if signature in self._seen:
            return False
        self._seen[signature] = None
        while len(self._seen) > self._capacity:
            self._seen.popitem(last=False)
        return True

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()

    async def _pump(self, index: int, stream: ResilientLogStream) -> None:
        """Vuelca un motor en la cola compartida. No propaga fallos a los demas."""
        async for notification in stream:
            try:
                self._queue.put_nowait((index, notification))
            except asyncio.QueueFull:
                # Cola llena: se tira lo mas viejo, que es lo que menos vale en tiempo real.
                with contextlib.suppress(asyncio.QueueEmpty):
                    self._queue.get_nowait()
                    self.stats.dropped += 1
                with contextlib.suppress(asyncio.QueueFull):
                    self._queue.put_nowait((index, notification))

    def _start(self) -> None:
        if self._tasks:
            return
        self._tasks = [
            asyncio.create_task(self._pump(index, stream))
            for index, stream in enumerate(self._streams)
        ]

    async def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        self._start()
        while True:
            index, notification = await self._queue.get()
            value = ((notification.get("params") or {}).get("result") or {}).get("value") or {}
            signature = value.get("signature")
            # Sin firma no se puede deduplicar: se entrega, porque perder un evento es peor
            # que repetirlo, y el detector deduplica otra vez aguas abajo.
            if signature and not self.mark_seen(str(signature)):
                self.stats.duplicates += 1
                continue
            self.stats.delivered += 1
            if self.stats.wins is not None:
                self.stats.wins[index] = self.stats.wins.get(index, 0) + 1
            yield notification
