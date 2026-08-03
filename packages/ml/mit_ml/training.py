"""Pipeline de entrenamiento walk-forward con anti-leakage estructural (SPEC.md 19).

**El leakage no se revisa: se hace imposible.** `TrainingWindow` filtra por timestamp antes de
entregar los datos, igual que hace `mit_features.windows`. Un modelo no puede entrenarse con
el futuro porque nunca lo recibe.

Se empieza por baselines a proposito (SPEC.md 19): regresion logistica y Random Forest antes
que LightGBM. Si el baseline ya no distingue, el gradiente boosting solo va a sobreajustar
mejor.

Las probabilidades se CALIBRAN siempre. Un modelo con buen AUC y mala calibracion es inutil
para dimensionar: si dice 70% y ocurre el 40%, el sizing esta mal por construccion.
"""

from __future__ import annotations

import itertools
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import numpy as np
from lightgbm import LGBMClassifier
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score

LOGGER = logging.getLogger("mit.ml.training")


class LeakageError(AssertionError):
    """Se intento entrenar con datos posteriores al corte. Nunca se captura para seguir."""


@dataclass(frozen=True, slots=True)
class Sample:
    """Una observacion etiquetada, con los dos instantes que importan."""

    at: datetime
    features: dict[str, float]
    label: int
    # Cuando quedo resuelta la etiqueta: sin esto no se puede purgar.
    resolved_at: datetime


@dataclass(frozen=True, slots=True)
class TrainingWindow:
    """Ventana de entrenamiento con purga y embargo explicitos."""

    train_end: datetime
    purge: timedelta

    def select_train(self, samples: Sequence[Sample]) -> list[Sample]:
        """Muestras utilizables para entrenar.

        Se exige que la etiqueta se resolviera ANTES del corte menos la purga. No basta con
        que la observacion sea anterior: si su etiqueta se resuelve despues del corte, la
        muestra contiene informacion del periodo de validacion.
        """
        limit = self.train_end - self.purge
        return [s for s in samples if s.at <= limit and s.resolved_at <= limit]

    def select_validation(self, samples: Sequence[Sample]) -> list[Sample]:
        return [s for s in samples if s.at > self.train_end]


@dataclass(frozen=True, slots=True)
class ModelCard:
    """Ficha del modelo (SPEC.md 19).

    Un numero sin ficha no es utilizable: no se sabe cuando se entreno, con cuantos datos ni
    si sus probabilidades significan algo.
    """

    name: str
    algorithm: str
    trained_at: datetime
    train_samples: int
    validation_samples: int
    features: tuple[str, ...]
    auc: float
    brier: float
    calibration_error: float
    mean_prediction: float
    purge_seconds: float

    @property
    def is_usable(self) -> bool:
        """Un modelo que no supera el azar no se usa, por buena que sea su calibracion."""
        return self.auc > 0.55 and self.calibration_error < 0.15

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "algorithm": self.algorithm,
            "trained_at": self.trained_at.isoformat(),
            "train_samples": self.train_samples,
            "validation_samples": self.validation_samples,
            "features": list(self.features),
            "auc": round(self.auc, 4),
            "brier": round(self.brier, 4),
            "calibration_error": round(self.calibration_error, 4),
            "mean_prediction": round(self.mean_prediction, 4),
            "purge_seconds": self.purge_seconds,
            "usable": self.is_usable,
        }


@dataclass
class TrainedModel:
    """Modelo entrenado con su ficha. La ficha va SIEMPRE con el modelo."""

    card: ModelCard
    estimator: Any
    feature_names: tuple[str, ...] = field(default_factory=tuple)

    def predict_proba(self, features: dict[str, float]) -> float:
        """Probabilidad calibrada de la clase positiva."""
        row = np.array([[features.get(name, 0.0) for name in self.feature_names]])
        return float(self.estimator.predict_proba(row)[0][1])


def calibration_error(y_true: np.ndarray, y_prob: np.ndarray, bins: int = 10) -> float:
    """Error de calibracion esperado: |frecuencia observada - probabilidad predicha|.

    Es la metrica que decide si una probabilidad se puede usar para dimensionar. El AUC mide
    si el orden es bueno; esto mide si el NUMERO significa lo que dice.
    """
    if len(y_true) == 0:
        return 0.0
    edges = np.linspace(0.0, 1.0, bins + 1)
    total = 0.0
    for low, high in itertools.pairwise(edges):
        mask = (y_prob >= low) & (y_prob < high)
        if not mask.any():
            continue
        total += mask.mean() * abs(y_true[mask].mean() - y_prob[mask].mean())
    return float(total)


# El orden NO es casual (SPEC.md 19): se empieza por el baseline. Si la regresion logistica
# ya no distingue, el gradiente boosting solo va a sobreajustar mejor, y con mas conviccion.
#
# `random_state` fijo en los tres: un modelo que no se puede reentrenar identico no se puede
# auditar ni comparar entre versiones.
ALGORITHMS: dict[str, Any] = {
    "logistic": lambda: LogisticRegression(max_iter=1000, random_state=0),
    "random_forest": lambda: RandomForestClassifier(n_estimators=100, random_state=0),
    "lightgbm": lambda: LGBMClassifier(
        n_estimators=200,
        learning_rate=0.05,
        num_leaves=15,
        # Regularizacion deliberadamente alta: los corpus de memecoins son pequenos y
        # ruidosos, y LightGBM memoriza ruido con una facilidad notable.
        min_child_samples=20,
        subsample=0.8,
        subsample_freq=1,
        colsample_bytree=0.8,
        reg_lambda=1.0,
        random_state=0,
        verbosity=-1,
        n_jobs=1,
    ),
}


def train(
    samples: Sequence[Sample],
    window: TrainingWindow,
    *,
    algorithm: str = "logistic",
    name: str = "baseline",
) -> TrainedModel | None:
    """Entrena y calibra. Devuelve `None` si no hay datos suficientes.

    Devolver `None` en vez de un modelo malo es deliberado: un modelo entrenado con veinte
    muestras produce metricas con formato y sin significado, y alguien las usara.
    """
    train_set = window.select_train(samples)
    validation_set = window.select_validation(samples)

    # Defensa redundante: si `select_train` cambiara, esto lo convierte en fallo ruidoso.
    limit = window.train_end - window.purge
    for sample in train_set:
        if sample.resolved_at > limit:
            msg = f"LEAKAGE: muestra resuelta en {sample.resolved_at} entrenando hasta {limit}"
            raise LeakageError(msg)

    if len(train_set) < 30 or len(validation_set) < 10:
        return None
    labels = {s.label for s in train_set}
    if len(labels) < 2:
        return None

    feature_names = tuple(sorted(train_set[0].features))
    x_train = np.array([[s.features.get(f, 0.0) for f in feature_names] for s in train_set])
    y_train = np.array([s.label for s in train_set])
    x_val = np.array([[s.features.get(f, 0.0) for f in feature_names] for s in validation_set])
    y_val = np.array([s.label for s in validation_set])

    base = ALGORITHMS.get(algorithm, ALGORITHMS["logistic"])()
    # Calibracion isotonica sobre validacion cruzada: sin esto las probabilidades no son
    # comparables entre modelos ni utilizables para dimensionar.
    estimator = CalibratedClassifierCV(base, method="sigmoid", cv=3)
    estimator.fit(x_train, y_train)

    probabilities = estimator.predict_proba(x_val)[:, 1]
    auc = float(roc_auc_score(y_val, probabilities)) if len(set(y_val)) > 1 else 0.5

    card = ModelCard(
        name=name,
        algorithm=algorithm,
        trained_at=window.train_end,
        train_samples=len(train_set),
        validation_samples=len(validation_set),
        features=feature_names,
        auc=auc,
        brier=float(brier_score_loss(y_val, probabilities)),
        calibration_error=calibration_error(y_val, probabilities),
        mean_prediction=float(probabilities.mean()),
        purge_seconds=window.purge.total_seconds(),
    )
    return TrainedModel(card=card, estimator=estimator, feature_names=feature_names)


def _base_estimators(model: TrainedModel) -> list[Any]:
    """Estimadores base que hay dentro del envoltorio de calibracion.

    `CalibratedClassifierCV` guarda un estimador por pliegue. SHAP necesita el modelo de
    dentro, no el calibrador: el calibrador solo reescala la salida y no tiene estructura
    que explicar.
    """
    calibrated = getattr(model.estimator, "calibrated_classifiers_", [])
    return [c.estimator for c in calibrated if hasattr(c, "estimator")]


def explain(
    model: TrainedModel,
    features: dict[str, float],
    background: Sequence[dict[str, float]] | None = None,
) -> dict[str, float]:
    """Aportacion SHAP de cada feature a ESTA prediccion (SPEC.md 19).

    Devuelve un valor por feature: positivo empuja hacia la clase 1, negativo hacia la 0. Es
    lo que convierte "probabilidad 0,73" en algo discutible.

    Se promedian los estimadores de los pliegues de calibracion, porque cada uno vio datos
    distintos y quedarse con uno seria arbitrario.

    Si SHAP no puede explicar el modelo, devuelve un diccionario VACIO en vez de inventarse
    numeros: una explicacion falsa es peor que ninguna, porque se cree.
    """
    import shap

    estimators = _base_estimators(model)
    if not estimators:
        return {}

    row = np.array([[features.get(name, 0.0) for name in model.feature_names]])
    background_matrix = (
        np.array([[b.get(name, 0.0) for name in model.feature_names] for b in background])
        if background
        else None
    )

    totals = np.zeros(len(model.feature_names))
    used = 0
    for estimator in estimators:
        try:
            if (
                hasattr(estimator, "tree_")
                or hasattr(estimator, "estimators_")
                or hasattr(estimator, "booster_")
            ):
                explainer = shap.TreeExplainer(estimator)
                values = explainer.shap_values(row)
            elif background_matrix is not None:
                explainer = shap.LinearExplainer(estimator, background_matrix)
                values = explainer.shap_values(row)
            else:
                continue
        except Exception:
            # SHAP no soporta todas las combinaciones de modelo y version. Se anota y se
            # sigue con el resto de pliegues: es preferible una explicacion parcial a
            # ninguna, y ninguna a una inventada.
            LOGGER.debug("SHAP no pudo explicar un estimador", exc_info=True)
            continue

        array = np.asarray(values)
        # Segun modelo y version, SHAP devuelve (1, n), (1, n, 2) o una lista por clase.
        if array.ndim == 3:
            array = array[..., -1]
        elif array.ndim == 1:
            array = array.reshape(1, -1)
        if array.shape[-1] != len(model.feature_names):
            continue
        totals += array[0]
        used += 1

    if used == 0:
        return {}
    return {
        name: round(float(value / used), 6)
        for name, value in zip(model.feature_names, totals, strict=True)
    }


def top_drivers(contributions: dict[str, float], limit: int = 3) -> list[tuple[str, float]]:
    """Las features que mas pesaron, por magnitud absoluta y con su signo."""
    return sorted(contributions.items(), key=lambda kv: -abs(kv[1]))[:limit]
