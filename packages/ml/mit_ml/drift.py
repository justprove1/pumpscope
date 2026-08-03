"""Deteccion de drift y desactivacion automatica (SPEC.md 19).

**Un modelo degradado se desactiva SOLO.** No espera a que alguien lo mire: el sistema cae al
modo heuristico y avisa. Es la unica postura defendible — un modelo que se equivoca de forma
sistematica y sigue puntuando es peor que no tener modelo, porque su salida parece
informacion.

Reactivarlo, en cambio, es SIEMPRE manual: lo mismo que con los kill switches.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum


class ModelStatus(StrEnum):
    TRAINING = "training"
    VALIDATED = "validated"
    ACTIVE = "active"
    DEGRADED = "degraded"
    RETIRED = "retired"


@dataclass(frozen=True, slots=True)
class DriftThresholds:
    """Cuanto puede empeorar un modelo antes de apagarse."""

    # Caida maxima de AUC respecto al entrenamiento.
    max_auc_drop: float = 0.10
    # Error de calibracion maximo: si dice 70% y ocurre el 40%, no sirve para dimensionar.
    max_calibration_error: float = 0.15
    # Desplazamiento maximo de la distribucion de predicciones.
    max_prediction_shift: float = 0.20
    min_samples: int = 50


@dataclass(frozen=True, slots=True)
class DriftReport:
    """Veredicto con TODAS las razones."""

    degraded: bool
    reasons: tuple[str, ...] = ()
    samples: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "degraded": self.degraded,
            "reasons": list(self.reasons),
            "samples": self.samples,
        }


def detect_drift(
    *,
    training_auc: float,
    recent_auc: float,
    calibration_error: float,
    training_mean_prediction: float,
    recent_mean_prediction: float,
    samples: int,
    thresholds: DriftThresholds | None = None,
) -> DriftReport:
    """Compara el comportamiento reciente con el del entrenamiento.

    Con pocas muestras NO se declara degradacion: apagar un modelo bueno por una racha corta
    es tan malo como dejar vivo uno malo.
    """
    t = thresholds or DriftThresholds()
    if samples < t.min_samples:
        return DriftReport(
            degraded=False,
            reasons=(
                f"solo {samples} muestras recientes, se exigen {t.min_samples}: "
                f"no hay base para declarar degradacion",
            ),
            samples=samples,
        )

    reasons: list[str] = []
    auc_drop = training_auc - recent_auc
    if auc_drop > t.max_auc_drop:
        reasons.append(
            f"AUC cayo de {training_auc:.3f} a {recent_auc:.3f} (-{auc_drop:.3f}), "
            f"maximo admitido {t.max_auc_drop:.3f}"
        )
    if calibration_error > t.max_calibration_error:
        reasons.append(
            f"error de calibracion {calibration_error:.3f} supera {t.max_calibration_error:.3f}: "
            f"las probabilidades ya no significan lo que dicen"
        )
    shift = abs(recent_mean_prediction - training_mean_prediction)
    if shift > t.max_prediction_shift:
        reasons.append(
            f"la prediccion media se desplazo {shift:.3f} "
            f"({training_mean_prediction:.3f} -> {recent_mean_prediction:.3f})"
        )
    return DriftReport(degraded=bool(reasons), reasons=tuple(reasons), samples=samples)


@dataclass
class ModelGuard:
    """Vigila un modelo y lo apaga solo si se degrada.

    `status` empieza en ACTIVE y solo un humano puede devolverlo ahi tras una degradacion.
    """

    name: str
    version: int
    status: ModelStatus = ModelStatus.ACTIVE
    disabled_at: datetime | None = None
    disabled_reason: str = ""

    @property
    def is_usable(self) -> bool:
        """Si no es usable, el sistema cae al modo heuristico."""
        return self.status is ModelStatus.ACTIVE

    def apply(self, report: DriftReport, now: datetime | None = None) -> bool:
        """Aplica un informe de drift. Devuelve `True` si acaba de desactivarse."""
        if report.degraded and self.status is ModelStatus.ACTIVE:
            self.status = ModelStatus.DEGRADED
            self.disabled_at = now or datetime.now(UTC)
            self.disabled_reason = "; ".join(report.reasons)
            return True
        return False

    def reactivate(self, operator: str) -> None:
        """Reactivacion MANUAL. Igual que un kill switch."""
        if not operator:
            msg = "reactivar un modelo degradado exige identificar al operador"
            raise ValueError(msg)
        self.status = ModelStatus.ACTIVE
        self.disabled_at = None
        self.disabled_reason = ""
