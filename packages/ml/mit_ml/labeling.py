"""Triple-barrier labeling (SPEC.md 19).

**No se predice "subira o bajara".** Esa pregunta no tiene respuesta accionable: no dice
cuanto, ni en cuanto tiempo, ni que pasa si primero baja. El triple-barrier si:

    barrera superior  +X%       -> etiqueta 1 (el objetivo se alcanzo primero)
    barrera inferior  -Y%       -> etiqueta 0 (el stop se alcanzo primero)
    barrera temporal   T        -> etiqueta segun donde quedo al expirar

El horizonte es EXPLICITO y forma parte de la etiqueta. Sin el, dos observaciones con el
mismo resultado pero distinta duracion se mezclan, y el modelo aprende ruido.

`resolved_at` se devuelve siempre porque es lo que permite purgar: una observacion cuya
etiqueta tarda 60 minutos en resolverse se solapa con las 60 siguientes, y sin purgarlas el
modelo ve el futuro.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class BarrierHit(StrEnum):
    UPPER = "upper"
    LOWER = "lower"
    TIME = "time"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True, slots=True)
class TripleBarrier:
    """Configuracion de las tres barreras."""

    upper_return: float = 0.20
    lower_return: float = 0.10
    horizon: timedelta = timedelta(minutes=60)

    def __post_init__(self) -> None:
        if self.upper_return <= 0 or self.lower_return <= 0:
            msg = "las barreras de precio deben ser positivas"
            raise ValueError(msg)
        if self.horizon <= timedelta(0):
            msg = "el horizonte debe ser positivo"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class PricePoint:
    at: datetime
    price: float


@dataclass(frozen=True, slots=True)
class Label:
    """Etiqueta con TODO lo necesario para purgar y para auditar."""

    observed_at: datetime
    label: int
    hit: BarrierHit
    # Cuando quedo resuelta. Es el dato que permite purgar el solapamiento.
    resolved_at: datetime
    realized_return: float
    max_favorable_excursion: float
    max_adverse_excursion: float

    @property
    def resolution_seconds(self) -> float:
        return (self.resolved_at - self.observed_at).total_seconds()


def label_observation(
    series: Sequence[PricePoint], index: int, barrier: TripleBarrier
) -> Label | None:
    """Etiqueta una observacion mirando SOLO hacia adelante desde ella.

    Devuelve `None` si la serie no alcanza a resolver la etiqueta: una observacion sin
    resolver NO se etiqueta con lo que haya al final. Eso seria inventar el resultado, y es
    la forma mas comun de meter sesgo de supervivencia sin darse cuenta.
    """
    if index >= len(series) - 1:
        return None
    entry = series[index]
    if entry.price <= 0:
        return None

    deadline = entry.at + barrier.horizon
    upper = entry.price * (1 + barrier.upper_return)
    lower = entry.price * (1 - barrier.lower_return)

    best = worst = 0.0
    for point in series[index + 1 :]:
        change = (point.price - entry.price) / entry.price
        best = max(best, change)
        worst = min(worst, change)

        if point.price >= upper:
            return Label(entry.at, 1, BarrierHit.UPPER, point.at, change, best, worst)
        if point.price <= lower:
            return Label(entry.at, 0, BarrierHit.LOWER, point.at, change, best, worst)
        if point.at >= deadline:
            return Label(
                entry.at, 1 if change > 0 else 0, BarrierHit.TIME, point.at, change, best, worst
            )

    # La serie se acaba sin resolver: NO se etiqueta.
    return None


def label_series(series: Sequence[PricePoint], barrier: TripleBarrier) -> list[Label]:
    """Etiqueta toda la serie, descartando las observaciones sin resolver."""
    ordered = sorted(series, key=lambda p: p.at)
    labels = [label_observation(ordered, i, barrier) for i in range(len(ordered))]
    return [label for label in labels if label is not None]


def max_resolution(labels: Sequence[Label]) -> timedelta:
    """Tiempo maximo de resolucion: es el minimo de purga que hay que aplicar."""
    if not labels:
        return timedelta(0)
    return timedelta(seconds=max(label.resolution_seconds for label in labels))
