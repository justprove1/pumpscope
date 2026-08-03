"""Scores, stops, señales y alertas (SPEC.md 11, 13, 14, 22)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from mit_data_models.enums import SignalType
from mit_notifications.alerts import Alert, AlertChannel, AlertDispatcher, AlertSeverity
from mit_risk.stops import PositionState, StopConfig, evaluate_stops
from mit_risk.types import StopType
from mit_strategies.eligibility import EligibilityInputs, evaluate
from mit_strategies.scores import ScoreWeights, TokenScores, opportunity_score
from mit_strategies.signals import Signal, SignalThresholds, exit_signal, generate

NOW = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)


def _good_scores(**over: float) -> TokenScores:
    base: dict[str, float] = {
        "narrative": 90.0,
        "momentum": 85.0,
        "liquidity": 80.0,
        "holder_quality": 80.0,
        "distribution": 75.0,
        "creator": 70.0,
        "whale": 70.0,
        "social_authenticity": 80.0,
        "exit_liquidity": 75.0,
        "data_confidence": 95.0,
    }
    base.update(over)
    return TokenScores(**base)


# --- Scores ---------------------------------------------------------------------------------


def test_all_thirteen_scores_of_spec_11_exist() -> None:
    from dataclasses import fields

    assert len(fields(TokenScores)) == 13


def test_scores_outside_range_are_rejected() -> None:
    with pytest.raises(ValueError, match="fuera de rango"):
        TokenScores(narrative=101.0)
    with pytest.raises(ValueError, match="fuera de rango"):
        TokenScores(momentum=-1.0)


def test_opportunity_score_stays_in_range() -> None:
    for scores in (TokenScores(), _good_scores(), _good_scores(rug_risk=100.0)):
        assert 0.0 <= opportunity_score(scores).opportunity <= 100.0


def test_risk_subtracts_it_does_not_average() -> None:
    """Un rug alto no se salva por una narrativa perfecta.

    Si los riesgos se promediaran, un token con rug 80 y todo lo demas excelente saldria
    "bueno con matices". La media ponderada convierte una alarma en un detalle.
    """
    clean = opportunity_score(_good_scores()).opportunity
    rugged = opportunity_score(_good_scores(rug_risk=80.0)).opportunity
    assert clean > 70
    # No se exige exactamente 0: se exige que el desplome sea brutal y no un matiz.
    assert rugged < 25.0
    assert clean - rugged > 50.0
    # Y con rug maximo queda practicamente en cero por perfecto que sea el resto.
    assert opportunity_score(_good_scores(rug_risk=100.0)).opportunity < 2.0


def test_manipulation_also_subtracts() -> None:
    clean = opportunity_score(_good_scores()).opportunity
    manipulated = opportunity_score(_good_scores(manipulation_risk=60.0)).opportunity
    assert manipulated < clean


def test_breakdown_explains_every_contribution() -> None:
    """Un score sin desglose no es explicable, y SPEC.md 11 exige pesos explicables."""
    breakdown = opportunity_score(_good_scores())
    assert len(breakdown.contributions) == 10
    assert set(breakdown.penalties) == {"manipulation", "rug"}
    assert breakdown.weights


def test_weights_are_data_not_hidden_constants() -> None:
    strict = ScoreWeights(rug_penalty=0.1)
    assert (
        opportunity_score(_good_scores(rug_risk=50.0), strict).opportunity
        > opportunity_score(_good_scores(rug_risk=50.0)).opportunity
    )


def test_scoring_is_deterministic() -> None:
    scores = _good_scores()
    first = opportunity_score(scores).as_dict()
    for _ in range(30):
        assert opportunity_score(scores).as_dict() == first


# --- Stops ----------------------------------------------------------------------------------


def test_no_stop_on_a_healthy_position() -> None:
    assert evaluate_stops(PositionState()) is None


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (PositionState(exit_liquidity_lamports=1), StopType.LIQUIDITY),
        (PositionState(unrealized_return=-0.5), StopType.HARD),
        (PositionState(whale_exiting=True), StopType.WHALE_EXIT),
        (PositionState(narrative_exhausted=True), StopType.NARRATIVE),
        (PositionState(max_favorable_return=0.8, unrealized_return=0.1), StopType.TRAILING),
        (PositionState(entry_score=90.0, current_score=40.0), StopType.SOFT),
        (PositionState(unrealized_return=0.35), StopType.PARTIAL_TAKE_PROFIT),
        (
            PositionState(unrealized_return=0.25, partial_taken=True),
            StopType.BREAK_EVEN,
        ),
        (PositionState(held_seconds=99_999), StopType.TIME),
    ],
    ids=[
        "liquidez",
        "hard",
        "whale",
        "narrativa",
        "trailing",
        "soft",
        "toma_parcial",
        "break_even",
        "tiempo",
    ],
)
def test_each_of_the_nine_stops_fires(state: PositionState, expected: StopType) -> None:
    trigger = evaluate_stops(state)
    assert trigger is not None
    assert trigger.stop == expected
    assert trigger.reason


def test_illiquidity_takes_priority_over_the_hard_stop() -> None:
    """Si no se puede salir, esperar a perder el porcentaje pactado no arregla nada."""
    trigger = evaluate_stops(PositionState(unrealized_return=-0.9, exit_liquidity_lamports=1))
    assert trigger is not None
    assert trigger.stop == StopType.LIQUIDITY


def test_partial_take_profit_exits_only_a_fraction() -> None:
    trigger = evaluate_stops(PositionState(unrealized_return=0.35))
    assert trigger is not None
    assert 0 < trigger.exit_fraction < 1


def test_stop_evaluation_is_deterministic() -> None:
    state = PositionState(unrealized_return=-0.5)
    first = evaluate_stops(state)
    for _ in range(30):
        again = evaluate_stops(state)
        assert again is not None and first is not None
        assert again.stop == first.stop


def test_stop_thresholds_are_configurable() -> None:
    tight = StopConfig(hard_stop_loss_fraction=0.05)
    trigger = evaluate_stops(PositionState(unrealized_return=-0.10), tight)
    assert trigger is not None
    assert trigger.stop == StopType.HARD


# --- Señales --------------------------------------------------------------------------------


def _signal(score_overrides: dict[str, float] | None = None, **kwargs: Any) -> Signal:
    breakdown = opportunity_score(_good_scores(**(score_overrides or {})))
    params: dict[str, Any] = {
        "timestamp": NOW,
        "mint": "M",
        "breakdown": breakdown,
        "eligibility": evaluate(EligibilityInputs()),
        "confidence": 0.9,
        "recommended_size_lamports": 50_000_000,
    }
    params.update(kwargs)
    return generate(**params)


def test_a_strong_token_produces_an_entry_signal() -> None:
    signal = _signal()
    assert signal.signal_type == SignalType.ENTER
    assert signal.recommended_size_lamports == 50_000_000


def test_a_veto_forces_ignore_no_matter_the_score() -> None:
    """SPEC.md 12: ninguna puntuacion compensa un veto."""
    signal = _signal(eligibility=evaluate(EligibilityInputs(rug_risk=99.0)))
    assert signal.signal_type == SignalType.IGNORE
    assert signal.score > 70, "el score sigue siendo alto: el veto es lo que manda"
    assert signal.recommended_size_lamports == 0
    assert signal.eligibility_vetoes


def test_low_confidence_downgrades_to_watch() -> None:
    assert _signal(confidence=0.2).signal_type == SignalType.WATCH


def test_only_entry_signals_carry_a_size() -> None:
    """Una senal de vigilancia no puede llevar importe: no autoriza nada."""
    assert _signal(confidence=0.2).recommended_size_lamports == 0


def test_every_signal_explains_itself() -> None:
    """SPEC.md 13: la senal incluye por que se abre o se cierra."""
    for signal in (_signal(), _signal(eligibility=evaluate(EligibilityInputs(rug_risk=99.0)))):
        assert signal.explanation
        assert signal.as_dict()["explanation"]


def test_entry_signals_declare_invalidation_and_planned_exit() -> None:
    signal = _signal()
    assert signal.invalidation
    assert signal.planned_exit
    assert signal.max_duration_seconds > 0


def test_signal_generation_is_deterministic() -> None:
    first = _signal().as_dict()
    for _ in range(20):
        assert _signal().as_dict() == first


def test_thresholds_are_configurable() -> None:
    strict = SignalThresholds(enter=99.0)
    assert _signal(thresholds=strict).signal_type != SignalType.ENTER


def test_exit_signal_names_the_stop_that_fired() -> None:
    signal = exit_signal(
        timestamp=NOW, mint="M", stop_name="hard", reason="perdida 30%", emergency=True
    )
    assert signal.signal_type == SignalType.EMERGENCY_EXIT
    assert "hard" in signal.explanation


def test_no_signal_type_allows_adding_to_a_position() -> None:
    """SPEC.md 13: nada de averaging down automatico."""
    assert SignalType.ADD_FORBIDDEN in set(SignalType)
    entry_types = {SignalType.ENTER, SignalType.ENTER_SMALL}
    assert SignalType.ADD_FORBIDDEN not in entry_types


# --- Alertas --------------------------------------------------------------------------------


def test_an_alert_without_verifiable_data_is_rejected() -> None:
    """SPEC.md 22: datos verificables, no mensajes vagos."""
    with pytest.raises(ValueError, match="datos verificables"):
        Alert(
            alert_type="algo_raro",
            severity=AlertSeverity.WARNING,
            title="Actividad sospechosa",
            facts={},
        )


def test_alerts_render_their_numbers() -> None:
    alert = Alert(
        alert_type="creator_dump",
        severity=AlertSeverity.CRITICAL,
        title="El creador esta vendiendo",
        facts={"sol_vendido": 0.070, "minutos": 3},
        mint="5wT2ps",
    )
    rendered = alert.render()
    assert "0.07" in rendered
    assert "5wT2ps" in rendered
    assert "CRITICAL" in rendered


def test_repeated_alerts_are_deduplicated() -> None:
    """Sin dedup, una condicion persistente genera una alerta por tick y se ignora el canal."""
    dispatcher = AlertDispatcher(cooldown=timedelta(minutes=10))
    alert = Alert(
        alert_type="liquidez_baja",
        severity=AlertSeverity.WARNING,
        title="Liquidez cayendo",
        facts={"sol": 1.2},
    )
    assert dispatcher.should_send(alert, NOW)
    assert not dispatcher.should_send(alert, NOW + timedelta(minutes=5))
    assert dispatcher.should_send(alert, NOW + timedelta(minutes=11))


def test_critical_alerts_are_never_silenced() -> None:
    """Silenciar un kill switch porque ya se aviso hace ocho minutos no es admisible."""
    dispatcher = AlertDispatcher()
    alert = Alert(
        alert_type="kill_switch",
        severity=AlertSeverity.CRITICAL,
        title="Kill switch activado",
        facts={"motivo": "drawdown"},
    )
    for minute in range(5):
        assert dispatcher.should_send(alert, NOW + timedelta(minutes=minute))


def test_critical_alerts_use_every_channel() -> None:
    dispatcher = AlertDispatcher()
    assert len(dispatcher.channels_for(AlertSeverity.CRITICAL)) == 4
    assert AlertChannel.INTERNAL in dispatcher.channels_for(AlertSeverity.INFO)


def test_dedup_key_is_stable_for_the_same_facts() -> None:
    def build() -> Alert:
        return Alert(
            alert_type="whale",
            severity=AlertSeverity.WARNING,
            title="Whale vendiendo",
            facts={"sol": 12.5, "wallet": "abc"},
            mint="M",
        )

    assert build().dedup_key == build().dedup_key
