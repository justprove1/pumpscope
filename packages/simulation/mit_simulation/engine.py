"""Simulador event-driven (SPEC.md 17).

**Lo que este simulador NO hace:** `pnl = precio_final - precio_inicial`. Esa formula ignora
que llegaste tarde, que te llenaron peor, que la transaccion fallo y que al salir no habia
nadie al otro lado. Produce curvas de equity preciosas y falsas.

**Anti-leakage estructural.** La estrategia recibe un `DecisionContext` que contiene SOLO
eventos con timestamp <= al instante de decision. No es una convencion que haya que respetar:
el motor filtra antes de llamar, asi que una estrategia no puede mirar al futuro ni queriendo.

**Reproducibilidad bit a bit.** Todo el azar pasa por un `random.Random` con semilla explicita.
Misma semilla y mismos eventos -> mismo resultado, siempre. Sin eso un backtest no es evidencia
de nada porque no se puede repetir.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from mit_pumpfun.curve import CurveState

from mit_simulation.fills import (
    ExecutionConfig,
    FillOutcome,
    FillStatus,
    simulate_buy,
    simulate_sell,
)
from mit_simulation.latency import LatencyModel


class SimulationMode(StrEnum):
    """Los cuatro modos de SPEC.md 17."""

    HISTORICAL_REPLAY = "HISTORICAL_REPLAY"
    PAPER_LIVE = "PAPER_LIVE"
    MONTE_CARLO = "MONTE_CARLO"
    STRESS_TEST = "STRESS_TEST"


class StressScenario(StrEnum):
    """Escenarios adversos inyectados a proposito (SPEC.md 17.D)."""

    RUG = "rug"
    WHALE_EXIT = "whale_exit"
    RPC_OUTAGE = "rpc_outage"
    LIQUIDITY_VANISHES = "liquidity_vanishes"
    CONGESTION = "congestion"
    INSTANT_CRASH_50 = "instant_crash_50"
    REPEATED_FAILURES = "repeated_failures"
    INCONSISTENT_DATA = "inconsistent_data"


@dataclass(frozen=True, slots=True)
class MarketEvent:
    """Un instante observable del mercado para un token."""

    at: datetime
    mint: str
    curve: CurveState
    exit_blocked: bool = False


@dataclass(frozen=True, slots=True)
class DecisionContext:
    """Lo que ve la estrategia. NUNCA contiene nada posterior a `now`."""

    now: datetime
    event: MarketEvent
    history: tuple[MarketEvent, ...]
    open_position: Position | None = None


@dataclass(frozen=True, slots=True)
class Decision:
    """Lo que la estrategia pide. El motor decide si se puede."""

    action: str  # "buy" | "sell" | "hold"
    lamports: int = 0
    reason: str = ""


@dataclass
class Position:
    """Posicion abierta durante la simulacion."""

    mint: str
    opened_at: datetime
    tokens: int
    cost_lamports: int
    fees_lamports: int = 0

    @property
    def average_price(self) -> float:
        return self.cost_lamports / self.tokens if self.tokens else 0.0


@dataclass(frozen=True, slots=True)
class SimulatedTrade:
    """Una operacion cerrada, con TODO su coste desglosado."""

    mint: str
    opened_at: datetime
    closed_at: datetime
    tokens: int
    cost_lamports: int
    proceeds_lamports: int
    fees_lamports: int
    entry_status: FillStatus
    exit_status: FillStatus
    entry_slippage_bps: int
    exit_slippage_bps: int
    entry_latency_ms: float
    exit_latency_ms: float

    @property
    def net_pnl_lamports(self) -> int:
        """PnL NETO. Es el unico que se reporta: el bruto no aparece en ningun informe."""
        return self.proceeds_lamports - self.cost_lamports - self.fees_lamports

    @property
    def gross_pnl_lamports(self) -> int:
        """Solo para poder MEDIR cuanto se comen los costes, nunca para presumir."""
        return self.proceeds_lamports - self.cost_lamports

    @property
    def return_fraction(self) -> float:
        return self.net_pnl_lamports / self.cost_lamports if self.cost_lamports else 0.0

    def as_dict(self) -> dict[str, object]:
        return {
            "mint": self.mint,
            "opened_at": self.opened_at.isoformat(),
            "closed_at": self.closed_at.isoformat(),
            "tokens": self.tokens,
            "cost_lamports": self.cost_lamports,
            "proceeds_lamports": self.proceeds_lamports,
            "fees_lamports": self.fees_lamports,
            "net_pnl_lamports": self.net_pnl_lamports,
            "gross_pnl_lamports": self.gross_pnl_lamports,
            "return_fraction": round(self.return_fraction, 6),
            "entry_status": self.entry_status.value,
            "exit_status": self.exit_status.value,
            "entry_slippage_bps": self.entry_slippage_bps,
            "exit_slippage_bps": self.exit_slippage_bps,
            "entry_latency_ms": round(self.entry_latency_ms, 1),
            "exit_latency_ms": round(self.exit_latency_ms, 1),
        }


@dataclass(frozen=True, slots=True)
class EquityPoint:
    at: datetime
    equity_lamports: int
    drawdown_fraction: float


@dataclass(frozen=True, slots=True)
class SimulationResult:
    """Salida completa y reproducible de una simulacion."""

    mode: SimulationMode
    seed: int
    initial_capital_lamports: int
    trades: tuple[SimulatedTrade, ...]
    equity_curve: tuple[EquityPoint, ...]
    attempts: int
    failed_fills: dict[str, int] = field(default_factory=dict)
    stuck_positions: int = 0

    @property
    def final_equity_lamports(self) -> int:
        return (
            self.equity_curve[-1].equity_lamports
            if self.equity_curve
            else (self.initial_capital_lamports)
        )

    @property
    def fill_rate(self) -> float:
        return len(self.trades) / self.attempts if self.attempts else 0.0


Strategy = Callable[[DecisionContext], Decision]


class EventDrivenSimulator:
    """Reproduce eventos en orden temporal y ejecuta una estrategia sobre ellos."""

    def __init__(
        self,
        execution: ExecutionConfig | None = None,
        latency: LatencyModel | None = None,
        initial_capital_lamports: int = 1_000_000_000,
    ) -> None:
        self._execution = execution or ExecutionConfig()
        self._latency = latency or LatencyModel()
        self._initial = initial_capital_lamports

    def run(
        self,
        events: Sequence[MarketEvent],
        strategy: Strategy,
        *,
        seed: int,
        mode: SimulationMode = SimulationMode.HISTORICAL_REPLAY,
        max_hold: timedelta = timedelta(minutes=60),
    ) -> SimulationResult:
        """Ejecuta la simulacion. Determinista dada `seed` y `events`."""
        rng = random.Random(seed)
        ordered = sorted(events, key=lambda e: e.at)

        cash = self._initial
        position: Position | None = None
        trades: list[SimulatedTrade] = []
        curve: list[EquityPoint] = []
        failures: dict[str, int] = {}
        attempts = 0
        stuck = 0
        peak = self._initial
        entry_fill: FillOutcome | None = None

        for index, event in enumerate(ordered):
            # ANTI-LEAKAGE: la estrategia solo ve hasta este evento, nunca mas alla.
            context = DecisionContext(
                now=event.at,
                event=event,
                history=tuple(ordered[: index + 1]),
                open_position=position,
            )
            decision = strategy(context)

            if decision.action == "buy" and position is None and decision.lamports > 0:
                if decision.lamports > cash:
                    decision = Decision("hold", reason="sin capital suficiente")
                else:
                    attempts += 1
                    latency = self._latency.sample(rng)
                    outcome = simulate_buy(
                        event.curve, decision.lamports, latency, self._execution, rng
                    )
                    entry_fill = outcome
                    cash -= outcome.total_cost_lamports
                    if outcome.succeeded:
                        position = Position(
                            mint=event.mint,
                            opened_at=event.at,
                            tokens=outcome.tokens_received,
                            cost_lamports=outcome.sol_spent,
                            fees_lamports=outcome.fee_lamports + outcome.priority_fee_lamports,
                        )
                    else:
                        failures[outcome.status.value] = failures.get(outcome.status.value, 0) + 1

            elif position is not None:
                timed_out = event.at - position.opened_at >= max_hold
                if decision.action == "sell" or timed_out:
                    latency = self._latency.sample(rng)
                    outcome = simulate_sell(
                        event.curve,
                        position.tokens,
                        latency,
                        self._execution,
                        rng,
                        exit_blocked=event.exit_blocked,
                    )
                    fees = outcome.fee_lamports + outcome.priority_fee_lamports
                    cash -= fees

                    if outcome.succeeded:
                        proceeds = -outcome.sol_spent
                        cash += proceeds
                        trades.append(
                            SimulatedTrade(
                                mint=position.mint,
                                opened_at=position.opened_at,
                                closed_at=event.at,
                                tokens=position.tokens,
                                cost_lamports=position.cost_lamports,
                                proceeds_lamports=proceeds,
                                fees_lamports=position.fees_lamports + fees,
                                entry_status=(
                                    entry_fill.status if entry_fill else FillStatus.FILLED
                                ),
                                exit_status=outcome.status,
                                entry_slippage_bps=(entry_fill.slippage_bps if entry_fill else 0),
                                exit_slippage_bps=outcome.slippage_bps,
                                entry_latency_ms=(
                                    entry_fill.latency.total_ms
                                    if entry_fill and entry_fill.latency
                                    else 0.0
                                ),
                                exit_latency_ms=(
                                    outcome.latency.total_ms if outcome.latency else 0.0
                                ),
                            )
                        )
                        position = None
                    elif outcome.status == FillStatus.CANNOT_EXIT:
                        # Una posicion que no se puede vender vale CERO, no su precio de
                        # mercado. Es lo que casi ningun simulador modela y lo que mas
                        # dinero cuesta.
                        stuck += 1
                        trades.append(
                            SimulatedTrade(
                                mint=position.mint,
                                opened_at=position.opened_at,
                                closed_at=event.at,
                                tokens=position.tokens,
                                cost_lamports=position.cost_lamports,
                                proceeds_lamports=0,
                                fees_lamports=position.fees_lamports + fees,
                                entry_status=(
                                    entry_fill.status if entry_fill else FillStatus.FILLED
                                ),
                                exit_status=outcome.status,
                                entry_slippage_bps=0,
                                exit_slippage_bps=0,
                                entry_latency_ms=0.0,
                                exit_latency_ms=(
                                    outcome.latency.total_ms if outcome.latency else 0.0
                                ),
                            )
                        )
                        position = None
                    else:
                        failures[outcome.status.value] = failures.get(outcome.status.value, 0) + 1

            equity = cash
            if position is not None:
                # Valoracion conservadora: lo que se sacaria vendiendo AHORA, no el precio
                # marginal. Marcar a precio marginal infla la equity de toda posicion ilíquida.
                from mit_pumpfun.curve import sol_out_for_tokens

                equity += sol_out_for_tokens(event.curve, position.tokens)

            peak = max(peak, equity)
            curve.append(
                EquityPoint(
                    at=event.at,
                    equity_lamports=equity,
                    drawdown_fraction=(peak - equity) / peak if peak > 0 else 0.0,
                )
            )

        return SimulationResult(
            mode=mode,
            seed=seed,
            initial_capital_lamports=self._initial,
            trades=tuple(trades),
            equity_curve=tuple(curve),
            attempts=attempts,
            failed_fills=failures,
            stuck_positions=stuck,
        )

    def monte_carlo(
        self,
        events: Sequence[MarketEvent],
        strategy_factory: Callable[[], Strategy],
        *,
        seeds: Sequence[int],
        max_hold: timedelta = timedelta(minutes=60),
    ) -> tuple[SimulationResult, ...]:
        """Varias corridas con semillas distintas (SPEC.md 17.C).

        Recibe una FABRICA de estrategias, no una estrategia. Es deliberado: casi toda
        estrategia real lleva estado (ya compre, cuantas van, ultimo precio visto), y
        reutilizar la misma instancia entre corridas la deja contaminada por la anterior.
        El sintoma es brutal y silencioso: la primera corrida opera y las 199 siguientes no
        hacen nada, devolviendo una distribucion de ceros que parece "muy estable".

        Se reporta la DISTRIBUCION, no la media. Si la mediana gana pero el percentil 10 es
        ruina, la estrategia no es viable por buena que sea su media.
        """
        return tuple(
            self.run(
                events,
                strategy_factory(),
                seed=seed,
                mode=SimulationMode.MONTE_CARLO,
                max_hold=max_hold,
            )
            for seed in seeds
        )
