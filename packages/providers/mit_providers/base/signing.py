"""Contrato del servicio de firma (SPEC.md 16, SECURITY.md 2).

Este es el limite de seguridad mas importante del sistema. Todo lo que hay aqui son bytes de
transacciones sin firmar y respuestas; **ninguna clave privada aparece en ninguna firma de
este modulo, ni entra ni sale**. El backend principal no puede acceder al material
criptografico ni aunque quiera: no esta en su proceso.

La interfaz vive en `providers` porque el ExecutionEngine consume el signer como un proveedor
mas, a traves de HTTP local. La implementacion vive en `apps/signer`, en otro contenedor.

INTERFAZ ABSTRACTA, SIN IMPLEMENTACION. El signer real es Fase 6.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class SignRequest:
    """Peticion de firma.

    `idempotency_key` impide que un timeout de red produzca dos transacciones. Es
    responsabilidad del signer, no del llamante: si el llamante fuera fiable, no harian falta
    estas defensas.
    """

    serialized_transaction: bytes
    idempotency_key: str
    mint: str
    max_sol: Decimal
    program_ids: tuple[str, ...]
    requested_by: str
    reason: str


@dataclass(frozen=True, slots=True)
class SignRejection:
    """Rechazo de una firma, con el motivo concreto.

    Siempre se registra. Un rechazo repetido es senal de que algo va mal aguas arriba, y
    puede activar el kill switch de `firma no autorizada`.
    """

    code: str
    detail: str


@dataclass(frozen=True, slots=True)
class SignResponse:
    """Resultado de una peticion de firma."""

    request_id: str
    signed: bool
    signed_transaction: bytes | None = None
    rejection: SignRejection | None = None
    daily_sol_remaining: Decimal | None = None


@dataclass(frozen=True, slots=True)
class SignerStatus:
    """Estado del signer. Lo consulta el dashboard; nunca expone la clave ni su ubicacion."""

    mode: str
    enabled: bool
    public_key: str | None = None
    daily_sol_limit: Decimal | None = None
    daily_sol_spent: Decimal | None = None
    program_allowlist: tuple[str, ...] = field(default_factory=tuple)


class SigningService(ABC):
    """Contrato del servicio de firma aislado.

    Las nueve validaciones de SECURITY.md 2 las hace el SIGNER, no el llamante. El signer
    asume que quien le habla puede estar comprometido:

        1. Origen autenticado                    6. Sin creacion ni delegacion de autoridades
        2. Programas en allowlist                7. Sin instrucciones desconocidas
        3. Importe por orden dentro del limite   8. Blockhash reciente e idempotencia
        4. Acumulado diario dentro del limite    9. Modo LIVE activo
        5. Destinos en allowlist
    """

    @abstractmethod
    async def status(self) -> SignerStatus:
        """Estado actual. Nunca devuelve material criptografico."""

    @abstractmethod
    async def sign(self, request: SignRequest) -> SignResponse:
        """Valida y firma.

        Devuelve `SignResponse(signed=False, rejection=...)` cuando rechaza. No lanza
        excepcion: un rechazo es una respuesta legitima y debe quedar registrada como tal,
        no perderse en un manejador de errores.
        """

    @abstractmethod
    async def daily_spent(self) -> Decimal:
        """SOL gastado hoy, segun el contador PROPIO del signer.

        Deliberadamente no se consulta al backend: si el backend estuviera comprometido, su
        contador seria lo primero en mentir.
        """
