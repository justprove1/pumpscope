"""Curva, concentracion y anti-leakage.

Incluye los property tests que exige la Fase 2: rangos acotados y determinismo ante la misma
entrada. No hace falta `hypothesis`: se generan las entradas con una semilla fija, que es
determinista y no anade una dependencia mas.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from mit_features.concentration import (
    concentration,
    entropy,
    exclude_known_accounts,
    gini,
    herfindahl,
    normalized_entropy,
    top_n_pct,
)
from mit_features.windows import (
    WINDOW_SECONDS,
    LeakageError,
    Observation,
    WindowedFeatures,
    count,
    last,
    mean,
    select_visible,
    total,
    velocity,
    window_bounds,
)
from mit_pumpfun.curve import (
    IMPACT_SIZES_SOL,
    CurveError,
    CurveState,
    graduation_market_cap_lamports,
    impact_curve,
    market_cap_lamports,
    progress_pct,
    sol_out_for_tokens,
    sol_to_complete,
    spot_price_lamports,
    tokens_out_for_sol,
)
from mit_shared.types import LAMPORTS_PER_SOL

# Parametros REALES, observados identicos en las 5 creaciones capturadas de mainnet.
REAL_STATE = CurveState(
    virtual_sol_reserves=30_000_000_000,
    virtual_token_reserves=1_073_000_000_000_000,
    real_token_reserves=793_100_000_000_000,
    token_total_supply=1_000_000_000_000_000,
)


# --- Curva --------------------------------------------------------------------------------


def test_graduation_threshold_is_derived_not_a_dollar_constant() -> None:
    """El umbral NO son 69.000 $: sale de la invariante de la curva, en SOL.

    Con los parametros reales de mainnet salen ~85 SOL recaudados y ~411 SOL de
    capitalizacion. Esas cifras no dependen del precio del dolar, asi que siguen siendo
    correctas cuando SOL se mueve.
    """
    assert sol_to_complete(REAL_STATE) / LAMPORTS_PER_SOL == pytest.approx(85.005, abs=0.01)
    cap = graduation_market_cap_lamports(REAL_STATE) / LAMPORTS_PER_SOL
    assert cap == pytest.approx(410.9, abs=0.5)


def test_initial_market_cap_matches_the_curve() -> None:
    assert market_cap_lamports(REAL_STATE) / LAMPORTS_PER_SOL == pytest.approx(27.96, abs=0.05)


def test_price_impact_is_monotonic_in_size() -> None:
    """Comprar mas mueve mas el precio. Si esto se rompe, la curva esta mal implementada."""
    impacts = impact_curve(REAL_STATE)
    values = [impacts[size] for size in IMPACT_SIZES_SOL]
    assert values == sorted(values)
    assert all(v >= 0 for v in values)


def test_impact_curve_covers_every_required_size() -> None:
    """SPEC.md 7 exige exactamente estos seis tamanos."""
    assert set(impact_curve(REAL_STATE)) == {"0.01", "0.05", "0.1", "0.25", "0.5", "1"}


def test_buying_more_yields_more_tokens_but_worse_price() -> None:
    small = tokens_out_for_sol(REAL_STATE, LAMPORTS_PER_SOL // 100)
    large = tokens_out_for_sol(REAL_STATE, LAMPORTS_PER_SOL)
    assert large > small
    # 100x el importe da menos de 100x los tokens: eso es el slippage.
    assert large < small * 100


def test_curve_cannot_sell_more_than_it_holds() -> None:
    """Una orden gigantesca no puede sacar mas tokens de los que quedan en la curva."""
    absurd = 10_000 * LAMPORTS_PER_SOL
    assert tokens_out_for_sol(REAL_STATE, absurd) <= REAL_STATE.real_token_reserves


def test_selling_returns_less_than_buying_cost() -> None:
    """Ida y vuelta inmediata siempre pierde: es la curva, no una comision."""
    spent = LAMPORTS_PER_SOL
    tokens = tokens_out_for_sol(REAL_STATE, spent)
    assert sol_out_for_tokens(REAL_STATE, tokens) < spent


def test_invalid_reserves_are_rejected() -> None:
    with pytest.raises(CurveError):
        CurveState(0, 1, 1, 1)
    with pytest.raises(CurveError):
        CurveState(1, 1, -5, 1)


def test_completed_curve_needs_nothing_more() -> None:
    done = CurveState(115_000_000_000, 279_900_000_000_000, 0, 1_000_000_000_000_000)
    assert done.is_complete
    assert sol_to_complete(done) == 0


def test_zero_and_negative_amounts_are_safe() -> None:
    assert tokens_out_for_sol(REAL_STATE, 0) == 0
    assert tokens_out_for_sol(REAL_STATE, -5) == 0
    assert sol_out_for_tokens(REAL_STATE, 0) == 0


def test_curve_is_deterministic() -> None:
    """Property test: misma entrada, misma salida, siempre."""
    for _ in range(50):
        assert impact_curve(REAL_STATE) == impact_curve(REAL_STATE)
        assert spot_price_lamports(REAL_STATE) == spot_price_lamports(REAL_STATE)


def test_progress_stays_within_range() -> None:
    assert Decimal(0) <= progress_pct(REAL_STATE) <= Decimal(100)


# --- Concentracion ------------------------------------------------------------------------


def _random_balances(rng: random.Random, n: int) -> list[int]:
    return [rng.randint(1, 10**12) for _ in range(n)]


def test_concentration_metrics_stay_in_range() -> None:
    """Property test: HHI y Gini en [0,1], porcentajes en [0,100], para 200 casos."""
    rng = random.Random(20260803)
    for _ in range(200):
        balances = _random_balances(rng, rng.randint(1, 60))
        metrics = concentration(balances)
        assert Decimal(0) <= metrics.hhi <= Decimal(1)
        assert Decimal(0) <= metrics.gini <= Decimal(1)
        assert Decimal(0) <= metrics.normalized_entropy <= Decimal(1)
        for pct in (metrics.top1_pct, metrics.top5_pct, metrics.top10_pct, metrics.top20_pct):
            assert Decimal(0) <= pct <= Decimal(100)


def test_top_percentages_are_cumulative() -> None:
    rng = random.Random(7)
    for _ in range(100):
        balances = _random_balances(rng, rng.randint(20, 80))
        m = concentration(balances)
        assert m.top1_pct <= m.top5_pct <= m.top10_pct <= m.top20_pct


def test_concentration_is_deterministic() -> None:
    balances = [500, 300, 100, 50, 25, 25]
    first = concentration(balances).as_dict()
    for _ in range(20):
        assert concentration(balances).as_dict() == first
    # El orden de entrada no debe cambiar nada.
    assert concentration(list(reversed(balances))).as_dict() == first


def test_single_holder_is_maximum_concentration() -> None:
    assert herfindahl([1000]) == Decimal(1)
    assert gini([1000]) == Decimal(1)
    assert top_n_pct([1000], 1) == Decimal(100)
    assert entropy([1000]) == Decimal(0)


def test_perfectly_even_distribution_is_minimum_inequality() -> None:
    balances = [100] * 50
    # float() explicito: pytest.approx no resta Decimal con float.
    assert float(gini(balances)) == pytest.approx(0.0, abs=1e-9)
    assert float(herfindahl(balances)) == pytest.approx(0.02, abs=1e-9)
    assert float(normalized_entropy(balances)) == pytest.approx(1.0, abs=1e-9)


def test_excluding_the_pool_changes_the_verdict() -> None:
    """Incluir el pool subestima SIEMPRE la concentracion.

    Aqui el supply real esta en dos manos, pero con el pool dentro parece repartido. Es el
    error que hace pasar por sano un token que no lo es.
    """
    balances = {"pool": 900_000, "ballena_a": 60_000, "ballena_b": 40_000}
    with_pool = concentration(list(balances.values()))
    without_pool = concentration(list(exclude_known_accounts(balances, ["pool"]).values()))

    assert float(with_pool.top1_pct) == pytest.approx(90.0, abs=0.1)
    assert float(without_pool.top1_pct) == pytest.approx(60.0, abs=0.1)
    assert without_pool.holder_count == 2
    # Sin el pool, los dos holders reales acaparan el 100%.
    assert float(without_pool.top5_pct) == pytest.approx(100.0, abs=0.1)


def test_empty_and_zero_balances_do_not_crash() -> None:
    for balances in ([], [0, 0, 0]):
        metrics = concentration(balances)
        assert metrics.holder_count == 0
        assert metrics.hhi == Decimal(0)


# --- Anti-leakage -------------------------------------------------------------------------

NOW = datetime(2026, 8, 3, 12, 0, 0, tzinfo=UTC)


def _series() -> list[Observation[float]]:
    """Una observacion por segundo, desde 2 h antes hasta 1 h DESPUES de `NOW`."""
    return [
        Observation(at=NOW + timedelta(seconds=offset), value=float(offset))
        for offset in range(-7200, 3601, 1)
    ]


def test_future_observations_are_never_visible() -> None:
    """El test que da sentido a todo el modulo.

    La serie contiene una hora entera de datos POSTERIORES al instante de prediccion. Ni una
    sola debe llegar a una feature.
    """
    for window in WINDOW_SECONDS:
        visible = select_visible(_series(), window, NOW)
        assert visible, f"la ventana {window} no deberia quedar vacia"
        assert all(o.at <= NOW for o in visible), f"leakage en la ventana {window}"


def test_window_respects_its_lower_bound() -> None:
    for window, seconds in WINDOW_SECONDS.items():
        start, end = window_bounds(window, NOW)
        assert end == NOW
        assert (end - start).total_seconds() == seconds
        assert all(start <= o.at <= NOW for o in select_visible(_series(), window, NOW))


def test_window_sample_counts_match_their_length() -> None:
    engine = WindowedFeatures({"count": count})
    for window, seconds in WINDOW_SECONDS.items():
        result = engine.compute(_series(), window, NOW)
        # Una observacion por segundo, ambos extremos incluidos.
        assert result.sample_count == seconds + 1


def test_result_declares_its_lookback_boundary() -> None:
    """`lookback_start_at` se persiste y la base de datos lo verifica con un CHECK."""
    engine = WindowedFeatures({"count": count})
    result = engine.compute(_series(), "5m", NOW)
    assert result.lookback_start_at == NOW - timedelta(minutes=5)
    assert result.lookback_start_at <= result.as_of


def test_a_result_that_looks_ahead_is_rejected() -> None:
    """Aunque alguien construya un resultado a mano, no puede declarar futuro."""
    from mit_features.windows import WindowResult

    with pytest.raises(LeakageError):
        WindowResult(
            window="1m",
            as_of=NOW,
            lookback_start_at=NOW + timedelta(seconds=1),
            sample_count=0,
        )


def test_features_computed_at_two_instants_differ_only_by_new_data() -> None:
    """Calcular en el pasado no puede beneficiarse de lo que paso despues."""
    engine = WindowedFeatures({"total": total, "count": count})
    earlier = engine.compute(_series(), "1m", NOW - timedelta(hours=1))
    later = engine.compute(_series(), "1m", NOW)
    assert earlier.values != later.values
    assert earlier.as_of < later.as_of


def test_all_windows_of_spec_10_are_supported() -> None:
    assert set(WINDOW_SECONDS) == {"5s", "15s", "30s", "1m", "3m", "5m", "15m", "1h"}


def test_unknown_window_is_rejected() -> None:
    with pytest.raises(ValueError, match="ventana desconocida"):
        window_bounds("2h", NOW)


def test_basic_features_handle_empty_windows() -> None:
    empty: list[Observation[float]] = []
    assert count(empty) == 0.0
    assert total(empty) == 0.0
    assert mean(empty) == 0.0
    assert last(empty) == 0.0
    assert velocity(empty) == 0.0


def test_features_are_deterministic() -> None:
    engine = WindowedFeatures({"total": total, "mean": mean, "velocity": velocity})
    series = _series()
    first = engine.compute(series, "5m", NOW).values
    for _ in range(20):
        assert engine.compute(series, "5m", NOW).values == first


def test_last_uses_timestamps_not_input_order() -> None:
    """El orden de llegada no es el orden temporal: eso pasa con datos de red."""
    shuffled = [
        Observation(at=NOW - timedelta(seconds=1), value=10.0),
        Observation(at=NOW - timedelta(seconds=30), value=99.0),
        Observation(at=NOW - timedelta(seconds=10), value=50.0),
    ]
    assert last(shuffled) == 10.0
