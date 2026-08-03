"""Deduplicacion acotada en memoria (SPEC.md 25).

Tras una reconexion se reprocesan eventos ya vistos: el proveedor reenvia parte de la
ventana, y sin deduplicar el mismo token entraria dos veces en la base de datos.

La clave es que sea ACOTADA. Un `set` que crece sin limite es una fuga de memoria con otro
nombre, y en un proceso pensado para correr 24/7 acaba tumbandolo. Aqui la capacidad es fija
y se expulsa por orden de llegada.
"""

from __future__ import annotations

from collections import OrderedDict


class BoundedDedup:
    """Recuerda las ultimas `capacity` claves vistas. O(1) en memoria y en tiempo.

    Expulsa por orden de insercion (FIFO), no por uso: una firma de Solana no se repite mas
    alla de la ventana de reenvio de una reconexion, asi que recordar las mas recientes es
    exactamente lo que hace falta.
    """

    __slots__ = ("_capacity", "_seen")

    def __init__(self, capacity: int = 100_000) -> None:
        if capacity <= 0:
            msg = "la capacidad debe ser positiva"
            raise ValueError(msg)
        self._capacity = capacity
        self._seen: OrderedDict[str, None] = OrderedDict()

    def __len__(self) -> int:
        return len(self._seen)

    def __contains__(self, key: str) -> bool:
        return key in self._seen

    @property
    def capacity(self) -> int:
        return self._capacity

    def add(self, key: str) -> bool:
        """Registra una clave. Devuelve `True` si es nueva, `False` si ya se habia visto.

        El valor de retorno es el que usa el llamante para decidir si procesa el evento, asi
        que comprobar y registrar tienen que ser una sola operacion: hacerlo en dos pasos
        deja una ventana donde el mismo evento pasa dos veces.
        """
        if key in self._seen:
            return False
        self._seen[key] = None
        if len(self._seen) > self._capacity:
            self._seen.popitem(last=False)
        return True

    def clear(self) -> None:
        self._seen.clear()
