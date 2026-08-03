"""Modelo de ejecucion: que pasa REALMENTE cuando intentas comprar (SPEC.md 17).

Un fill no es automatico ni completo ni al precio que viste. Aqui se modela todo lo que se
interpone entre la decision y el resultado:

    cotizacion caducada · deriva de precio durante la latencia · competencia de otros bots
    MEV adverso · price impact sobre la curva real · liquidez insuficiente -> fill parcial
    transaccion fallida (se paga el fee, no hay fill) · imposibilidad de salir

La deriva de precio durante la latencia NO se escala con sqrt(t). En un memecoin los retornos
no son independientes: hay rafagas cortas y violentas seguidas de reversion. El exponente de
escalado real esta entre 0,32 y 0,57 (medido en el proyecto pumpscope previo), asi que se usa
un exponente configurable con 0,45 por defecto en vez de asumir 0,5.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from mit_pumpfun.curve import CurveState, price_impact_bps, tokens_out_for_sol
from mit_shared.types import LAMPORTS_PER_SOL

from mit_simulation.latency import LatencyBreakdown


class FillStatus(StrEnum):
    FILLED = "filled"
    PARTIAL = "partial"
    QUOTE_EXPIRED = "quote_expired"
    IMPACT_REJECTED = "impact_rejected"
    TX_FAILED = "tx_failed"
    NO_LIQUIDITY = "no_liquidity"
    CANNOT_EXIT = "cannot_exit"


# Estados en los que NO se obtiene token pero SI se paga el fee de red. Es la asimetria que
# mas dano hace y la que un simulador ingenuo ignora: fallar cuesta dinero.
COSTS_FEE_WITHOUT_FILL = frozenset({FillStatus.TX_FAILED, FillStatus.QUOTE_EXPIRED})


@dataclass(frozen=True, slots=True)
class ExecutionConfig:
    """Limites y costes. Los limites replican los de RISK_POLICY.md."""

    max_quote_age_ms: float = 1500.0
    max_price_impact_bps: int = 300
    max_slippage_bps: int = 250

    base_fee_lamports: int = 5_000
    priority_fee_lamports: int = 200_000
    # Probabilidad de que la transaccion falle en cadena aunque todo lo demas cuadre.
    failure_probability: float = 0.08
    # Probabilidad de sufrir un sandwich u orden adversa por delante.
    mev_probability: float = 0.15
    mev_adverse_bps: int = 120

    # Volatilidad por segundo del token, en fraccion de precio.
    volatility_per_second: float = 0.012
    # Exponente de escalado temporal. NO es 0,5: los retornos de un memecoin no son
    # independientes (ver docstring del modulo).
    volatility_scaling_exponent: float = 0.45
    # Otros bots consumen liquidez antes que nosotros durante la latencia.
    competition_liquidity_fraction: float = 0.10


@dataclass(frozen=True, slots=True)
class FillOutcome:
    """Resultado de un intento de ejecucion."""

    status: FillStatus
    sol_spent: int = 0
    tokens_received: int = 0
    fee_lamports: int = 0
    priority_fee_lamports: int = 0
    slippage_bps: int = 0
    price_impact_bps: int = 0
    price_drift_bps: int = 0
    mev_bps: int = 0
    latency: LatencyBreakdown | None = None
    detail: str = ""

    @property
    def succeeded(self) -> bool:
        return self.status in (FillStatus.FILLED, FillStatus.PARTIAL)

    @property
    def total_cost_lamports(self) -> int:
        """Todo lo que sale de la wallet, haya fill o no."""
        return self.sol_spent + self.fee_lamports + self.priority_fee_lamports

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "sol_spent": self.sol_spent,
            "tokens_received": self.tokens_received,
            "fee_lamports": self.fee_lamports,
            "priority_fee_lamports": self.priority_fee_lamports,
            "slippage_bps": self.slippage_bps,
            "price_impact_bps": self.price_impact_bps,
            "price_drift_bps": self.price_drift_bps,
            "mev_bps": self.mev_bps,
            "detail": self.detail,
            "latency": self.latency.as_dict() if self.latency else None,
        }


def price_drift_bps(config: ExecutionConfig, elapsed_ms: float, rng: random.Random) -> int:
    """Cuanto se mueve el precio mientras la transaccion viaja.

    Escalado con exponente medido, no con sqrt(t). Con rafagas y reversion, asumir sqrt(t)
    SOBREESTIMA el movimiento a horizontes largos y lo subestima a los cortos.
    """
    if elapsed_ms <= 0:
        return 0
    seconds = elapsed_ms / 1000.0
    scale = config.volatility_per_second * (seconds**config.volatility_scaling_exponent)
    return int(rng.gauss(0.0, scale) * 10_000)


def _drifted_curve(curve: CurveState, drift_bps: int) -> CurveState:
    """Aplica una deriva de precio moviendo la reserva virtual de SOL.

    Se mueve la reserva y no el precio directamente para que la curva siga siendo coherente:
    el price impact posterior se calcula sobre un estado valido, no sobre un numero pegado.
    """
    factor = Decimal(10_000 + drift_bps) / Decimal(10_000)
    if factor <= 0:
        factor = Decimal("0.01")
    return CurveState(
        virtual_sol_reserves=max(1, int(Decimal(curve.virtual_sol_reserves) * factor)),
        virtual_token_reserves=curve.virtual_token_reserves,
        real_token_reserves=curve.real_token_reserves,
        token_total_supply=curve.token_total_supply,
        real_sol_reserves=curve.real_sol_reserves,
    )


def simulate_buy(
    curve: CurveState,
    lamports_in: int,
    latency: LatencyBreakdown,
    config: ExecutionConfig,
    rng: random.Random,
) -> FillOutcome:
    """Simula una compra completa, con todo lo que puede salir mal.

    El orden de las comprobaciones importa y replica el de la vida real: la cotizacion caduca
    ANTES de que la transaccion llegue a fallar, y el fee se paga igual.
    """
    fees = config.base_fee_lamports
    priority = config.priority_fee_lamports

    # 1. La cotizacion envejece mientras la transaccion viaja.
    if latency.quote_age_ms > config.max_quote_age_ms:
        return FillOutcome(
            status=FillStatus.QUOTE_EXPIRED,
            fee_lamports=fees,
            priority_fee_lamports=priority,
            latency=latency,
            detail=(
                f"cotizacion de {latency.quote_age_ms:.0f} ms, maximo "
                f"{config.max_quote_age_ms:.0f} ms"
            ),
        )

    # 2. El precio se mueve durante la latencia total.
    drift = price_drift_bps(config, latency.total_ms, rng)

    # 3. MEV: alguien se cuela delante y empeora el precio.
    mev = 0
    if rng.random() < config.mev_probability:
        mev = config.mev_adverse_bps

    effective_curve = _drifted_curve(curve, drift + mev)

    # 4. La competencia se ha comido parte de la liquidez disponible.
    available = int(
        effective_curve.real_token_reserves * (1 - config.competition_liquidity_fraction)
    )
    if available <= 0:
        return FillOutcome(
            status=FillStatus.NO_LIQUIDITY,
            fee_lamports=fees,
            priority_fee_lamports=priority,
            latency=latency,
            price_drift_bps=drift,
            mev_bps=mev,
            detail="sin liquidez tras la competencia",
        )

    impact = price_impact_bps(effective_curve, lamports_in)
    if impact > config.max_price_impact_bps:
        return FillOutcome(
            status=FillStatus.IMPACT_REJECTED,
            latency=latency,
            price_impact_bps=impact,
            price_drift_bps=drift,
            mev_bps=mev,
            detail=f"impacto {impact} bps supera el maximo {config.max_price_impact_bps}",
        )

    # 5. La transaccion puede fallar en cadena. Se paga el fee igual.
    if rng.random() < config.failure_probability:
        return FillOutcome(
            status=FillStatus.TX_FAILED,
            fee_lamports=fees,
            priority_fee_lamports=priority,
            latency=latency,
            price_drift_bps=drift,
            mev_bps=mev,
            detail="transaccion fallida en cadena",
        )

    tokens = tokens_out_for_sol(effective_curve, lamports_in)
    partial = False
    spent = lamports_in
    if tokens > available:
        # Fill parcial: solo se ejecuta lo que la liquidez permite.
        partial = True
        ratio = Decimal(available) / Decimal(tokens)
        tokens = available
        spent = int(Decimal(lamports_in) * ratio)

    if tokens <= 0:
        return FillOutcome(
            status=FillStatus.NO_LIQUIDITY,
            fee_lamports=fees,
            priority_fee_lamports=priority,
            latency=latency,
            detail="la curva no devuelve tokens para ese importe",
        )

    slippage = max(0, drift + mev + impact)
    return FillOutcome(
        status=FillStatus.PARTIAL if partial else FillStatus.FILLED,
        sol_spent=spent,
        tokens_received=tokens,
        fee_lamports=fees,
        priority_fee_lamports=priority,
        slippage_bps=slippage,
        price_impact_bps=impact,
        price_drift_bps=drift,
        mev_bps=mev,
        latency=latency,
        detail="fill parcial por liquidez" if partial else "",
    )


def simulate_sell(
    curve: CurveState,
    tokens_in: int,
    latency: LatencyBreakdown,
    config: ExecutionConfig,
    rng: random.Random,
    *,
    exit_blocked: bool = False,
) -> FillOutcome:
    """Simula una venta.

    `exit_blocked` modela lo que casi ningun simulador incluye y mas dinero cuesta: que no
    haya forma de salir. Una posicion que no se puede vender NO vale su precio de mercado:
    vale cero. Tratarla de otro modo produce curvas de equity preciosas y falsas.
    """
    fees = config.base_fee_lamports
    priority = config.priority_fee_lamports

    if exit_blocked:
        return FillOutcome(
            status=FillStatus.CANNOT_EXIT,
            fee_lamports=fees,
            priority_fee_lamports=priority,
            latency=latency,
            detail="imposible salir: honeypot, liquidez retirada o sin ruta",
        )

    if latency.quote_age_ms > config.max_quote_age_ms:
        return FillOutcome(
            status=FillStatus.QUOTE_EXPIRED,
            fee_lamports=fees,
            priority_fee_lamports=priority,
            latency=latency,
            detail="cotizacion caducada al vender",
        )

    drift = price_drift_bps(config, latency.total_ms, rng)
    mev = config.mev_adverse_bps if rng.random() < config.mev_probability else 0
    # Al vender, la deriva adversa es a la baja.
    effective_curve = _drifted_curve(curve, drift - mev)

    if rng.random() < config.failure_probability:
        return FillOutcome(
            status=FillStatus.TX_FAILED,
            fee_lamports=fees,
            priority_fee_lamports=priority,
            latency=latency,
            price_drift_bps=drift,
            mev_bps=mev,
            detail="venta fallida en cadena",
        )

    from mit_pumpfun.curve import sol_out_for_tokens

    proceeds = sol_out_for_tokens(effective_curve, tokens_in)
    if proceeds <= 0:
        return FillOutcome(
            status=FillStatus.NO_LIQUIDITY,
            fee_lamports=fees,
            priority_fee_lamports=priority,
            latency=latency,
            detail="la venta no devuelve SOL: liquidez agotada",
        )

    return FillOutcome(
        status=FillStatus.FILLED,
        sol_spent=-proceeds,  # negativo: entra SOL
        tokens_received=-tokens_in,
        fee_lamports=fees,
        priority_fee_lamports=priority,
        slippage_bps=abs(min(0, drift - mev)),
        price_drift_bps=drift,
        mev_bps=mev,
        latency=latency,
    )


def lamports_to_sol(lamports: int) -> float:
    return lamports / LAMPORTS_PER_SOL
