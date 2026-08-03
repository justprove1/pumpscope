"""Ciclo de vida de una narrativa (SPEC.md 9).

Ocho estados, de NASCENT a EXHAUSTED, con REVIVING como retorno. Las transiciones son
DETERMINISTAS y salen de metricas medidas, no de la opinion del LLM: el modelo propone un
estado, pero quien lo fija es esta funcion.

Esa separacion importa. Si el estado lo decidiera el LLM, una narrativa podria "revivir"
porque el modelo se puso optimista, y de ahi cuelga el veto de elegibilidad
`narrative_exhausted`.
"""

from __future__ import annotations

from dataclasses import dataclass

from mit_data_models.enums import NarrativeState

# Estados desde los que NO se puede comprar: la fiesta ya paso (SPEC.md 12).
EXHAUSTED_STATES: frozenset[NarrativeState] = frozenset(
    {NarrativeState.SATURATED, NarrativeState.DECELERATING, NarrativeState.EXHAUSTED}
)


@dataclass(frozen=True, slots=True)
class NarrativeSignals:
    """Metricas observadas. Todas medidas, ninguna opinada."""

    mention_velocity: float = 0.0
    mention_acceleration: float = 0.0
    unique_author_growth: float = 0.0
    cross_platform_spread: float = 0.0
    spam_probability: float = 0.0
    age_minutes: float = 0.0
    peak_velocity: float = 0.0

    @property
    def decay_ratio(self) -> float:
        """Cuanto ha caido respecto a su pico. 0 = en maximos, 1 = muerta."""
        if self.peak_velocity <= 0:
            return 0.0
        return max(0.0, min(1.0, 1.0 - self.mention_velocity / self.peak_velocity))


def classify(signals: NarrativeSignals, previous: NarrativeState | None = None) -> NarrativeState:
    """Estado de la narrativa a partir de sus metricas.

    El orden de las comprobaciones es el orden de prioridad, y no es arbitrario: primero se
    descarta el spam, luego se mira si esta muriendo y solo despues si esta creciendo. Al
    reves, una narrativa fabricada por bots con mucha velocidad se clasificaria como VIRAL.
    """
    # El spam no es una narrativa, por mucho volumen que tenga.
    if signals.spam_probability >= 0.7:
        return NarrativeState.NASCENT

    decaying = signals.decay_ratio >= 0.5
    reviving = (
        previous in EXHAUSTED_STATES
        and signals.mention_acceleration > 0
        and signals.decay_ratio < 0.4
    )

    if reviving:
        return NarrativeState.REVIVING
    if signals.decay_ratio >= 0.85:
        return NarrativeState.EXHAUSTED
    if decaying:
        return NarrativeState.DECELERATING
    if signals.mention_velocity <= 0:
        return NarrativeState.NASCENT

    # Saturada: mucho volumen pero ya sin aceleracion ni autores nuevos. Es el techo, y
    # etiquetarla de alcista aqui seria el error caro.
    if (
        signals.mention_velocity > 0
        and signals.mention_acceleration <= 0
        and signals.unique_author_growth <= 0
    ):
        return NarrativeState.SATURATED

    if signals.mention_acceleration > 2.0 and signals.cross_platform_spread >= 0.5:
        return NarrativeState.VIRAL
    if signals.mention_acceleration > 0.5:
        return NarrativeState.ACCELERATING
    if signals.mention_velocity > 0:
        return NarrativeState.EMERGING
    return NarrativeState.NASCENT


def is_exhausted(state: NarrativeState) -> bool:
    """Si la narrativa esta agotada, el veto de SPEC.md 12 se activa."""
    return state in EXHAUSTED_STATES
