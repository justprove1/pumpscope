"""Particion temporal con purga y embargo (SPEC.md 18).

Separacion ESTRICTAMENTE temporal, nunca aleatoria. Un split aleatorio en series temporales
entrena con el futuro y produce backtests espectaculares e inservibles.

La purga no es un adorno: si una etiqueta necesita 60 minutos para resolverse, las
observaciones de los ultimos 60 minutos de `train` se solapan con `validation`. Sin purgarlas,
el modelo ve el resultado antes de predecirlo.

    |------- train -------|-- purga --|-- validation --|-- purga --|-- test --|
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta


class SplitError(ValueError):
    """La particion pedida no es posible con los datos disponibles."""


@dataclass(frozen=True, slots=True)
class TimeSplit:
    """Una particion temporal con sus fronteras explicitas."""

    train_start: datetime
    train_end: datetime
    validation_start: datetime
    validation_end: datetime
    test_start: datetime
    test_end: datetime

    def __post_init__(self) -> None:
        if not (
            self.train_start
            < self.train_end
            <= self.validation_start
            < self.validation_end
            <= self.test_start
            < self.test_end
        ):
            msg = "las particiones deben ser temporales, disjuntas y en orden"
            raise SplitError(msg)

    def select(self, items: Sequence[tuple[datetime, object]], part: str) -> list[object]:
        """Elementos de una particion. `part`: train | validation | test."""
        bounds = {
            "train": (self.train_start, self.train_end),
            "validation": (self.validation_start, self.validation_end),
            "test": (self.test_start, self.test_end),
        }
        if part not in bounds:
            msg = f"particion desconocida: {part}"
            raise SplitError(msg)
        start, end = bounds[part]
        return [value for at, value in items if start <= at < end]

    def as_dict(self) -> dict[str, str]:
        return {
            "train_start": self.train_start.isoformat(),
            "train_end": self.train_end.isoformat(),
            "validation_start": self.validation_start.isoformat(),
            "validation_end": self.validation_end.isoformat(),
            "test_start": self.test_start.isoformat(),
            "test_end": self.test_end.isoformat(),
        }


def walk_forward_splits(
    start: datetime,
    end: datetime,
    *,
    train: timedelta,
    validation: timedelta,
    test: timedelta,
    step: timedelta,
    purge: timedelta,
) -> list[TimeSplit]:
    """Genera ventanas walk-forward que avanzan en el tiempo.

    Un unico split no es evidencia: puede haber caido en un periodo favorable. Walk-forward
    obliga a que la estrategia funcione en varios regimenes distintos.

    `purge` se aplica entre particiones y debe ser >= al horizonte maximo de etiquetado.
    """
    if purge < timedelta(0):
        msg = "la purga no puede ser negativa"
        raise SplitError(msg)
    span = train + purge + validation + purge + test
    if end - start < span:
        msg = f"el rango disponible ({end - start}) no cabe una ventana completa ({span})"
        raise SplitError(msg)

    splits: list[TimeSplit] = []
    cursor = start
    while cursor + span <= end:
        train_end = cursor + train
        validation_start = train_end + purge
        validation_end = validation_start + validation
        test_start = validation_end + purge
        splits.append(
            TimeSplit(
                train_start=cursor,
                train_end=train_end,
                validation_start=validation_start,
                validation_end=validation_end,
                test_start=test_start,
                test_end=test_start + test,
            )
        )
        cursor += step
    return splits
