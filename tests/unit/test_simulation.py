"""Simulador y backtesting (SPEC.md 17, 18).

Los tres tests obligatorios de la fase estan aqui, marcados con su seccion:

- El PnL neto es realista, no ingenuo (§ PnL realista).
- El replay es reproducible bit a bit (§ Reproducibilidad).
- El backtest no puede ver datos posteriores a la decision (§ Anti-leakage).
"""

from __future__ import annotations

import json
import random
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from mit_pumpfun.curve import CurveState, sol_out_for_tokens, tokens_out_for_sol
from mit_shared.types import LAMPORTS_PER_SOL
from mit_simulation import (
    STRESSED_LATENCY,
    CandidateCriteria,
    Decision,
    DecisionContext,
    EventDrivenSimulator,
    ExecutionConfig,
    FillStatus,
    LatencyModel,
    MarketEvent,
    SimulationMode,
    SplitError,
    compute_metrics,
    equity_curve_csv,
    evaluate_candidate,
    result_json,
    simulate_buy,
    simulate_sell,
    survives_without_outliers,
    trades_csv,
    walk_forward_splits,
)

START = datetime(2026, 8, 3, 12, 0, 0, tzinfo=UTC)
BASE_CURVE = CurveState(
    virtual_sol_reserves=30_000_000_000,
    virtual_token_reserves=1_073_000_000_000_000,
    real_token_reserves=793_100_000_000_000,
    token_total_supply=1_000_000_000_000_000,
)


def _events(
    count: int = 40, drift_bps_per_step: int = 60, *, blocked_from: int | None = None
) -> list[MarketEvent]:
    """Serie de eventos con precio que sube de forma constante.

    Deliberadamente ALCISTA: si incluso con el precio subiendo el PnL neto sale peor que el
    ingenuo, es que los costes se estan modelando de verdad.
    """
    events: list[MarketEvent] = []
    reserves = BASE_CURVE.virtual_sol_reserves
    for i in range(count):
        reserves = int(reserves * (10_000 + drift_bps_per_step) / 10_000)
        events.append(
            MarketEvent(
                at=START + timedelta(seconds=30 * i),
                mint="SimMint111111111111111111111111111111111111",
                curve=CurveState(
                    virtual_sol_reserves=reserves,
                    virtual_token_reserves=BASE_CURVE.virtual_token_reserves,
                    real_token_reserves=BASE_CURVE.real_token_reserves,
                    token_total_supply=BASE_CURVE.token_total_supply,
                ),
                exit_blocked=blocked_from is not None and i >= blocked_from,
            )
        )
    return events


def _buy_then_hold(hold_steps: int = 10) -> Callable[[DecisionContext], Decision]:
    """Estrategia trivial: comprar en el primer evento, vender N pasos despues."""
    state: dict[str, datetime | None] = {"bought_at": None}

    def strategy(context: DecisionContext) -> Decision:
        if context.open_position is None and state["bought_at"] is None:
            state["bought_at"] = context.now
            return Decision("buy", lamports=LAMPORTS_PER_SOL // 10, reason="entrada trivial")
        if context.open_position is not None:
            elapsed = context.now - context.open_position.opened_at
            if elapsed >= timedelta(seconds=30 * hold_steps):
                return Decision("sell", reason="salida por tiempo")
        return Decision("hold")

    return strategy


# =============================================================================================
# PnL realista: el test obligatorio numero 1
# =============================================================================================


def test_naive_pnl_overstates_the_real_result() -> None:
    """`precio_final - precio_inicial` miente, y aqui se mide cuanto.

    Con el precio SUBIENDO durante toda la simulacion, el resultado neto tiene que ser
    peor que el ingenuo. La diferencia es latencia, slippage, fees, MEV y fallos.
    """
    events = _events()
    size = LAMPORTS_PER_SOL // 10
    simulator = EventDrivenSimulator(execution=ExecutionConfig(max_quote_age_ms=1_000_000.0))

    # PnL ingenuo: comprar y vender al precio de la curva, sin coste ni latencia alguna.
    naive_tokens = tokens_out_for_sol(events[0].curve, size)
    naive_pnl = sol_out_for_tokens(events[10].curve, naive_tokens) - size

    # Se promedia sobre muchas semillas A PROPOSITO. La deriva de precio durante la latencia
    # es simetrica, asi que una semilla concreta puede salir favorecida: comprobarlo con una
    # sola seria un test que pasa por suerte. Lo que no puede pasar es que el NETO ESPERADO
    # supere al ingenuo, porque fees, MEV y fallos solo restan.
    results = simulator.monte_carlo(events, _buy_then_hold, seeds=list(range(120)))
    closed = [t for r in results for t in r.trades]
    assert closed, "ninguna corrida cerro operacion"

    average_net = sum(t.net_pnl_lamports for t in closed) / len(closed)
    assert average_net < naive_pnl, (
        f"el neto medio ({average_net:.0f}) no deberia superar al ingenuo ({naive_pnl})"
    )
    assert all(t.fees_lamports > 0 for t in closed), "toda operacion paga fees"
    assert all(t.net_pnl_lamports < t.gross_pnl_lamports for t in closed)


def test_failed_transactions_cost_money_without_a_fill() -> None:
    """La asimetria que ignora un simulador ingenuo: fallar cuesta dinero."""
    # max_quote_age_ms generoso a proposito: se aisla el fallo en cadena, que es lo que
    # este test mide. Con el valor por defecto caducaria antes la cotizacion.
    config = ExecutionConfig(failure_probability=1.0, max_quote_age_ms=1_000_000.0)
    rng = random.Random(1)
    outcome = simulate_buy(
        BASE_CURVE, LAMPORTS_PER_SOL // 10, LatencyModel().sample(rng), config, rng
    )
    assert outcome.status == FillStatus.TX_FAILED
    assert outcome.tokens_received == 0
    assert outcome.total_cost_lamports > 0


def test_an_unsellable_position_is_worth_zero_not_its_market_price() -> None:
    """Lo que casi ningun simulador modela y mas dinero cuesta."""
    rng = random.Random(2)
    outcome = simulate_sell(
        BASE_CURVE,
        1_000_000,
        LatencyModel().sample(rng),
        ExecutionConfig(),
        rng,
        exit_blocked=True,
    )
    assert outcome.status == FillStatus.CANNOT_EXIT
    assert outcome.sol_spent == 0

    # Y a nivel de simulacion completa: la operacion se cierra con ingresos cero.
    result = EventDrivenSimulator().run(
        _events(blocked_from=5), _buy_then_hold(hold_steps=8), seed=99
    )
    stuck = [t for t in result.trades if t.exit_status == FillStatus.CANNOT_EXIT]
    assert stuck, "deberia haber quedado una posicion atrapada"
    assert stuck[0].proceeds_lamports == 0
    assert stuck[0].net_pnl_lamports < 0
    assert result.stuck_positions >= 1


def test_expired_quotes_and_impact_rejections_are_modelled() -> None:
    """Una cotizacion vieja no se ejecuta: se descarta y se recotiza."""
    rng = random.Random(3)
    outcome = simulate_buy(
        BASE_CURVE,
        LAMPORTS_PER_SOL // 10,
        STRESSED_LATENCY.sample(rng),
        ExecutionConfig(max_quote_age_ms=100.0),
        rng,
    )
    assert outcome.status == FillStatus.QUOTE_EXPIRED

    huge = simulate_buy(
        BASE_CURVE,
        50 * LAMPORTS_PER_SOL,
        LatencyModel().sample(rng),
        ExecutionConfig(
            max_price_impact_bps=50,
            failure_probability=0.0,
            mev_probability=0.0,
            max_quote_age_ms=1_000_000.0,
        ),
        rng,
    )
    assert huge.status == FillStatus.IMPACT_REJECTED


def test_worse_latency_produces_worse_results() -> None:
    """Criterio de SPEC.md 18: la estrategia debe sobrevivir a latencias peores."""
    events = _events()
    normal = EventDrivenSimulator(latency=LatencyModel()).run(events, _buy_then_hold(), seed=7)
    stressed = EventDrivenSimulator(latency=STRESSED_LATENCY).run(events, _buy_then_hold(), seed=7)
    assert stressed.fill_rate <= normal.fill_rate


# =============================================================================================
# Reproducibilidad: el test obligatorio numero 2
# =============================================================================================


def test_replay_is_reproducible_bit_for_bit() -> None:
    """Misma semilla y mismos eventos -> mismo resultado, byte a byte.

    Sin esto un backtest no es evidencia: no se puede repetir ni auditar.
    """
    events = _events()
    simulator = EventDrivenSimulator()

    first = simulator.run(events, _buy_then_hold(), seed=20260803)
    second = simulator.run(events, _buy_then_hold(), seed=20260803)

    assert result_json(first, compute_metrics(first)) == result_json(
        second, compute_metrics(second)
    )
    assert equity_curve_csv(first) == equity_curve_csv(second)
    assert trades_csv(first) == trades_csv(second)


def test_a_different_seed_gives_a_different_path() -> None:
    """Si dos semillas dieran lo mismo, el azar no se estaria aplicando."""
    events = _events()
    # Cotizacion sin caducar: si ambas corridas se quedan sin operar, sus curvas serian
    # identicas por no haber pasado nada, no por falta de azar.
    simulator = EventDrivenSimulator(execution=ExecutionConfig(max_quote_age_ms=1_000_000.0))
    a = simulator.run(events, _buy_then_hold(), seed=1)
    b = simulator.run(events, _buy_then_hold(), seed=2)
    assert equity_curve_csv(a) != equity_curve_csv(b)


def test_monte_carlo_reports_a_distribution_not_an_average() -> None:
    """SPEC.md 17.C: si la mediana gana pero el percentil 10 arruina, no es viable."""
    events = _events()
    runs = EventDrivenSimulator(
        execution=ExecutionConfig(max_quote_age_ms=1_000_000.0)
    ).monte_carlo(events, _buy_then_hold, seeds=list(range(30)))
    assert len(runs) == 30
    # No se exige que TODAS operen: la compra puede fallar en cadena (~8%) y la estrategia
    # solo lo intenta una vez, asi que unas pocas corridas sin operacion son correctas. El
    # umbral sigue delatando el bug de estado compartido entre corridas, que dejaria
    # operando a UNA sola de treinta.
    with_trades = sum(1 for r in runs if r.trades)
    assert with_trades >= 0.6 * len(runs), (
        f"solo {with_trades}/{len(runs)} corridas operaron: la estrategia se esta "
        f"reutilizando entre corridas"
    )
    finals = sorted(r.final_equity_lamports for r in runs)
    assert finals[0] != finals[-1], "todas las corridas dan lo mismo: no hay variabilidad"
    assert all(r.mode == SimulationMode.MONTE_CARLO for r in runs)


# =============================================================================================
# Anti-leakage: el test obligatorio numero 3
# =============================================================================================


def test_the_strategy_never_sees_the_future() -> None:
    """El motor filtra antes de llamar: mirar al futuro es imposible, no solo desaconsejado."""
    events = _events(count=30)
    seen: list[tuple[datetime, datetime]] = []

    def spy(context: DecisionContext) -> Decision:
        for observed in context.history:
            assert observed.at <= context.now, (
                f"LEAKAGE: la estrategia vio {observed.at} decidiendo en {context.now}"
            )
        seen.append((context.now, max(o.at for o in context.history)))
        return Decision("hold")

    EventDrivenSimulator().run(events, spy, seed=5)

    assert len(seen) == len(events)
    assert all(latest <= now for now, latest in seen)


def test_history_grows_monotonically_and_never_skips_ahead() -> None:
    lengths: list[int] = []

    def spy(context: DecisionContext) -> Decision:
        lengths.append(len(context.history))
        return Decision("hold")

    events = _events(count=25)
    EventDrivenSimulator().run(events, spy, seed=6)
    assert lengths == list(range(1, len(events) + 1))


def test_walk_forward_splits_are_disjoint_and_purged() -> None:
    """Sin purga, las etiquetas de train se solapan con validation."""
    splits = walk_forward_splits(
        START,
        START + timedelta(days=30),
        train=timedelta(days=5),
        validation=timedelta(days=2),
        test=timedelta(days=2),
        step=timedelta(days=3),
        purge=timedelta(hours=6),
    )
    assert splits
    for split in splits:
        assert split.train_end < split.validation_start
        assert split.validation_end < split.test_start
        assert split.validation_start - split.train_end == timedelta(hours=6)
        assert split.test_start - split.validation_end == timedelta(hours=6)


def test_splits_advance_through_time() -> None:
    """Un unico split puede haber caido en un periodo favorable: walk-forward lo evita."""
    splits = walk_forward_splits(
        START,
        START + timedelta(days=40),
        train=timedelta(days=5),
        validation=timedelta(days=2),
        test=timedelta(days=2),
        step=timedelta(days=4),
        purge=timedelta(hours=1),
    )
    assert len(splits) >= 3
    starts = [s.train_start for s in splits]
    assert starts == sorted(starts)
    assert len(set(starts)) == len(starts)


def test_an_impossible_split_is_rejected() -> None:
    with pytest.raises(SplitError, match="no cabe"):
        walk_forward_splits(
            START,
            START + timedelta(days=2),
            train=timedelta(days=5),
            validation=timedelta(days=2),
            test=timedelta(days=2),
            step=timedelta(days=1),
            purge=timedelta(hours=1),
        )


def test_split_selection_respects_boundaries() -> None:
    splits = walk_forward_splits(
        START,
        START + timedelta(days=30),
        train=timedelta(days=5),
        validation=timedelta(days=2),
        test=timedelta(days=2),
        step=timedelta(days=10),
        purge=timedelta(hours=6),
    )
    items = [(START + timedelta(hours=h), h) for h in range(0, 600)]
    train = splits[0].select(items, "train")
    test = splits[0].select(items, "test")
    assert train and test
    assert set(train).isdisjoint(set(test))


# =============================================================================================
# Metricas y criterios de candidatura
# =============================================================================================


def test_every_metric_of_spec_18_is_reported() -> None:
    result = EventDrivenSimulator().run(_events(), _buy_then_hold(), seed=11)
    metrics = compute_metrics(result).as_dict()
    for name in (
        "net_return",
        "win_rate",
        "profit_factor",
        "expectancy",
        "sharpe",
        "sortino",
        "calmar",
        "max_drawdown",
        "value_at_risk_95",
        "expected_shortfall_95",
        "max_consecutive_losses",
        "recovery_factor",
        "fill_rate",
        "failed_transaction_rate",
        "slippage_avg_bps",
        "slippage_p95_bps",
        "slippage_p99_bps",
        "cost_drag",
    ):
        assert name in metrics, f"falta la metrica {name} de SPEC.md 18"


def test_a_strategy_with_few_trades_is_never_a_candidate() -> None:
    """Con pocas operaciones el resultado es ruido, gane lo que gane."""
    result = EventDrivenSimulator().run(_events(), _buy_then_hold(), seed=12)
    verdict = evaluate_candidate(compute_metrics(result), result.trades)
    assert not verdict.is_candidate
    assert any("ruido" in reason for reason in verdict.reasons)


def test_the_verdict_lists_every_reason_not_just_the_first() -> None:
    result = EventDrivenSimulator().run(_events(), _buy_then_hold(), seed=13)
    verdict = evaluate_candidate(
        compute_metrics(result),
        result.trades,
        CandidateCriteria(min_trades=1000, min_profit_factor=99.0),
        out_of_sample_net_return=-0.2,
        stressed_net_return=-0.1,
    )
    assert not verdict.is_candidate
    assert len(verdict.reasons) >= 3


def test_outlier_dependence_is_detected() -> None:
    """Una estrategia sostenida por dos aciertos enormes no tiene ventaja, tiene suerte."""
    from mit_simulation.engine import SimulatedTrade

    def trade(pnl: int) -> SimulatedTrade:
        return SimulatedTrade(
            mint="m",
            opened_at=START,
            closed_at=START + timedelta(minutes=5),
            tokens=1,
            cost_lamports=1_000_000,
            proceeds_lamports=1_000_000 + pnl,
            fees_lamports=0,
            entry_status=FillStatus.FILLED,
            exit_status=FillStatus.FILLED,
            entry_slippage_bps=0,
            exit_slippage_bps=0,
            entry_latency_ms=0.0,
            exit_latency_ms=0.0,
        )

    lucky = [trade(10_000_000)] + [trade(-50_000) for _ in range(99)]
    assert not survives_without_outliers(lucky)

    solid = [trade(20_000) for _ in range(100)]
    assert survives_without_outliers(solid)


# =============================================================================================
# Exportacion
# =============================================================================================


def test_exports_carry_the_provenance_needed_to_repeat_them() -> None:
    result = EventDrivenSimulator().run(_events(), _buy_then_hold(), seed=4242)
    payload = json.loads(result_json(result, compute_metrics(result)))
    assert payload["reproducibility"]["seed"] == 4242
    assert payload["reproducibility"]["mode"] == "HISTORICAL_REPLAY"
    assert "equity_curve" in payload


def test_equity_curve_csv_has_a_row_per_event() -> None:
    events = _events(count=20)
    result = EventDrivenSimulator().run(events, _buy_then_hold(), seed=8)
    lines = equity_curve_csv(result).strip().splitlines()
    assert lines[0] == "at,equity_lamports,equity_sol,drawdown_fraction"
    assert len(lines) == len(events) + 1


def test_drawdown_is_never_negative() -> None:
    result = EventDrivenSimulator().run(_events(), _buy_then_hold(), seed=9)
    assert all(0.0 <= p.drawdown_fraction <= 1.0 for p in result.equity_curve)
