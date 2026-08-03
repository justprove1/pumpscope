"""Metricas de backtest y criterios de candidatura (SPEC.md 18).

**Se reportan todas. Ninguna se presenta aislada.**

Un win rate del 90% con una cola izquierda que se lleva el ano es una forma cara de perder
dinero, y presentado solo parece un exito. Por eso `BacktestMetrics` no tiene un "resultado":
tiene quince numeros y un veredicto que exige que TODOS cumplan.

Todos los retornos son NETOS de costes. El bruto solo existe para poder medir cuanto se comen
las comisiones, y no aparece en ningun criterio.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

from mit_simulation.engine import SimulatedTrade, SimulationResult


def _percentile(values: Sequence[float], fraction: float) -> float:
    """Percentil por rango mas cercano, sin interpolar.

    Sin interpolar porque un valor interpolado nunca ocurrio, y para fijar umbrales
    operativos es preferible un numero que si paso.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * len(ordered)) - 1))
    return ordered[index]


@dataclass(frozen=True, slots=True)
class BacktestMetrics:
    """Las metricas de SPEC.md 18, todas juntas."""

    trades: int
    total_return: float
    net_return: float
    win_rate: float
    average_win: float
    average_loss: float
    profit_factor: float
    expectancy: float
    sharpe: float
    sortino: float
    calmar: float
    max_drawdown: float
    value_at_risk_95: float
    expected_shortfall_95: float
    max_consecutive_losses: int
    recovery_factor: float
    fill_rate: float
    failed_transaction_rate: float
    slippage_avg_bps: float
    slippage_p95_bps: float
    slippage_p99_bps: float
    latency_p95_ms: float
    stuck_positions: int
    cost_drag: float

    def as_dict(self) -> dict[str, float | int]:
        return {
            "trades": self.trades,
            "total_return": round(self.total_return, 6),
            "net_return": round(self.net_return, 6),
            "win_rate": round(self.win_rate, 4),
            "average_win": round(self.average_win, 6),
            "average_loss": round(self.average_loss, 6),
            "profit_factor": round(self.profit_factor, 4),
            "expectancy": round(self.expectancy, 6),
            "sharpe": round(self.sharpe, 4),
            "sortino": round(self.sortino, 4),
            "calmar": round(self.calmar, 4),
            "max_drawdown": round(self.max_drawdown, 4),
            "value_at_risk_95": round(self.value_at_risk_95, 6),
            "expected_shortfall_95": round(self.expected_shortfall_95, 6),
            "max_consecutive_losses": self.max_consecutive_losses,
            "recovery_factor": round(self.recovery_factor, 4),
            "fill_rate": round(self.fill_rate, 4),
            "failed_transaction_rate": round(self.failed_transaction_rate, 4),
            "slippage_avg_bps": round(self.slippage_avg_bps, 2),
            "slippage_p95_bps": round(self.slippage_p95_bps, 2),
            "slippage_p99_bps": round(self.slippage_p99_bps, 2),
            "latency_p95_ms": round(self.latency_p95_ms, 1),
            "stuck_positions": self.stuck_positions,
            "cost_drag": round(self.cost_drag, 6),
        }


def _consecutive_losses(trades: Sequence[SimulatedTrade]) -> int:
    worst = current = 0
    for trade in trades:
        if trade.net_pnl_lamports < 0:
            current += 1
            worst = max(worst, current)
        else:
            current = 0
    return worst


def compute_metrics(result: SimulationResult) -> BacktestMetrics:
    """Calcula todas las metricas de una simulacion."""
    trades = result.trades
    returns = [t.return_fraction for t in trades]
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r < 0]

    gross_profit = sum(t.net_pnl_lamports for t in trades if t.net_pnl_lamports > 0)
    gross_loss = -sum(t.net_pnl_lamports for t in trades if t.net_pnl_lamports < 0)

    initial = result.initial_capital_lamports
    net_return = (result.final_equity_lamports - initial) / initial if initial else 0.0
    max_drawdown = max((p.drawdown_fraction for p in result.equity_curve), default=0.0)

    mean_return = sum(returns) / len(returns) if returns else 0.0
    if len(returns) > 1:
        variance = sum((r - mean_return) ** 2 for r in returns) / (len(returns) - 1)
        stdev = math.sqrt(variance)
        downside = [min(0.0, r) for r in returns]
        downside_dev = math.sqrt(sum(d * d for d in downside) / len(downside))
    else:
        stdev = downside_dev = 0.0

    total_fees = sum(t.fees_lamports for t in trades)
    gross_pnl = sum(t.gross_pnl_lamports for t in trades)

    failed = sum(
        count
        for status, count in result.failed_fills.items()
        if status in {"tx_failed", "quote_expired"}
    )
    slippages = [float(t.entry_slippage_bps) for t in trades] + [
        float(t.exit_slippage_bps) for t in trades
    ]
    latencies = [t.entry_latency_ms for t in trades] + [t.exit_latency_ms for t in trades]

    return BacktestMetrics(
        trades=len(trades),
        total_return=gross_pnl / initial if initial else 0.0,
        net_return=net_return,
        win_rate=len(wins) / len(returns) if returns else 0.0,
        average_win=sum(wins) / len(wins) if wins else 0.0,
        average_loss=sum(losses) / len(losses) if losses else 0.0,
        # Sin perdidas el profit factor es infinito; se devuelve 0 para no fabricar un
        # numero espectacular a partir de una muestra insuficiente.
        profit_factor=(gross_profit / gross_loss) if gross_loss > 0 else 0.0,
        expectancy=mean_return,
        sharpe=(mean_return / stdev) if stdev > 0 else 0.0,
        sortino=(mean_return / downside_dev) if downside_dev > 0 else 0.0,
        calmar=(net_return / max_drawdown) if max_drawdown > 0 else 0.0,
        max_drawdown=max_drawdown,
        value_at_risk_95=_percentile(returns, 0.05),
        expected_shortfall_95=(
            sum(r for r in returns if r <= _percentile(returns, 0.05))
            / max(1, len([r for r in returns if r <= _percentile(returns, 0.05)]))
            if returns
            else 0.0
        ),
        max_consecutive_losses=_consecutive_losses(trades),
        recovery_factor=(net_return / max_drawdown) if max_drawdown > 0 else 0.0,
        fill_rate=result.fill_rate,
        failed_transaction_rate=(failed / result.attempts) if result.attempts else 0.0,
        slippage_avg_bps=sum(slippages) / len(slippages) if slippages else 0.0,
        slippage_p95_bps=_percentile(slippages, 0.95),
        slippage_p99_bps=_percentile(slippages, 0.99),
        latency_p95_ms=_percentile(latencies, 0.95),
        stuck_positions=result.stuck_positions,
        # Cuanto del resultado bruto se comen las comisiones. Si es cercano o mayor que 1,
        # la "ventaja" era solo la ausencia de costes.
        cost_drag=(total_fees / abs(gross_pnl)) if gross_pnl else 0.0,
    )


@dataclass(frozen=True, slots=True)
class CandidateCriteria:
    """Umbrales de SPEC.md 18. Una estrategia es candidata solo si los cumple TODOS."""

    min_trades: int = 100
    min_profit_factor: float = 1.3
    max_drawdown: float = 0.10
    require_positive_net_return: bool = True
    # Debe seguir siendo rentable eliminando el mejor 1% de las operaciones.
    outlier_fraction: float = 0.01


@dataclass(frozen=True, slots=True)
class CandidateVerdict:
    """Veredicto con TODAS las razones del rechazo, no solo la primera."""

    is_candidate: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, object]:
        return {"is_candidate": self.is_candidate, "reasons": list(self.reasons)}


def survives_without_outliers(trades: Sequence[SimulatedTrade], fraction: float = 0.01) -> bool:
    """Sigue siendo rentable tras quitar el mejor 1% de las operaciones.

    SPEC.md 18 lo exige: una estrategia que depende de dos aciertos enormes no tiene ventaja,
    tiene suerte, y la suerte no se repite en produccion.
    """
    if not trades:
        return False
    ordered = sorted(trades, key=lambda t: t.net_pnl_lamports, reverse=True)
    drop = max(1, int(len(ordered) * fraction))
    return sum(t.net_pnl_lamports for t in ordered[drop:]) > 0


def evaluate_candidate(
    metrics: BacktestMetrics,
    trades: Sequence[SimulatedTrade],
    criteria: CandidateCriteria | None = None,
    *,
    out_of_sample_net_return: float | None = None,
    stressed_net_return: float | None = None,
) -> CandidateVerdict:
    """Aplica los criterios de SPEC.md 18. Devuelve TODAS las razones de rechazo."""
    criteria = criteria or CandidateCriteria()
    reasons: list[str] = []

    if metrics.trades < criteria.min_trades:
        reasons.append(
            f"solo {metrics.trades} operaciones, se exigen {criteria.min_trades}: "
            f"el resultado es ruido"
        )
    if criteria.require_positive_net_return and metrics.net_return <= 0:
        reasons.append(f"retorno NETO no positivo ({metrics.net_return:.4f})")
    if metrics.profit_factor < criteria.min_profit_factor:
        reasons.append(
            f"profit factor {metrics.profit_factor:.2f} por debajo de {criteria.min_profit_factor}"
        )
    if metrics.max_drawdown > criteria.max_drawdown:
        reasons.append(
            f"drawdown maximo {metrics.max_drawdown:.1%} supera el limite "
            f"{criteria.max_drawdown:.1%}"
        )
    if not survives_without_outliers(trades, criteria.outlier_fraction):
        reasons.append("deja de ser rentable al quitar el mejor 1% de las operaciones")
    if out_of_sample_net_return is not None and out_of_sample_net_return <= 0:
        reasons.append(f"fuera de muestra no es rentable ({out_of_sample_net_return:.4f})")
    if stressed_net_return is not None and stressed_net_return <= 0:
        reasons.append(f"no sobrevive a peor latencia y slippage ({stressed_net_return:.4f})")

    return CandidateVerdict(is_candidate=not reasons, reasons=tuple(reasons))
