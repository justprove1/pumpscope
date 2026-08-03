"""Modelos tabulares con probabilidades calibradas (SPEC.md 19, 20).

Tres invariantes del paquete:

1. **El modelo NO decide importes.** Su salida es una probabilidad que entra como un score
   mas; el `RiskEngine` sigue siendo el unico que dimensiona, y su entrada esta cerrada.
2. **Anti-leakage estructural.** `TrainingWindow` filtra por timestamp Y por instante de
   resolucion de la etiqueta antes de entregar nada.
3. **Un modelo degradado se apaga solo** y el sistema cae al modo heuristico. Reactivarlo es
   manual.
"""

from __future__ import annotations

from mit_ml.drift import (
    DriftReport,
    DriftThresholds,
    ModelGuard,
    ModelStatus,
    detect_drift,
)
from mit_ml.labeling import (
    BarrierHit,
    Label,
    PricePoint,
    TripleBarrier,
    label_observation,
    label_series,
    max_resolution,
)
from mit_ml.registry import PromotionError, Stage, StrategyLab, StrategyVersion
from mit_ml.training import (
    ALGORITHMS,
    LeakageError,
    ModelCard,
    Sample,
    TrainedModel,
    TrainingWindow,
    calibration_error,
    explain,
    top_drivers,
    train,
)

__all__ = [
    "ALGORITHMS",
    "BarrierHit",
    "DriftReport",
    "DriftThresholds",
    "Label",
    "LeakageError",
    "ModelCard",
    "ModelGuard",
    "ModelStatus",
    "PricePoint",
    "PromotionError",
    "Sample",
    "Stage",
    "StrategyLab",
    "StrategyVersion",
    "TrainedModel",
    "TrainingWindow",
    "TripleBarrier",
    "calibration_error",
    "detect_drift",
    "explain",
    "label_observation",
    "label_series",
    "max_resolution",
    "top_drivers",
    "train",
]
