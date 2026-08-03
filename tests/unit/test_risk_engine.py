"""RiskEngine: sizing, limites y kill switches (SPEC.md 14).

**Escrito ANTES de la implementacion** (CLAUDE.md 0.4). Estos tests definen el contrato del
componente que decide cuanto dinero se compromete; si el contrato lo dicta el codigo en vez
de los tests, acaba siendo "lo que salga".

Dos invariantes que este archivo persigue por encima de todo:

1. **Ningun camino permite superar un limite.** No basta con que el caso normal respete el
   limite: se prueban los casos borde y las combinaciones.
2. **El motor es DETERMINISTA.** Mismos datos, misma decision, siempre. Sin ML, sin LLM, sin
   aleatoriedad.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

import pytest
from mit_risk import (
    AccountState,
    KillSwitch,
    MarketSnapshot,
    RiskEngine,
    RiskLimits,
    SizingInputs,
    StopType,
)

SOL = 1_000_000_000
NOW = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)


def _limits(**overrides: Any) -> RiskLimits:
    base: dict[str, Any] = {
        "risk_per_trade_pct": 0.5,
        "max_exposure_per_token_pct": 3.0,
        "max_daily_loss_pct": 3.0,
        "max_drawdown_pct": 10.0,
        "max_consecutive_losses": 4,
        "min_sol_fee_reserve_lamports": 20_000_000,
        "max_order_lamports": 50_000_000,
        "max_total_exposure_lamports": 200_000_000,
        "max_open_positions": 1,
        "max_price_impact_bps": 300,
        "max_liquidity_fraction": 0.05,
    }
    base.update(overrides)
    return RiskLimits(**base)


def _account(**overrides: Any) -> AccountState:
    base: dict[str, Any] = {
        "balance_lamports": 10 * SOL,
        "equity_lamports": 10 * SOL,
        "peak_equity_lamports": 10 * SOL,
        "open_positions": 0,
        "exposure_lamports": 0,
        "realized_pnl_day_lamports": 0,
        "consecutive_losses": 0,
        "spent_today_lamports": 0,
    }
    base.update(overrides)
    return AccountState(**base)


def _inputs(**overrides: Any) -> SizingInputs:
    base: dict[str, Any] = {
        "stop_distance_fraction": 0.25,
        "liquidity_lamports": 50 * SOL,
        "volatility": 0.3,
        "expected_slippage_bps": 100,
        "confidence": 0.8,
        "correlated_exposure_lamports": 0,
        "estimated_exit_cost_lamports": 5_000_000,
    }
    base.update(overrides)
    return SizingInputs(**base)


# =============================================================================================
# Sizing: nunca por encima de ningun limite
# =============================================================================================


def test_size_is_the_minimum_of_all_constraints_never_the_maximum() -> None:
    """El tamano es el MINIMO de todas las restricciones.

    Si fuera el maximo de alguna, bastaria una restriccion laxa para colarse por encima de
    las demas.
    """
    engine = RiskEngine(_limits())
    result = engine.size_position(_account(), _inputs())
    assert result.lamports == min(result.constraints.values())
    assert result.binding_constraint in result.constraints


def test_never_exceeds_the_per_order_limit() -> None:
    engine = RiskEngine(_limits(max_order_lamports=10_000_000))
    result = engine.size_position(_account(balance_lamports=1000 * SOL), _inputs())
    assert result.lamports <= 10_000_000


def test_never_exceeds_the_per_token_exposure_limit() -> None:
    engine = RiskEngine(_limits(max_exposure_per_token_pct=1.0, max_order_lamports=10 * SOL))
    result = engine.size_position(_account(equity_lamports=10 * SOL), _inputs())
    assert result.lamports <= int(0.01 * 10 * SOL)


def test_never_exceeds_total_exposure_already_committed() -> None:
    """Lo ya comprometido descuenta del margen restante."""
    engine = RiskEngine(_limits(max_total_exposure_lamports=100_000_000))
    result = engine.size_position(_account(exposure_lamports=90_000_000), _inputs())
    assert result.lamports <= 10_000_000


def test_never_spends_the_fee_reserve() -> None:
    """SPEC.md 14: siempre queda SOL para comisiones y para poder SALIR."""
    engine = RiskEngine(_limits(min_sol_fee_reserve_lamports=1 * SOL))
    account = _account(balance_lamports=1_100_000_000, equity_lamports=1_100_000_000)
    result = engine.size_position(account, _inputs(estimated_exit_cost_lamports=50_000_000))
    assert result.lamports <= 1_100_000_000 - 1 * SOL - 50_000_000


def test_size_shrinks_when_the_stop_is_far() -> None:
    """Mas distancia al stop, menos tamano: es la definicion de riesgo por operacion."""
    engine = RiskEngine(_limits(max_order_lamports=10 * SOL))
    near = engine.size_position(_account(), _inputs(stop_distance_fraction=0.10))
    far = engine.size_position(_account(), _inputs(stop_distance_fraction=0.50))
    assert far.lamports < near.lamports


def test_size_shrinks_with_low_confidence() -> None:
    engine = RiskEngine(_limits(max_order_lamports=10 * SOL))
    sure = engine.size_position(_account(), _inputs(confidence=1.0))
    unsure = engine.size_position(_account(), _inputs(confidence=0.2))
    assert unsure.lamports < sure.lamports


def test_size_shrinks_with_high_volatility() -> None:
    engine = RiskEngine(_limits(max_order_lamports=10 * SOL))
    calm = engine.size_position(_account(), _inputs(volatility=0.1))
    wild = engine.size_position(_account(), _inputs(volatility=2.0))
    assert wild.lamports < calm.lamports


def test_size_is_capped_by_available_liquidity() -> None:
    """No se puede ocupar una fraccion grande de la liquidez: la salida seria imposible."""
    engine = RiskEngine(_limits(max_liquidity_fraction=0.05, max_order_lamports=10 * SOL))
    result = engine.size_position(_account(), _inputs(liquidity_lamports=1 * SOL))
    assert result.lamports <= int(0.05 * SOL)


def test_correlated_exposure_reduces_the_size() -> None:
    """Misma narrativa o mismo creador cuentan como la misma apuesta."""
    engine = RiskEngine(_limits(max_order_lamports=10 * SOL))
    alone = engine.size_position(_account(), _inputs(correlated_exposure_lamports=0))
    crowded = engine.size_position(_account(), _inputs(correlated_exposure_lamports=150_000_000))
    assert crowded.lamports < alone.lamports


def test_daily_loss_shrinks_the_size_before_stopping_altogether() -> None:
    """Perder reduce el tamano progresivamente, no solo al tocar el limite."""
    engine = RiskEngine(_limits(max_order_lamports=10 * SOL))
    fresh = engine.size_position(_account(), _inputs())
    bleeding = engine.size_position(
        _account(realized_pnl_day_lamports=-int(0.02 * 10 * SOL)), _inputs()
    )
    assert bleeding.lamports < fresh.lamports


def test_size_is_zero_when_below_the_minimum_operable() -> None:
    """Si no llega al minimo, NO se opera. Nunca se redondea hacia arriba."""
    engine = RiskEngine(_limits(max_order_lamports=1000))
    result = engine.size_position(_account(), _inputs())
    assert result.lamports == 0


def test_size_is_zero_without_balance() -> None:
    engine = RiskEngine(_limits())
    result = engine.size_position(_account(balance_lamports=0, equity_lamports=0), _inputs())
    assert result.lamports == 0


def test_sizing_is_deterministic() -> None:
    """Mismos datos, misma decision, siempre. Sin ML, sin LLM, sin azar."""
    engine = RiskEngine(_limits())
    account, inputs = _account(), _inputs()
    first = engine.size_position(account, inputs)
    for _ in range(50):
        again = engine.size_position(account, inputs)
        assert again.lamports == first.lamports
        assert again.constraints == first.constraints


def test_size_is_never_negative() -> None:
    engine = RiskEngine(_limits())
    for exposure in (0, 10**9, 10**12):
        result = engine.size_position(_account(exposure_lamports=exposure), _inputs())
        assert result.lamports >= 0


# =============================================================================================
# Kill switches: uno por cada disparador de SPEC.md 14
# =============================================================================================


def test_no_kill_switch_on_a_healthy_account() -> None:
    engine = RiskEngine(_limits())
    assert engine.kill_switches(_account(), MarketSnapshot()) == ()


@pytest.mark.parametrize(
    ("account_overrides", "market_overrides", "expected"),
    [
        ({"realized_pnl_day_lamports": -int(0.05 * 10 * SOL)}, {}, KillSwitch.DAILY_LOSS),
        ({"equity_lamports": 8 * SOL}, {}, KillSwitch.DRAWDOWN),
        ({"consecutive_losses": 5}, {}, KillSwitch.CONSECUTIVE_LOSSES),
        ({}, {"provider_down": True}, KillSwitch.PROVIDER_DOWN),
        ({}, {"latency_p95_ms": 30_000.0}, KillSwitch.LATENCY),
        ({}, {"error_rate": 0.5}, KillSwitch.ERROR_RATE),
        ({}, {"price_divergence_pct": 25.0}, KillSwitch.PRICE_DIVERGENCE),
        ({}, {"balance_mismatch": True}, KillSwitch.BALANCE_ANOMALY),
        ({}, {"duplicate_transactions": 2}, KillSwitch.DUPLICATE_TRANSACTIONS),
        ({}, {"unexpected_exposure": True}, KillSwitch.UNEXPECTED_EXPOSURE),
        ({}, {"unauthorized_signature": True}, KillSwitch.UNAUTHORIZED_SIGNATURE),
        ({}, {"unapproved_config_change": True}, KillSwitch.UNAPPROVED_CONFIG),
    ],
    ids=[
        "perdida_diaria",
        "drawdown",
        "perdidas_consecutivas",
        "proveedor_caido",
        "latencia",
        "tasa_errores",
        "divergencia_precios",
        "saldo_anomalo",
        "transacciones_duplicadas",
        "exposicion_inesperada",
        "firma_no_autorizada",
        "config_no_aprobada",
    ],
)
def test_each_dangerous_condition_trips_its_kill_switch(
    account_overrides: dict[str, Any],
    market_overrides: dict[str, Any],
    expected: KillSwitch,
) -> None:
    """SPEC.md 14: cada condicion peligrosa DETIENE las compras."""
    engine = RiskEngine(_limits())
    switches = engine.kill_switches(
        _account(**account_overrides), MarketSnapshot(**market_overrides)
    )
    assert expected in switches


def test_a_tripped_kill_switch_blocks_every_purchase() -> None:
    """No basta con reportarlo: tiene que impedir la compra."""
    engine = RiskEngine(_limits())
    account = _account(consecutive_losses=99)
    decision = engine.can_open(account, MarketSnapshot(), _inputs())
    assert not decision.allowed
    assert any("kill switch" in reason.lower() for reason in decision.reasons)


def test_kill_switches_never_reactivate_on_their_own() -> None:
    """SPEC.md 14: la reactivacion es SIEMPRE manual."""
    engine = RiskEngine(_limits())
    tripped = _account(consecutive_losses=99)
    assert engine.kill_switches(tripped, MarketSnapshot())
    engine.trip(KillSwitch.DAILY_LOSS, reason="prueba")
    # Aunque la cuenta vuelva a estar sana, el switch manual sigue activo.
    assert KillSwitch.DAILY_LOSS in engine.kill_switches(_account(), MarketSnapshot())
    engine.reset(KillSwitch.DAILY_LOSS, operator="humano")
    assert KillSwitch.DAILY_LOSS not in engine.kill_switches(_account(), MarketSnapshot())


def test_kill_switch_detection_is_deterministic() -> None:
    engine = RiskEngine(_limits())
    account = _account(consecutive_losses=5)
    first = engine.kill_switches(account, MarketSnapshot())
    for _ in range(30):
        assert engine.kill_switches(account, MarketSnapshot()) == first


# =============================================================================================
# Stops
# =============================================================================================


@pytest.mark.parametrize(
    "stop",
    [
        StopType.HARD,
        StopType.SOFT,
        StopType.TRAILING,
        StopType.TIME,
        StopType.LIQUIDITY,
        StopType.NARRATIVE,
        StopType.WHALE_EXIT,
        StopType.BREAK_EVEN,
        StopType.PARTIAL_TAKE_PROFIT,
    ],
)
def test_all_nine_stops_of_spec_14_exist(stop: StopType) -> None:
    assert isinstance(stop.value, str)


def test_averaging_down_is_forbidden() -> None:
    """SPEC.md 13: nada de averaging down automatico en esta version."""
    engine = RiskEngine(_limits())
    decision = engine.can_add_to_position(_account(open_positions=1))
    assert not decision.allowed
    assert any("averaging" in r.lower() or "add_forbidden" in r.lower() for r in decision.reasons)


# =============================================================================================
# El LLM no puede tocar el dinero
# =============================================================================================


def test_no_llm_output_can_change_the_size() -> None:
    """CLAUDE.md 1: el LLM no cambia importes ni limites.

    Se comprueba estructuralmente: `size_position` recibe SizingInputs, y ese tipo no tiene
    ningun campo por el que pueda entrar texto o una sugerencia de un modelo.
    """
    fields = set(SizingInputs.__annotations__)
    forbidden = {"llm", "narrative_text", "suggestion", "recommended_size", "model_output"}
    assert not (fields & forbidden)
    # Y no acepta campos extra: no se puede colar uno nuevo por la puerta de atras.
    with pytest.raises(TypeError):
        SizingInputs(**{**asdict(_inputs()), "recommended_size_lamports": 999})


def test_confidence_is_bounded_so_it_cannot_inflate_the_size() -> None:
    """La confianza modula a la baja, nunca al alza por encima del limite."""
    engine = RiskEngine(_limits(max_order_lamports=10_000_000))
    absurd = engine.size_position(_account(), _inputs(confidence=999.0))
    assert absurd.lamports <= 10_000_000
