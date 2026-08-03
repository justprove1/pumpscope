"""Deteccion de manipulacion (SPEC.md 8).

Doce detectores en modulos separados por familia: coordinacion, sybil, trading e integridad
del token. Cada uno es una funcion pura sobre un `TokenContext`, asi que se prueban de forma
aislada y determinista.

**Todos funcionan 100% con datos on-chain.** Ninguno necesita una API de pago
(DATA_PROVIDERS.md 4).
"""

from __future__ import annotations

from mit_strategies.manipulation.score import (
    DETECTORS,
    ManipulationReport,
    analyze,
)
from mit_strategies.manipulation.types import (
    Finding,
    Severity,
    TokenContext,
    TradeRecord,
    WalletInfo,
)

__all__ = [
    "DETECTORS",
    "Finding",
    "ManipulationReport",
    "Severity",
    "TokenContext",
    "TradeRecord",
    "WalletInfo",
    "analyze",
]
