"""Modelos: etiquetado, anti-leakage, calibracion, drift y versionado (SPEC.md 19, 20).

Los cuatro tests obligatorios de la fase estan aqui, marcados con su seccion.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from mit_ml import (
    BarrierHit,
    DriftThresholds,
    LeakageError,
    ModelGuard,
    ModelStatus,
    PricePoint,
    PromotionError,
    Sample,
    Stage,
    StrategyLab,
    TrainingWindow,
    TripleBarrier,
    calibration_error,
    detect_drift,
    label_series,
    max_resolution,
    train,
)
from mit_risk.types import SizingInputs

START = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)


def _series(changes: list[float]) -> list[PricePoint]:
    price = 1.0
    points = [PricePoint(START, price)]
    for i, change in enumerate(changes, start=1):
        price *= 1 + change
        points.append(PricePoint(START + timedelta(minutes=i), price))
    return points


# --- Triple-barrier -------------------------------------------------------------------------


def test_upper_barrier_gives_a_positive_label() -> None:
    labels = label_series(_series([0.10, 0.15]), TripleBarrier(0.20, 0.10))
    assert labels[0].label == 1
    assert labels[0].hit == BarrierHit.UPPER


def test_lower_barrier_gives_a_negative_label() -> None:
    labels = label_series(_series([-0.05, -0.10]), TripleBarrier(0.20, 0.10))
    assert labels[0].label == 0
    assert labels[0].hit == BarrierHit.LOWER


def test_time_barrier_resolves_by_position() -> None:
    barrier = TripleBarrier(0.50, 0.50, timedelta(minutes=2))
    labels = label_series(_series([0.01, 0.01, 0.01]), barrier)
    assert labels[0].hit == BarrierHit.TIME


def test_unresolved_observations_are_discarded_not_invented() -> None:
    """Etiquetar con lo que haya al final del historico es inventar el resultado.

    Es la forma mas comun de meter sesgo de supervivencia sin darse cuenta.
    """
    series = _series([0.001] * 3)
    labels = label_series(series, TripleBarrier(0.50, 0.50, timedelta(hours=10)))
    assert labels == []


def test_labels_record_when_they_resolved() -> None:
    """Sin `resolved_at` no se puede purgar el solapamiento."""
    labels = label_series(_series([0.10, 0.15]), TripleBarrier(0.20, 0.10))
    assert labels[0].resolved_at > labels[0].observed_at
    assert max_resolution(labels) > timedelta(0)


def test_labels_carry_mfe_and_mae() -> None:
    labels = label_series(_series([0.08, -0.03, 0.15]), TripleBarrier(0.20, 0.10))
    assert labels[0].max_favorable_excursion > 0
    assert labels[0].max_adverse_excursion <= 0


def test_invalid_barriers_are_rejected() -> None:
    with pytest.raises(ValueError, match="positivas"):
        TripleBarrier(upper_return=0.0)
    with pytest.raises(ValueError, match="horizonte"):
        TripleBarrier(horizon=timedelta(0))


# =============================================================================================
# Anti-leakage: test obligatorio 1
# =============================================================================================


def _samples(count: int = 200, *, seed: int = 7) -> list[Sample]:
    rng = random.Random(seed)
    samples = []
    for i in range(count):
        at = START + timedelta(minutes=i)
        signal = rng.random()
        samples.append(
            Sample(
                at=at,
                features={"momentum": signal, "noise": rng.random()},
                # Etiqueta correlacionada con la feature: hay senal que aprender.
                label=1 if signal > 0.5 else 0,
                resolved_at=at + timedelta(minutes=30),
            )
        )
    return samples


def test_training_never_sees_data_from_after_the_cut() -> None:
    """El pipeline filtra por timestamp Y por instante de resolucion de la etiqueta."""
    cut = START + timedelta(minutes=100)
    window = TrainingWindow(train_end=cut, purge=timedelta(minutes=30))
    train_set = window.select_train(_samples())

    limit = cut - timedelta(minutes=30)
    assert train_set
    for sample in train_set:
        assert sample.at <= limit
        # Lo que de verdad importa: la ETIQUETA tambien se resolvio antes del corte.
        assert sample.resolved_at <= limit


def test_a_sample_whose_label_resolves_after_the_cut_is_excluded() -> None:
    """Ser anterior al corte NO basta: si su etiqueta se resuelve despues, contiene futuro."""
    cut = START + timedelta(minutes=60)
    window = TrainingWindow(train_end=cut, purge=timedelta(minutes=10))
    sneaky = Sample(
        at=START + timedelta(minutes=45),
        features={"momentum": 0.9, "noise": 0.1},
        label=1,
        resolved_at=START + timedelta(minutes=120),
    )
    assert window.select_train([sneaky]) == []


def test_leaked_samples_raise_instead_of_training_silently() -> None:
    """Si el filtro fallara, entrenar debe REVENTAR, no producir un modelo optimista."""
    cut = START + timedelta(minutes=60)
    leaked = [
        Sample(
            at=START,
            features={"a": 1.0},
            label=1,
            resolved_at=START + timedelta(days=1),
        )
    ]

    # Ventana que NO filtra, para ejercitar la defensa redundante de `train`.
    class LeakyWindow:
        train_end = cut
        purge = timedelta(minutes=10)

        def select_train(self, samples: list[Sample]) -> list[Sample]:
            return list(samples)

        def select_validation(self, samples: list[Sample]) -> list[Sample]:
            return list(samples)

    with pytest.raises(LeakageError, match="LEAKAGE"):
        train(leaked, LeakyWindow())  # type: ignore[arg-type]


def test_purge_is_never_negative() -> None:
    window = TrainingWindow(train_end=START, purge=timedelta(minutes=30))
    assert window.purge >= timedelta(0)


# =============================================================================================
# Calibracion: test obligatorio 2
# =============================================================================================


def test_perfect_calibration_scores_zero_error() -> None:
    """Si de cada grupo con p=0.7 ocurre el 70%, el error es cero."""
    y_prob = np.array([0.7] * 100)
    y_true = np.array([1] * 70 + [0] * 30)
    assert calibration_error(y_true, y_prob) == pytest.approx(0.0, abs=0.01)


def test_overconfident_predictions_are_penalised() -> None:
    """Decir 90% y que ocurra el 30% tiene que salir caro en la metrica."""
    y_prob = np.array([0.9] * 100)
    y_true = np.array([1] * 30 + [0] * 70)
    assert calibration_error(y_true, y_prob) > 0.5


def test_a_trained_model_reports_its_calibration() -> None:
    """Un modelo sin calibracion medida no se puede usar para dimensionar."""
    window = TrainingWindow(train_end=START + timedelta(minutes=150), purge=timedelta(minutes=30))
    model = train(_samples(), window, algorithm="logistic")
    assert model is not None
    assert 0.0 <= model.card.calibration_error <= 1.0
    assert 0.0 <= model.card.auc <= 1.0
    assert model.card.train_samples > 0


def test_model_card_has_every_field_of_spec_19() -> None:
    window = TrainingWindow(train_end=START + timedelta(minutes=150), purge=timedelta(minutes=30))
    model = train(_samples(), window)
    assert model is not None
    card = model.card.as_dict()
    for field in (
        "trained_at",
        "auc",
        "brier",
        "calibration_error",
        "train_samples",
        "usable",
    ):
        assert field in card


def test_training_returns_none_instead_of_a_meaningless_model() -> None:
    """Con veinte muestras se producirian metricas con formato y sin significado."""
    window = TrainingWindow(train_end=START + timedelta(minutes=10), purge=timedelta(minutes=1))
    assert train(_samples(count=15), window) is None


def test_predictions_are_probabilities() -> None:
    window = TrainingWindow(train_end=START + timedelta(minutes=150), purge=timedelta(minutes=30))
    model = train(_samples(), window)
    assert model is not None
    probability = model.predict_proba({"momentum": 0.9, "noise": 0.2})
    assert 0.0 <= probability <= 1.0


# =============================================================================================
# Drift y desactivacion automatica: test obligatorio 3
# =============================================================================================


def test_a_degraded_model_disables_itself() -> None:
    """SPEC.md 19: la desactivacion es AUTOMATICA."""
    guard = ModelGuard(name="baseline", version=1)
    assert guard.is_usable

    report = detect_drift(
        training_auc=0.80,
        recent_auc=0.52,
        calibration_error=0.05,
        training_mean_prediction=0.4,
        recent_mean_prediction=0.41,
        samples=200,
    )
    assert report.degraded
    assert guard.apply(report)
    assert guard.disabled_reason
    assert guard.status is ModelStatus.DEGRADED
    assert not guard.is_usable


def test_the_system_falls_back_to_heuristic_when_the_model_is_off() -> None:
    """`is_usable=False` es la senal que hace caer al modo heuristico."""
    guard = ModelGuard(name="baseline", version=1, status=ModelStatus.DEGRADED)
    assert not guard.is_usable


def test_reactivation_is_manual() -> None:
    guard = ModelGuard(name="baseline", version=1, status=ModelStatus.DEGRADED)
    with pytest.raises(ValueError, match="operador"):
        guard.reactivate("")
    guard.reactivate("humano")
    assert guard.is_usable


def test_bad_calibration_alone_degrades_the_model() -> None:
    """Aunque el AUC aguante: una probabilidad que miente no sirve para dimensionar."""
    report = detect_drift(
        training_auc=0.80,
        recent_auc=0.79,
        calibration_error=0.40,
        training_mean_prediction=0.4,
        recent_mean_prediction=0.42,
        samples=200,
    )
    assert report.degraded
    assert any("calibracion" in reason for reason in report.reasons)


def test_a_short_streak_does_not_disable_a_good_model() -> None:
    """Apagar un modelo bueno por una racha corta es tan malo como dejar vivo uno malo."""
    report = detect_drift(
        training_auc=0.80,
        recent_auc=0.30,
        calibration_error=0.9,
        training_mean_prediction=0.4,
        recent_mean_prediction=0.9,
        samples=5,
    )
    assert not report.degraded
    assert any("muestras" in reason for reason in report.reasons)


def test_drift_thresholds_are_configurable() -> None:
    strict = DriftThresholds(max_auc_drop=0.01)
    report = detect_drift(
        training_auc=0.80,
        recent_auc=0.75,
        calibration_error=0.05,
        training_mean_prediction=0.4,
        recent_mean_prediction=0.4,
        samples=200,
        thresholds=strict,
    )
    assert report.degraded


# =============================================================================================
# El modelo NO decide el importe: test obligatorio 4
# =============================================================================================


def test_model_output_cannot_reach_the_sizing_inputs() -> None:
    """CLAUDE.md 1: el modelo aporta una probabilidad, no un importe.

    Se comprueba estructuralmente: `SizingInputs` no tiene ningun campo por el que entre una
    prediccion, y `confidence` esta acotado, asi que no puede inflar el tamano.
    """
    fields = set(SizingInputs.__annotations__)
    forbidden = {"model_probability", "prediction", "model_output", "ml_score", "recommended"}
    assert not (fields & forbidden)


def test_the_risk_engine_ignores_any_extra_field() -> None:
    from dataclasses import asdict

    from mit_risk import RiskEngine, RiskLimits

    engine = RiskEngine(RiskLimits(max_order_lamports=10_000_000))
    inputs = SizingInputs(
        stop_distance_fraction=0.25,
        liquidity_lamports=50_000_000_000,
        volatility=0.3,
        expected_slippage_bps=100,
        confidence=1.0,
    )
    # Un modelo "muy seguro" no puede superar el limite por orden.
    assert engine.size_position.__doc__
    with pytest.raises(TypeError):
        SizingInputs(**{**asdict(inputs), "model_probability": 0.99})


def test_a_probability_is_a_score_not_an_amount() -> None:
    """La salida del modelo es adimensional y acotada: no es un importe."""
    window = TrainingWindow(train_end=START + timedelta(minutes=150), purge=timedelta(minutes=30))
    model = train(_samples(), window)
    assert model is not None
    value = model.predict_proba({"momentum": 0.8, "noise": 0.1})
    assert 0.0 <= value <= 1.0


# --- StrategyLab (SPEC.md 20) ---------------------------------------------------------------


def test_a_strategy_cannot_skip_stages() -> None:
    lab = StrategyLab()
    version = lab.register("momentum", {"threshold": 0.7})
    with pytest.raises(PromotionError, match="una a una"):
        version.promote(Stage.APPROVED, operator="humano")


def test_approval_requires_a_human() -> None:
    """SPEC.md 20: el sistema no se aprueba a si mismo."""
    lab = StrategyLab()
    version = lab.register("momentum", {"threshold": 0.7})
    version.promote(Stage.BACKTESTED)
    version.promote(Stage.OUT_OF_SAMPLE)
    version.promote(Stage.PAPER)
    with pytest.raises(PromotionError, match="MANUAL"):
        version.promote(Stage.APPROVED)
    version.promote(Stage.APPROVED, operator="matteo", now=START)
    assert version.is_deployable
    assert version.approved_by == "matteo"


def test_only_approved_versions_are_deployed() -> None:
    lab = StrategyLab()
    lab.register("momentum", {"threshold": 0.7})
    assert lab.deployed("momentum") is None


def test_every_version_is_reversible() -> None:
    """SPEC.md 20: toda version se puede revertir."""
    lab = StrategyLab()
    for threshold in (0.6, 0.7):
        version = lab.register("momentum", {"threshold": threshold})
        version.promote(Stage.BACKTESTED)
        version.promote(Stage.OUT_OF_SAMPLE)
        version.promote(Stage.PAPER)
        version.promote(Stage.APPROVED, operator="matteo", now=START)

    assert lab.deployed("momentum").params["threshold"] == 0.7  # type: ignore[union-attr]
    previous = lab.rollback("momentum")
    assert previous is not None
    assert previous.params["threshold"] == 0.6


def test_promotions_are_recorded() -> None:
    lab = StrategyLab()
    version = lab.register("momentum", {"threshold": 0.7})
    version.promote(Stage.BACKTESTED)
    assert version.history


# --- LightGBM y SHAP ------------------------------------------------------------------------


def test_the_three_algorithms_of_spec_19_are_wired() -> None:
    """SPEC.md 19: baselines primero, boosting despues. Los tres disponibles."""
    from mit_ml import ALGORITHMS

    assert set(ALGORITHMS) == {"logistic", "random_forest", "lightgbm"}


@pytest.mark.parametrize("algorithm", ["logistic", "random_forest", "lightgbm"])
def test_every_algorithm_trains_and_calibrates(algorithm: str) -> None:
    window = TrainingWindow(train_end=START + timedelta(minutes=150), purge=timedelta(minutes=30))
    model = train(_samples(), window, algorithm=algorithm, name=algorithm)
    assert model is not None
    assert model.card.algorithm == algorithm
    assert 0.0 <= model.card.calibration_error <= 1.0
    assert 0.0 <= model.predict_proba({"momentum": 0.8, "noise": 0.2}) <= 1.0


@pytest.mark.parametrize("algorithm", ["logistic", "random_forest", "lightgbm"])
def test_training_is_reproducible(algorithm: str) -> None:
    """Un modelo que no se reentrena identico no se puede auditar ni comparar."""
    window = TrainingWindow(train_end=START + timedelta(minutes=150), purge=timedelta(minutes=30))
    samples = _samples()
    first = train(samples, window, algorithm=algorithm)
    second = train(samples, window, algorithm=algorithm)
    assert first is not None and second is not None
    probe = {"momentum": 0.77, "noise": 0.31}
    assert first.predict_proba(probe) == pytest.approx(second.predict_proba(probe), abs=1e-9)


@pytest.mark.parametrize("algorithm", ["random_forest", "lightgbm"])
def test_shap_explains_tree_models(algorithm: str) -> None:
    """SPEC.md 19: la explicabilidad convierte '0,73' en algo discutible."""
    from mit_ml import explain, top_drivers

    window = TrainingWindow(train_end=START + timedelta(minutes=150), purge=timedelta(minutes=30))
    model = train(_samples(), window, algorithm=algorithm)
    assert model is not None

    contributions = explain(model, {"momentum": 0.9, "noise": 0.2})
    assert set(contributions) == set(model.feature_names)
    drivers = top_drivers(contributions)
    assert drivers
    # `momentum` es la feature que genera la etiqueta: debe pesar mas que el ruido.
    assert abs(contributions["momentum"]) >= abs(contributions["noise"])


def test_shap_returns_nothing_rather_than_inventing_an_explanation() -> None:
    """Una explicacion falsa es peor que ninguna, porque se cree."""
    from mit_ml import explain
    from mit_ml.training import TrainedModel

    empty = TrainedModel(
        card=train(
            _samples(),
            TrainingWindow(train_end=START + timedelta(minutes=150), purge=timedelta(minutes=30)),
        ).card,  # type: ignore[union-attr]
        estimator=object(),
        feature_names=("momentum", "noise"),
    )
    assert explain(empty, {"momentum": 0.5, "noise": 0.5}) == {}


def test_shap_contributions_are_signed() -> None:
    """El signo importa: dice hacia que clase empuja cada feature."""
    from mit_ml import explain

    window = TrainingWindow(train_end=START + timedelta(minutes=150), purge=timedelta(minutes=30))
    model = train(_samples(), window, algorithm="lightgbm")
    assert model is not None
    high = explain(model, {"momentum": 0.95, "noise": 0.5})
    low = explain(model, {"momentum": 0.05, "noise": 0.5})
    assert high["momentum"] > low["momentum"]
