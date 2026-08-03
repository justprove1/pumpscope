"""Feature engineering por ventanas, con anti-leakage estructural (SPEC.md 10).

**El data leakage no se revisa: se hace imposible.**

Revisar a mano que ninguna feature mira al futuro no escala y falla justo cuando el codigo
crece. Aqui la defensa es de diseno: `FeatureWindow.compute()` recibe las observaciones y
DESCARTA por si misma todo lo que tenga timestamp posterior al momento de prediccion, antes
de que la funcion de la feature vea un solo dato. Una feature no puede hacer trampa porque
nunca llega a tener acceso al futuro.

Ademas se registra `lookback_start_at`, que se persiste en la tabla `features` con un CHECK
en la base de datos. Tres capas: el filtro, la columna y la restriccion.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Final

# Ventanas exigidas por SPEC.md 10.
WINDOW_SECONDS: Final[dict[str, int]] = {
    "5s": 5,
    "15s": 15,
    "30s": 30,
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
}


class LeakageError(AssertionError):
    """Se ha intentado usar informacion posterior al instante de prediccion.

    Es `AssertionError` y no un error de dominio a proposito: significa que hay un bug en el
    codigo de features, no un dato malo. Nunca se captura para continuar.
    """


@dataclass(frozen=True, slots=True)
class Observation[T]:
    """Un dato con su instante. El instante es obligatorio: sin el no hay anti-leakage."""

    at: datetime
    value: T


@dataclass(frozen=True, slots=True)
class WindowResult:
    """Resultado de una ventana, con su frontera temporal declarada."""

    window: str
    as_of: datetime
    lookback_start_at: datetime
    sample_count: int
    values: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.lookback_start_at > self.as_of:
            msg = f"ventana invalida: {self.lookback_start_at} > {self.as_of}"
            raise LeakageError(msg)


def window_bounds(window: str, as_of: datetime) -> tuple[datetime, datetime]:
    """Limites `[inicio, as_of]` de una ventana.

    El limite superior es `as_of` INCLUSIVE y nunca posterior: ese es todo el truco.
    """
    if window not in WINDOW_SECONDS:
        msg = f"ventana desconocida: {window}. Validas: {sorted(WINDOW_SECONDS)}"
        raise ValueError(msg)
    return as_of - timedelta(seconds=WINDOW_SECONDS[window]), as_of


def select_visible[T](
    observations: Sequence[Observation[T]], window: str, as_of: datetime
) -> list[Observation[T]]:
    """Observaciones utilizables: dentro de la ventana y NO posteriores a `as_of`.

    Es el unico punto por el que los datos entran a una feature. Si algo del futuro llegara
    a una feature, tendria que ser saltandose esta funcion.
    """
    start, end = window_bounds(window, as_of)
    return [o for o in observations if start <= o.at <= end]


FeatureFn = Callable[[Sequence[Observation[float]]], float]


class WindowedFeatures:
    """Calcula un conjunto de features sobre varias ventanas, sin leakage posible."""

    def __init__(self, features: dict[str, FeatureFn]) -> None:
        if not features:
            msg = "hay que registrar al menos una feature"
            raise ValueError(msg)
        self._features = dict(features)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._features))

    def compute(
        self,
        observations: Sequence[Observation[float]],
        window: str,
        as_of: datetime,
    ) -> WindowResult:
        """Calcula todas las features de una ventana en el instante `as_of`."""
        visible = select_visible(observations, window, as_of)

        # Defensa redundante a proposito: si `select_visible` cambiara y dejara pasar algo
        # del futuro, esto lo convierte en un fallo ruidoso y no en un backtest optimista.
        for observation in visible:
            if observation.at > as_of:
                msg = f"observacion futura en la ventana: {observation.at} > {as_of}"
                raise LeakageError(msg)

        start, _ = window_bounds(window, as_of)
        return WindowResult(
            window=window,
            as_of=as_of,
            lookback_start_at=start,
            sample_count=len(visible),
            values={name: fn(visible) for name, fn in sorted(self._features.items())},
        )

    def compute_all(
        self, observations: Sequence[Observation[float]], as_of: datetime
    ) -> dict[str, WindowResult]:
        """Calcula todas las ventanas de SPEC.md 10 en el mismo instante."""
        return {w: self.compute(observations, w, as_of) for w in WINDOW_SECONDS}


# --- Features basicas ----------------------------------------------------------------------
# Deliberadamente simples: son las primitivas sobre las que la Fase 4 construira los scores.
# Todas reciben solo observaciones ya filtradas, asi que no pueden mirar al futuro.


def count(observations: Sequence[Observation[float]]) -> float:
    return float(len(observations))


def total(observations: Sequence[Observation[float]]) -> float:
    return float(sum(o.value for o in observations))


def mean(observations: Sequence[Observation[float]]) -> float:
    return total(observations) / len(observations) if observations else 0.0


def maximum(observations: Sequence[Observation[float]]) -> float:
    return max((o.value for o in observations), default=0.0)


def last(observations: Sequence[Observation[float]]) -> float:
    """Ultimo valor de la ventana. Ordena por instante: el orden de entrada no se asume."""
    if not observations:
        return 0.0
    return max(observations, key=lambda o: o.at).value


def velocity(observations: Sequence[Observation[float]]) -> float:
    """Observaciones por segundo dentro de la ventana observada."""
    if len(observations) < 2:
        return 0.0
    instants = sorted(o.at for o in observations)
    span = (instants[-1] - instants[0]).total_seconds()
    return len(observations) / span if span > 0 else 0.0
