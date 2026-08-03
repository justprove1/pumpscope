"""Tipos del motor de riesgo (SPEC.md 14).

`SizingInputs` es deliberadamente CERRADO: `slots=True` impide anadirle campos, y no existe
ninguno por el que pueda entrar texto, una sugerencia o la salida de un modelo. Es la
aplicacion estructural de CLAUDE.md 1: el LLM no decide importes porque no tiene por donde.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class KillSwitch(StrEnum):
    """Los doce disparadores de SPEC.md 14."""

    DAILY_LOSS = "daily_loss"
    DRAWDOWN = "drawdown"
    CONSECUTIVE_LOSSES = "consecutive_losses"
    PROVIDER_DOWN = "provider_down"
    LATENCY = "latency"
    ERROR_RATE = "error_rate"
    PRICE_DIVERGENCE = "price_divergence"
    BALANCE_ANOMALY = "balance_anomaly"
    DUPLICATE_TRANSACTIONS = "duplicate_transactions"
    UNEXPECTED_EXPOSURE = "unexpected_exposure"
    UNAUTHORIZED_SIGNATURE = "unauthorized_signature"
    UNAPPROVED_CONFIG = "unapproved_config"


class StopType(StrEnum):
    """Los nueve mecanismos de salida de SPEC.md 14."""

    HARD = "hard"
    SOFT = "soft"
    TRAILING = "trailing"
    TIME = "time"
    LIQUIDITY = "liquidity"
    NARRATIVE = "narrative"
    WHALE_EXIT = "whale_exit"
    BREAK_EVEN = "break_even"
    PARTIAL_TAKE_PROFIT = "partial_take_profit"


@dataclass(frozen=True, slots=True)
class RiskLimits:
    """Limites de RISK_POLICY.md. Cambiarlos es un evento auditado."""

    risk_per_trade_pct: float = 0.5
    max_exposure_per_token_pct: float = 3.0
    max_daily_loss_pct: float = 3.0
    max_drawdown_pct: float = 10.0
    max_consecutive_losses: int = 4
    min_sol_fee_reserve_lamports: int = 20_000_000
    max_order_lamports: int = 50_000_000
    max_total_exposure_lamports: int = 200_000_000
    max_open_positions: int = 1
    max_price_impact_bps: int = 300
    max_liquidity_fraction: float = 0.05
    # Por debajo de esto no se opera. NUNCA se redondea hacia arriba.
    min_order_lamports: int = 5_000_000
    max_latency_p95_ms: float = 5_000.0
    max_error_rate: float = 0.25
    max_price_divergence_pct: float = 10.0


@dataclass(frozen=True, slots=True)
class AccountState:
    """Estado de la cuenta. Todo medido, nada estimado."""

    balance_lamports: int
    equity_lamports: int
    peak_equity_lamports: int
    open_positions: int = 0
    exposure_lamports: int = 0
    realized_pnl_day_lamports: int = 0
    consecutive_losses: int = 0
    spent_today_lamports: int = 0


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    """Salud del sistema y del mercado en el instante de decidir."""

    provider_down: bool = False
    latency_p95_ms: float = 0.0
    error_rate: float = 0.0
    price_divergence_pct: float = 0.0
    balance_mismatch: bool = False
    duplicate_transactions: int = 0
    unexpected_exposure: bool = False
    unauthorized_signature: bool = False
    unapproved_config_change: bool = False


@dataclass(frozen=True, slots=True)
class SizingInputs:
    """Entradas del calculo de tamano.

    CERRADO a proposito (`slots=True`): no hay campo por el que colar una sugerencia de un
    modelo, y anadir uno obliga a tocar este archivo, que es donde se mira.
    """

    stop_distance_fraction: float
    liquidity_lamports: int
    volatility: float
    expected_slippage_bps: int
    confidence: float
    correlated_exposure_lamports: int = 0
    estimated_exit_cost_lamports: int = 0


@dataclass(frozen=True, slots=True)
class SizingResult:
    """Tamano y, sobre todo, QUE restriccion lo limito.

    Saber cual ata es lo que permite explicar la decision y detectar un limite mal puesto.
    """

    lamports: int
    binding_constraint: str
    constraints: dict[str, int]

    def as_dict(self) -> dict[str, object]:
        return {
            "lamports": self.lamports,
            "binding_constraint": self.binding_constraint,
            "constraints": dict(self.constraints),
        }


@dataclass(frozen=True, slots=True)
class RiskDecision:
    """Permitido o no, con TODAS las razones."""

    allowed: bool
    reasons: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {"allowed": self.allowed, "reasons": list(self.reasons)}
