"""RiskEngine determinista (SPEC.md 14).

Es el UNICO componente que decide cuanto dinero se compromete. No recibe entrada de ningun
modelo generativo, y `SizingInputs` esta cerrado para que no pueda recibirla nunca.
"""

from __future__ import annotations

from mit_risk.engine import RiskEngine
from mit_risk.types import (
    AccountState,
    KillSwitch,
    MarketSnapshot,
    RiskDecision,
    RiskLimits,
    SizingInputs,
    SizingResult,
    StopType,
)

__all__ = [
    "AccountState",
    "KillSwitch",
    "MarketSnapshot",
    "RiskDecision",
    "RiskEngine",
    "RiskLimits",
    "SizingInputs",
    "SizingResult",
    "StopType",
]
