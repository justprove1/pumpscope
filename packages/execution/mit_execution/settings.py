"""Configuracion de ejecucion y activacion de LIVE (SPEC.md 15, 30).

**LIVE no es un interruptor: son quince condiciones.** `ENABLE_LIVE_TRADING=true` es UNA de
ellas. Si bastara, un despiste en un `.env` moveria dinero real, y ese es exactamente el
fallo contra el que esta escrito todo este archivo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

# SPEC.md 30: minimo de operaciones simuladas antes de tocar dinero real.
MIN_SIMULATED_TRADES = 1000
MIN_PROFIT_FACTOR = 1.3
MAX_DRAWDOWN = 0.10


class ExecutionMode(StrEnum):
    DRY_RUN = "DRY_RUN"
    PAPER = "PAPER"
    LIVE = "LIVE"


@dataclass(frozen=True, slots=True)
class ExecutionSettings:
    """Limites de ejecucion de SPEC.md 15. Arranca en DRY_RUN SIEMPRE."""

    mode: ExecutionMode = ExecutionMode.DRY_RUN
    max_quote_age_ms: int = 1500
    max_price_impact_bps: int = 300
    max_slippage_bps: int = 250
    max_priority_fee_lamports: int = 200_000
    max_retries: int = 2
    transaction_timeout_ms: int = 20_000
    min_expected_output_required: bool = True

    @property
    def live_enabled(self) -> bool:
        return self.mode is ExecutionMode.LIVE


@dataclass(frozen=True, slots=True)
class LiveActivationChecklist:
    """Las quince condiciones de SPEC.md 30. Todas obligatorias."""

    env_enabled: bool = False
    simulated_trades: int = 0
    has_out_of_sample: bool = False
    costs_included: bool = False
    profit_factor: float = 0.0
    max_drawdown: float = 1.0
    survives_without_outliers: bool = False
    stress_tests_passed: bool = False
    reconciliation_verified: bool = False
    kill_switches_verified: bool = False
    wallet_is_dedicated: bool = False
    wallet_capital_limited: bool = False
    signer_mode: str = "disabled"
    ui_confirmed: bool = False
    pin_verified: bool = False
    operator: str = ""


@dataclass(frozen=True, slots=True)
class ActivationVerdict:
    """Permitido o no, con la lista EXACTA de lo que falta."""

    allowed: bool
    missing: tuple[str, ...] = field(default_factory=tuple)
    checked_at: datetime | None = None

    def as_dict(self) -> dict[str, object]:
        return {"allowed": self.allowed, "missing": list(self.missing)}


def can_enable_live(
    checklist: LiveActivationChecklist, now: datetime | None = None
) -> ActivationVerdict:
    """Comprueba las quince condiciones. Falta UNA y no se activa.

    Devuelve lo que falta y no solo un `False`: el operador tiene que saber que le queda, no
    solo que no puede.
    """
    missing: list[str] = []

    if not checklist.env_enabled:
        missing.append("ENABLE_LIVE_TRADING no esta en true")
    if checklist.simulated_trades < MIN_SIMULATED_TRADES:
        missing.append(
            f"solo {checklist.simulated_trades} operaciones simuladas, se exigen "
            f"{MIN_SIMULATED_TRADES}"
        )
    if not checklist.has_out_of_sample:
        missing.append("no hay muestra fuera de entrenamiento")
    if not checklist.costs_included:
        missing.append("los costes reales no estan incluidos en los resultados")
    if checklist.profit_factor < MIN_PROFIT_FACTOR:
        missing.append(
            f"profit factor {checklist.profit_factor:.2f} por debajo de {MIN_PROFIT_FACTOR}"
        )
    if checklist.max_drawdown > MAX_DRAWDOWN:
        missing.append(f"drawdown {checklist.max_drawdown:.1%} supera el limite {MAX_DRAWDOWN:.1%}")
    if not checklist.survives_without_outliers:
        missing.append("el resultado depende de unos pocos outliers")
    if not checklist.stress_tests_passed:
        missing.append("los stress tests no estan superados")
    if not checklist.reconciliation_verified:
        missing.append("la reconciliacion on-chain no esta verificada")
    if not checklist.kill_switches_verified:
        missing.append("los kill switches no estan verificados")
    if not checklist.wallet_is_dedicated:
        missing.append("la wallet no es dedicada y desechable")
    if not checklist.wallet_capital_limited:
        missing.append("el capital de la wallet no esta limitado")
    if checklist.signer_mode == "disabled":
        missing.append("el signer esta deshabilitado")
    if not checklist.ui_confirmed:
        missing.append("falta la confirmacion explicita en la interfaz")
    if not checklist.pin_verified:
        missing.append("falta el PIN o segundo factor")
    if not checklist.operator:
        missing.append("falta identificar a la persona que activa")

    return ActivationVerdict(allowed=not missing, missing=tuple(missing), checked_at=now)


def quote_is_fresh(requested_at: datetime, now: datetime, settings: ExecutionSettings) -> bool:
    """Una cotizacion vieja no se ejecuta: se descarta y se recotiza."""
    age_ms = (now - requested_at).total_seconds() * 1000
    return 0 <= age_ms <= settings.max_quote_age_ms
