"""Reglas de elegibilidad: los 17 vetos de SPEC.md 12.

Hay un test por cada veto, y todos comprueban lo mismo: que la condicion peligrosa IMPIDE la
compra. Un veto que se detecta pero no bloquea no sirve de nada.
"""

from __future__ import annotations

from typing import Any

import pytest
from mit_data_models.enums import EligibilityVeto
from mit_strategies.eligibility import (
    EligibilityInputs,
    EligibilityThresholds,
    evaluate,
)

# (campo, valor peligroso, veto esperado) — uno por cada regla de SPEC.md 12.
DANGEROUS_CONDITIONS = [
    ("data_confidence", 10.0, EligibilityVeto.LOW_DATA_CONFIDENCE),
    ("rug_risk", 90.0, EligibilityVeto.HIGH_RUG_RISK),
    ("manipulation_risk", 95.0, EligibilityVeto.HIGH_MANIPULATION_RISK),
    ("liquidity_lamports", 1_000, EligibilityVeto.INSUFFICIENT_LIQUIDITY),
    ("has_verified_exit_route", False, EligibilityVeto.NO_EXIT_ROUTE),
    ("sell_simulation_succeeded", False, EligibilityVeto.SELL_SIMULATION_FAILED),
    ("price_impact_bps", 5_000, EligibilityVeto.PRICE_IMPACT_TOO_HIGH),
    ("creator_history_critical", True, EligibilityVeto.CREATOR_HISTORY_CRITICAL),
    ("top10_pct_adjusted", 90.0, EligibilityVeto.HOLDER_CONCENTRATION),
    ("dominant_cluster_pct", 80.0, EligibilityVeto.DANGEROUS_CLUSTER),
    ("pump_pct", 5_000.0, EligibilityVeto.ALREADY_PUMPED),
    ("narrative_exhausted", True, EligibilityVeto.NARRATIVE_EXHAUSTED),
    ("spread_bps", 9_000, EligibilityVeto.EXCESSIVE_SPREAD),
    ("data_age_ms", 60_000, EligibilityVeto.STALE_DATA),
    ("source_divergence_pct", 50.0, EligibilityVeto.SOURCE_DIVERGENCE),
    ("sufficient_sol", False, EligibilityVeto.INSUFFICIENT_SOL),
    ("daily_risk_limit_reached", True, EligibilityVeto.DAILY_RISK_LIMIT_REACHED),
]


def test_all_seventeen_vetoes_of_spec_12_are_covered() -> None:
    """Si SPEC.md 12 gana un veto, este test falla hasta que se anada su caso."""
    assert len(DANGEROUS_CONDITIONS) == len(list(EligibilityVeto)) == 17


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    DANGEROUS_CONDITIONS,
    ids=[veto.value for _, _, veto in DANGEROUS_CONDITIONS],
)
def test_each_dangerous_condition_blocks_the_purchase(
    field: str, value: Any, expected: EligibilityVeto
) -> None:
    result = evaluate(EligibilityInputs(**{field: value}))
    assert not result.eligible, f"{field}={value} deberia impedir la compra"
    assert expected in {v.veto for v in result.vetoes}


def test_a_clean_token_is_eligible() -> None:
    result = evaluate(EligibilityInputs())
    assert result.eligible
    assert result.vetoes == ()


def test_every_veto_records_its_value_and_threshold() -> None:
    """Sin cifra y umbral, la decision no se puede reconstruir ni el umbral discutir."""
    result = evaluate(EligibilityInputs(rug_risk=90.0, top10_pct_adjusted=88.0))
    assert len(result.vetoes) == 2
    for record in result.vetoes:
        assert record.reason
        assert record.value is not None
        assert record.threshold is not None


def test_all_failing_vetoes_are_reported_not_just_the_first() -> None:
    """Parar en el primero haria creer al operador que va mejorando al arreglar uno."""
    result = evaluate(
        EligibilityInputs(
            data_confidence=5.0,
            rug_risk=99.0,
            manipulation_risk=99.0,
            sell_simulation_succeeded=False,
            narrative_exhausted=True,
            sufficient_sol=False,
        )
    )
    assert len(result.vetoes) >= 6


def test_a_perfect_score_cannot_override_a_veto() -> None:
    """SPEC.md 12: un veto NO se compensa con puntuacion.

    La elegibilidad ni siquiera recibe el OpportunityScore: no hay por donde compensarlo.
    """
    assert "opportunity" not in EligibilityInputs.__annotations__
    assert "score" not in EligibilityInputs.__annotations__
    result = evaluate(EligibilityInputs(rug_risk=99.0))
    assert not result.eligible


def test_evaluation_is_deterministic() -> None:
    inputs = EligibilityInputs(rug_risk=50.0, spread_bps=800)
    first = evaluate(inputs).as_dict()
    for _ in range(30):
        assert evaluate(inputs).as_dict() == first


def test_thresholds_are_configurable_but_explicit() -> None:
    strict = EligibilityThresholds(max_rug_risk=1.0)
    assert not evaluate(EligibilityInputs(rug_risk=5.0), strict).eligible
    assert evaluate(EligibilityInputs(rug_risk=5.0)).eligible


def test_boundary_values_do_not_trip_the_veto() -> None:
    """Justo en el umbral NO se veta: el veto es por SUPERARLO."""
    thresholds = EligibilityThresholds()
    at_limit = EligibilityInputs(
        rug_risk=thresholds.max_rug_risk,
        data_confidence=thresholds.min_data_confidence,
        price_impact_bps=thresholds.max_price_impact_bps,
    )
    assert evaluate(at_limit).eligible
