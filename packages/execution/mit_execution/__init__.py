"""ExecutionEngine y contrato del signer (SPEC.md 15, 16, 30).

**LIVE deshabilitado por defecto y por entorno.** Este paquete construye el camino completo
hasta la firma, pero no lo abre: `ExecutionSettings` arranca en DRY_RUN y `can_enable_live`
exige las quince condiciones de SPEC.md 30.

Tres invariantes:

1. Aqui no hay ninguna clave. El material criptografico vive en el proceso del signer.
2. Una DECISION produce como mucho una orden, pase lo que pase con la red.
3. Ninguna IA firma, cambia limites ni salta validaciones (CLAUDE.md 1).
"""

from __future__ import annotations

from mit_execution.orders import TERMINAL, OrderIntent, OrderLedger, OrderStatus
from mit_execution.settings import (
    MAX_DRAWDOWN,
    MIN_PROFIT_FACTOR,
    MIN_SIMULATED_TRADES,
    ActivationVerdict,
    ExecutionMode,
    ExecutionSettings,
    LiveActivationChecklist,
    can_enable_live,
    quote_is_fresh,
)
from mit_execution.signing import (
    SignerDecision,
    SignerPolicy,
    SignerRejection,
    TransactionPlan,
    evaluate_signing_request,
)

__all__ = [
    "MAX_DRAWDOWN",
    "MIN_PROFIT_FACTOR",
    "MIN_SIMULATED_TRADES",
    "TERMINAL",
    "ActivationVerdict",
    "ExecutionMode",
    "ExecutionSettings",
    "LiveActivationChecklist",
    "OrderIntent",
    "OrderLedger",
    "OrderStatus",
    "SignerDecision",
    "SignerPolicy",
    "SignerRejection",
    "TransactionPlan",
    "can_enable_live",
    "evaluate_signing_request",
    "quote_is_fresh",
]
