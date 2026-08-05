"""Backtest de trailing stop sobre estampidas (SPEC.md 13, 18).

**Escrito ANTES de la implementacion** (CLAUDE.md 0.4): decide cuanto se gana o se pierde, asi
que su contrato lo dictan los tests.

El error que estos tests existen para impedir: calcular la salida como `pico x (1 - trailing)`.
Un trailing stop salta en el PRIMER retroceso que supera el umbral, no espera al maximo de toda
la serie. Si el token se hunde un 25% a mitad de subida y luego triplica, un stop del 20% te
saco abajo y no viste el triple. Confundir ambas cosas infla el resultado.
"""

from __future__ import annotations

import pytest
from mit_simulation.trailing import CostModel, backtest_series, sweep

# Sin costes: aisla la mecanica del stop de la aritmetica de comisiones.
FREE = CostModel(
    entry_fee=0.0, exit_fee=0.0, entry_slippage=0.0, exit_slippage=0.0, fixed_sol=0.0
)


def test_the_stop_fires_on_the_first_retracement_not_at_the_peak() -> None:
    """EL test que importa: sube, retrocede un 25%, y luego se dispara.

    Con un stop del 20% la salida ocurre en el retroceso, a 75. Quien calcule
    `pico x (1 - trailing)` diria 200 x 0,8 = 160 y se inventaria mas del doble.
    """
    series = [(0.0, 100.0), (1.0, 120.0), (2.0, 90.0), (3.0, 200.0)]
    result = backtest_series(series, trailing=0.20, costs=FREE)

    assert result.exit_price == pytest.approx(90.0)
    assert result.exit_reason == "trailing"
    assert result.multiple == pytest.approx(0.90)
    assert result.peak_multiple == pytest.approx(2.0), "el pico posterior se registra igual"
    assert result.captured_peak is False


def test_a_dip_smaller_than_the_stop_does_not_close_the_position() -> None:
    """Un pico de ventas breve no puede sacarte: es justo lo que hay que tolerar."""
    series = [(0.0, 100.0), (1.0, 150.0), (2.0, 140.0), (3.0, 300.0), (4.0, 200.0)]
    result = backtest_series(series, trailing=0.20, costs=FREE)

    # El retroceso de 150 a 140 es del 6,7%: no salta. Sale desde el pico de 300.
    assert result.exit_price == pytest.approx(200.0)
    assert result.multiple == pytest.approx(2.0)
    assert result.captured_peak is True


def test_a_series_that_only_falls_loses_money() -> None:
    series = [(0.0, 100.0), (1.0, 95.0), (2.0, 70.0), (3.0, 50.0)]
    result = backtest_series(series, trailing=0.20, costs=FREE)

    assert result.exit_price == pytest.approx(70.0)
    assert result.multiple < 1.0
    assert result.profit_sol < 0


def test_if_the_stop_never_fires_the_exit_is_the_last_price() -> None:
    """No se puede suponer que se vendio en el maximo: se sale donde acaba la serie."""
    series = [(0.0, 100.0), (1.0, 150.0), (2.0, 200.0)]
    result = backtest_series(series, trailing=0.30, costs=FREE)

    assert result.exit_price == pytest.approx(200.0)
    assert result.exit_reason == "fin de serie"


def test_costs_are_charged_on_both_legs() -> None:
    """Entrar y salir cuesta. Ignorarlo es lo que convierte una perdida en un 'beneficio'."""
    series = [(0.0, 100.0), (1.0, 100.0)]
    costs = CostModel(
        entry_fee=0.01, exit_fee=0.01, entry_slippage=0.0, exit_slippage=0.0, fixed_sol=0.0
    )
    result = backtest_series(series, trailing=0.20, costs=costs, size_sol=1.0)

    # Precio plano: sin costes seria cero. Con un 1% por lado, se pierde ~2%.
    assert result.profit_sol < 0
    assert result.profit_sol == pytest.approx(-0.02, abs=0.001)


def test_a_flat_series_is_not_a_profit() -> None:
    result = backtest_series([(0.0, 50.0), (1.0, 50.0)], trailing=0.20, costs=FREE)
    assert result.multiple == pytest.approx(1.0)
    assert result.profit_sol == pytest.approx(0.0)


def test_too_short_a_series_is_rejected_not_guessed() -> None:
    with pytest.raises(ValueError, match="serie"):
        backtest_series([(0.0, 100.0)], trailing=0.20, costs=FREE)


def test_an_invalid_trailing_is_rejected() -> None:
    series = [(0.0, 100.0), (1.0, 110.0)]
    for bad in (0.0, 1.0, -0.1):
        with pytest.raises(ValueError, match="trailing"):
            backtest_series(series, trailing=bad, costs=FREE)


def test_the_sweep_reports_every_level_it_tried() -> None:
    """Barrer varios niveles evita elegir uno a dedo y presentarlo como el bueno."""
    cases = [
        [(0.0, 100.0), (1.0, 200.0), (2.0, 150.0)],
        [(0.0, 100.0), (1.0, 130.0), (2.0, 60.0)],
    ]
    table = sweep(cases, levels=(0.10, 0.25), costs=FREE, size_sol=1.0)

    assert {row.trailing for row in table} == {0.10, 0.25}
    for row in table:
        assert row.cases == 2
        assert row.winners + row.losers <= row.cases


def test_the_sweep_ignores_series_it_cannot_use() -> None:
    """Una serie corta no invalida el barrido entero: se descarta y se cuenta."""
    table = sweep([[(0.0, 100.0)], [(0.0, 100.0), (1.0, 90.0)]], levels=(0.05,), costs=FREE)
    assert table[0].cases == 1
