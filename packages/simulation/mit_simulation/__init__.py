"""Simulador event-driven y backtesting (SPEC.md 17, 18).

Explicitamente NO calcula `precio_final - precio_inicial`. Modela la cadena de latencia de
seis etapas, el slippage sobre la curva real, los fees, las transacciones fallidas, las
cotizaciones caducadas, el MEV, los fills parciales, la competencia de otros bots y la
imposibilidad de salir.

Dos invariantes del paquete:

- **Reproducibilidad**: todo el azar pasa por una semilla explicita.
- **Anti-leakage**: la estrategia recibe solo eventos anteriores o iguales al instante de
  decision, filtrados por el motor antes de llamarla.
"""

from __future__ import annotations

from mit_simulation.engine import (
    Decision,
    DecisionContext,
    EquityPoint,
    EventDrivenSimulator,
    MarketEvent,
    Position,
    SimulatedTrade,
    SimulationMode,
    SimulationResult,
    StressScenario,
)
from mit_simulation.export import equity_curve_csv, result_json, trades_csv
from mit_simulation.fills import (
    ExecutionConfig,
    FillOutcome,
    FillStatus,
    simulate_buy,
    simulate_sell,
)
from mit_simulation.latency import STRESSED_LATENCY, LatencyBreakdown, LatencyModel
from mit_simulation.metrics import (
    BacktestMetrics,
    CandidateCriteria,
    CandidateVerdict,
    compute_metrics,
    evaluate_candidate,
    survives_without_outliers,
)
from mit_simulation.walkforward import SplitError, TimeSplit, walk_forward_splits

__all__ = [
    "STRESSED_LATENCY",
    "BacktestMetrics",
    "CandidateCriteria",
    "CandidateVerdict",
    "Decision",
    "DecisionContext",
    "EquityPoint",
    "EventDrivenSimulator",
    "ExecutionConfig",
    "FillOutcome",
    "FillStatus",
    "LatencyBreakdown",
    "LatencyModel",
    "MarketEvent",
    "Position",
    "SimulatedTrade",
    "SimulationMode",
    "SimulationResult",
    "SplitError",
    "StressScenario",
    "TimeSplit",
    "compute_metrics",
    "equity_curve_csv",
    "evaluate_candidate",
    "result_json",
    "simulate_buy",
    "simulate_sell",
    "survives_without_outliers",
    "trades_csv",
    "walk_forward_splits",
]
