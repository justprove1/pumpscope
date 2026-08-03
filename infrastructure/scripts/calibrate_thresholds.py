"""Barrido de calibracion de umbrales sobre series de precio REALES.

Los umbrales del sistema —stops, vetos, pesos— los puse a mano. Este script existe para
sustituir esa opinion por medicion.

**Advertencia que no se puede quitar hasta que deje de ser cierta:** calibrar contra datos
sinteticos generados por uno mismo es circular. Este script solo acepta el corpus REAL de
`tests/fixtures/pumpfun_price_series.json`, reconstruido de las reservas virtuales que trae
cada TradeEvent de la cadena.

Y con pocos tokens, el resultado es DIRECCIONAL, no concluyente. El script lo dice en su
salida con el tamano de muestra, para que nadie confunda "medido" con "demostrado".

Uso:
    python infrastructure/scripts/calibrate_thresholds.py
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mit_pumpfun.curve import CurveState
from mit_shared.types import LAMPORTS_PER_SOL
from mit_simulation import (
    Decision,
    DecisionContext,
    EventDrivenSimulator,
    ExecutionConfig,
    MarketEvent,
    compute_metrics,
)

CORPUS = Path(__file__).resolve().parents[2] / "tests/fixtures/pumpfun_price_series.json"
# Muestra minima para que un resultado se considere algo mas que una anecdota.
MIN_TRADES_FOR_CONFIDENCE = 100


def load_events() -> dict[str, list[MarketEvent]]:
    """Convierte el corpus real en eventos de mercado para el simulador."""
    payload: dict[str, Any] = json.loads(CORPUS.read_text(encoding="utf-8"))
    result: dict[str, list[MarketEvent]] = {}
    for token in payload["tokens"]:
        events: list[MarketEvent] = []
        for entry in token["events"]:
            if entry["vsol"] <= 0 or entry["vtok"] <= 0:
                continue
            events.append(
                MarketEvent(
                    at=datetime.fromtimestamp(entry["t"], tz=UTC),
                    mint=token["mint"],
                    curve=CurveState(
                        virtual_sol_reserves=entry["vsol"],
                        virtual_token_reserves=entry["vtok"],
                        # Reservas reales no observables desde el evento: se usa un valor
                        # amplio para que la liquidez no sea el factor limitante del barrido.
                        real_token_reserves=entry["vtok"] // 2,
                        token_total_supply=1_000_000_000_000_000,
                    ),
                )
            )
        if len(events) >= 3:
            result[token["mint"]] = events
    return result


def strategy_factory(stop_loss: float, take_profit: float):  # noqa: ANN201
    """Estrategia trivial parametrizada por los dos umbrales que se barren."""

    def make():  # noqa: ANN202
        state = {"entry_price": 0.0}

        def strategy(context: DecisionContext) -> Decision:
            price = (
                context.event.curve.virtual_sol_reserves
                / context.event.curve.virtual_token_reserves
            )
            if context.open_position is None:
                if state["entry_price"]:
                    return Decision("hold")
                state["entry_price"] = price
                return Decision("buy", lamports=LAMPORTS_PER_SOL // 20)
            entry = state["entry_price"] or price
            change = (price - entry) / entry
            if change <= -stop_loss or change >= take_profit:
                return Decision("sell", reason=f"cambio {change:+.1%}")
            return Decision("hold")

        return strategy

    return make


def sweep() -> None:
    corpus = load_events()
    total_events = sum(len(v) for v in corpus.values())
    print(f"corpus real: {len(corpus)} tokens, {total_events} eventos\n")

    simulator = EventDrivenSimulator(execution=ExecutionConfig(max_quote_age_ms=1_000_000.0))
    print(f"{'stop':>6} {'take':>6} {'ops':>5} {'neto':>10} {'PF':>6} {'MDD':>7} {'win':>6}")
    print("-" * 50)

    best: tuple[float, tuple[float, float]] | None = None
    for stop_loss in (0.10, 0.20, 0.30, 0.50):
        for take_profit in (0.20, 0.50, 1.00, 2.00):
            trades = []
            net = 0.0
            for events in corpus.values():
                result = simulator.run(
                    events, strategy_factory(stop_loss, take_profit)(), seed=20260803
                )
                trades.extend(result.trades)
                net += (
                    result.final_equity_lamports - result.initial_capital_lamports
                ) / LAMPORTS_PER_SOL
            if not trades:
                continue
            from mit_simulation.engine import SimulationResult

            merged = SimulationResult(
                mode=simulator.run(
                    next(iter(corpus.values())), strategy_factory(stop_loss, take_profit)(), seed=1
                ).mode,
                seed=20260803,
                initial_capital_lamports=1_000_000_000,
                trades=tuple(trades),
                equity_curve=(),
                attempts=len(trades),
            )
            metrics = compute_metrics(merged)
            print(
                f"{stop_loss:>6.0%} {take_profit:>6.0%} {len(trades):>5} {net:>+10.6f} "
                f"{metrics.profit_factor:>6.2f} {metrics.max_drawdown:>7.1%} "
                f"{metrics.win_rate:>6.0%}"
            )
            if best is None or net > best[0]:
                best = (net, (stop_loss, take_profit))

    print("-" * 50)
    if best:
        print(f"mejor combinacion del barrido: stop {best[1][0]:.0%} / take {best[1][1]:.0%}")
    print(
        f"\nADVERTENCIA: {total_events} eventos de {len(corpus)} tokens. "
        f"SPEC.md 18 exige al menos {MIN_TRADES_FOR_CONFIDENCE} operaciones para que un "
        f"resultado no sea ruido.\nEsto es DIRECCIONAL, no concluyente: no se puede fijar "
        f"ningun umbral de produccion con esta muestra."
    )


if __name__ == "__main__":
    sweep()
