"""Contrato de firma del signer aislado (SPEC.md 16).

**Aqui no hay ninguna clave.** Este modulo decide SI una transaccion puede firmarse; el
material criptografico vive en el proceso del signer y no cruza esta frontera. Por eso no
existe en todo el archivo ni una palabra que nombre una clave.

Las validaciones las hace el SIGNER, no el llamante. Recibe lo gastado hoy y lo comprueba el
mismo. Si el backend se compromete, el limite diario sigue en pie — que es justamente el
punto de tener un proceso aparte.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class SignerRejection(StrEnum):
    """Motivos por los que el signer se niega. Se reportan TODOS, no solo el primero."""

    PROGRAM_NOT_ALLOWED = "program_not_allowed"
    DESTINATION_NOT_ALLOWED = "destination_not_allowed"
    ORDER_LIMIT = "order_limit"
    DAILY_LIMIT = "daily_limit"
    AUTHORITY_CHANGE = "authority_change"
    UNKNOWN_INSTRUCTION = "unknown_instruction"
    ALREADY_SIGNED = "already_signed"
    ACCOUNT_CLOSURE = "account_closure"


@dataclass(frozen=True, slots=True)
class SignerPolicy:
    """Limites del signer. Se configuran fuera y el signer los aplica sin discutir."""

    program_allowlist: frozenset[str]
    destination_allowlist: frozenset[str]
    max_order_lamports: int
    max_daily_lamports: int
    owner_wallet: str


@dataclass(frozen=True, slots=True)
class TransactionPlan:
    """Lo que el ExecutionEngine pide firmar, ya decodificado.

    Se pasa DECODIFICADO a proposito: el signer no debe tener que entender bytes crudos para
    aplicar su politica. Si algo no se pudo decodificar, llega en `unknown_instructions` y se
    rechaza — lo que no se entiende no se firma.
    """

    program_ids: tuple[str, ...]
    destinations: tuple[str, ...]
    lamports_out: int
    recent_blockhash: str
    idempotency_key: str
    creates_authority: bool = False
    closes_accounts: bool = False
    unknown_instructions: int = 0


@dataclass(frozen=True, slots=True)
class SignerDecision:
    """Veredicto con todas sus razones. Se registra SIEMPRE, apruebe o no."""

    approved: bool
    idempotency_key: str
    lamports_out: int
    rejections: tuple[SignerRejection, ...] = field(default_factory=tuple)
    detail: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, object]:
        return {
            "approved": self.approved,
            "idempotency_key": self.idempotency_key,
            "lamports_out": self.lamports_out,
            "rejections": [r.value for r in self.rejections],
            "detail": list(self.detail),
        }


def evaluate_signing_request(
    plan: TransactionPlan,
    policy: SignerPolicy,
    *,
    spent_today_lamports: int,
    already_signed: tuple[str, ...] = (),
) -> SignerDecision:
    """Aplica las nueve validaciones de SECURITY.md 2.

    Devuelve TODOS los motivos de rechazo: arreglar uno no debe destapar el siguiente y
    hacer creer al operador que va progresando.
    """
    rejections: list[SignerRejection] = []
    detail: list[str] = []

    if plan.idempotency_key in already_signed:
        rejections.append(SignerRejection.ALREADY_SIGNED)
        detail.append(f"la intencion {plan.idempotency_key} ya se firmo")

    forbidden_programs = [p for p in plan.program_ids if p not in policy.program_allowlist]
    if forbidden_programs:
        rejections.append(SignerRejection.PROGRAM_NOT_ALLOWED)
        detail.append(f"programas no permitidos: {', '.join(forbidden_programs)}")

    forbidden_destinations = [
        d
        for d in plan.destinations
        if d not in policy.destination_allowlist and d != policy.owner_wallet
    ]
    if forbidden_destinations:
        rejections.append(SignerRejection.DESTINATION_NOT_ALLOWED)
        detail.append(f"destinos no permitidos: {', '.join(forbidden_destinations)}")

    if plan.lamports_out > policy.max_order_lamports:
        rejections.append(SignerRejection.ORDER_LIMIT)
        detail.append(
            f"{plan.lamports_out / 1e9:.4f} SOL supera el maximo por orden "
            f"{policy.max_order_lamports / 1e9:.4f} SOL"
        )

    if spent_today_lamports + plan.lamports_out > policy.max_daily_lamports:
        rejections.append(SignerRejection.DAILY_LIMIT)
        detail.append(
            f"gastado hoy {spent_today_lamports / 1e9:.4f} + {plan.lamports_out / 1e9:.4f} "
            f"supera el limite diario {policy.max_daily_lamports / 1e9:.4f} SOL"
        )

    if plan.creates_authority:
        rejections.append(SignerRejection.AUTHORITY_CHANGE)
        detail.append("la transaccion crea o delega una autoridad")

    if plan.closes_accounts:
        rejections.append(SignerRejection.ACCOUNT_CLOSURE)
        detail.append("la transaccion cierra cuentas")

    if plan.unknown_instructions > 0:
        rejections.append(SignerRejection.UNKNOWN_INSTRUCTION)
        detail.append(f"{plan.unknown_instructions} instruccion(es) no decodificables")

    return SignerDecision(
        approved=not rejections,
        idempotency_key=plan.idempotency_key,
        lamports_out=plan.lamports_out,
        rejections=tuple(rejections),
        detail=tuple(detail),
    )
