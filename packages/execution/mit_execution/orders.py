"""Idempotencia de ordenes (SPEC.md 15).

**El problema que resuelve este modulo cuesta dinero de verdad.** Ante un timeout NO se sabe
si la transaccion entro en un bloque. Reintentar a ciegas es doble gasto; no reintentar nunca
es perder operaciones legitimas. La respuesta es una clave por DECISION, no por intento.

Regla: un timeout NO libera la clave. Solo un fallo CONFIRMADO permite reintentar, y con un
tope, porque perseguir el precio indefinidamente es como se convierte una mala entrada en una
peor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class OrderStatus(StrEnum):
    RESERVED = "reserved"
    SUBMITTED = "submitted"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


# Estados en los que la clave queda BLOQUEADA para siempre. `TIMEOUT` esta aqui a proposito:
# es el caso en que no se sabe que paso, y ante la duda no se vuelve a gastar.
TERMINAL = frozenset({OrderStatus.CONFIRMED, OrderStatus.TIMEOUT, OrderStatus.CANCELLED})


@dataclass(frozen=True, slots=True)
class OrderIntent:
    """Una DECISION de operar. Su clave identifica la decision, no el intento."""

    idempotency_key: str
    mint: str
    side: str
    lamports: int
    created_at: datetime
    min_expected_output: int = 0
    require_min_output: bool = False

    def __post_init__(self) -> None:
        if self.require_min_output and self.min_expected_output <= 0:
            msg = (
                "min_expected_output obligatorio: sin salida minima garantizada un sandwich "
                "se lleva la operacion entera"
            )
            raise ValueError(msg)


@dataclass
class OrderLedger:
    """Registro de intenciones. Impide que una decision produzca dos ordenes."""

    max_retries: int = 2
    _status: dict[str, OrderStatus] = field(default_factory=dict)
    _attempts: dict[str, int] = field(default_factory=dict)

    def count(self) -> int:
        return len(self._status)

    def status(self, key: str) -> OrderStatus | None:
        return self._status.get(key)

    def reserve(self, intent: OrderIntent) -> str | None:
        """Reserva la clave. Devuelve `None` si esta decision ya no puede volver a enviarse.

        Comprobar y reservar es UNA sola operacion a proposito: en dos pasos queda una
        ventana por la que el mismo intent pasa dos veces.
        """
        key = intent.idempotency_key
        current = self._status.get(key)

        if current is None:
            self._status[key] = OrderStatus.RESERVED
            self._attempts[key] = 1
            return key
        if current in TERMINAL:
            return None
        if current is OrderStatus.FAILED and self._attempts.get(key, 0) < self.max_retries:
            self._status[key] = OrderStatus.RESERVED
            self._attempts[key] = self._attempts.get(key, 0) + 1
            return key
        return None

    def mark(self, key: str, status: OrderStatus) -> None:
        if key in self._status:
            self._status[key] = status

    def attempts(self, key: str) -> int:
        return self._attempts.get(key, 0)
