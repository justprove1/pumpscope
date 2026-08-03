"""Tipos comunes de los detectores de manipulacion (SPEC.md 8).

Un detector NO devuelve un booleano ni un numero suelto. Devuelve hallazgos con **evidencia
concreta y cifras**, porque SPEC.md 8 lo exige literalmente:

    "31% del supply pertenece a wallets financiadas por la misma direccion"
    "8 de los primeros 10 compradores han coincidido en 17 lanzamientos"

Un score sin razones es imposible de auditar y de mejorar: cuando falle, no se sabra por que.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class Severity(StrEnum):
    """Peso del hallazgo. Determina cuanto suma al score."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


SEVERITY_POINTS: dict[Severity, int] = {
    Severity.INFO: 0,
    Severity.LOW: 8,
    Severity.MEDIUM: 20,
    Severity.HIGH: 35,
    Severity.CRITICAL: 60,
}


@dataclass(frozen=True, slots=True)
class Finding:
    """Un hallazgo concreto, con su evidencia numerica."""

    detector: str
    severity: Severity
    # Frase legible CON cifras. Es lo que acaba en la interfaz y en la auditoria.
    reason: str
    # Datos crudos que sustentan la frase, para poder recalcularla o discutirla.
    evidence: dict[str, float | int | str] = field(default_factory=dict)

    @property
    def points(self) -> int:
        return SEVERITY_POINTS[self.severity]


@dataclass(frozen=True, slots=True)
class TradeRecord:
    """Una operacion sobre el token."""

    signature: str
    slot: int
    block_time: datetime
    wallet: str
    side: str  # "buy" | "sell"
    sol_amount: int  # lamports
    token_amount: int


@dataclass(frozen=True, slots=True)
class WalletInfo:
    """Lo que se sabe de una wallet. Todo opcional: casi nunca se sabe todo."""

    address: str
    first_seen_at: datetime | None = None
    funded_by: str | None = None
    is_pool: bool = False
    is_program: bool = False


@dataclass(frozen=True, slots=True)
class TokenContext:
    """Todo lo que un detector puede mirar de un token.

    Se pasa completo a todos los detectores y cada uno usa lo que necesita. Asi anadir un
    detector no obliga a cambiar la firma de los demas.
    """

    mint: str
    creator: str
    created_at: datetime
    total_supply: int
    trades: tuple[TradeRecord, ...] = ()
    holders: dict[str, int] = field(default_factory=dict)
    wallets: dict[str, WalletInfo] = field(default_factory=dict)
    name: str = ""
    symbol: str = ""
    uri: str = ""
    # Mints creados antes por el mismo creador y como acabaron.
    creator_previous_tokens: int = 0
    creator_previous_dumps: int = 0

    def wallet_info(self, address: str) -> WalletInfo:
        return self.wallets.get(address, WalletInfo(address=address))

    @property
    def buys(self) -> tuple[TradeRecord, ...]:
        return tuple(t for t in self.trades if t.side == "buy")

    @property
    def sells(self) -> tuple[TradeRecord, ...]:
        return tuple(t for t in self.trades if t.side == "sell")
