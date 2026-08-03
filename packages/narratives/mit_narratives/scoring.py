"""Sub-scores de narrativa (SPEC.md 9).

Funciones puras sobre publicaciones ya recogidas. La ingesta social vive detras de
`SocialProvider` y hoy no tiene adaptador, asi que estas funciones se prueban con datos
sinteticos y funcionaran igual el dia que lleguen datos reales.

**La regla que no se salta: una narrativa NO se confirma solo con publicaciones del creador**
(SPEC.md 9). Un creador hablando de su propio token no es una narrativa, es publicidad. Por
eso `authentic_authors` excluye al creador y a las wallets que el financio ANTES de medir
nada.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

MIN_INDEPENDENT_AUTHORS = 3


@dataclass(frozen=True, slots=True)
class Mention:
    """Una mencion social, ya normalizada."""

    author_id: str
    posted_at: datetime
    platform: str
    followers: int = 0
    author_age_days: int = 0
    is_creator_affiliated: bool = False
    bot_probability: float = 0.0


def authentic_mentions(mentions: Sequence[Mention]) -> list[Mention]:
    """Menciones que cuentan: ni del creador, ni de sus afiliados, ni de bots evidentes."""
    return [m for m in mentions if not m.is_creator_affiliated and m.bot_probability < 0.7]


def is_creator_only(mentions: Sequence[Mention]) -> bool:
    """True si toda la conversacion viene del creador o de su circulo.

    Cuando es True, la narrativa NO puede confirmarse por muchas menciones que haya.
    """
    authentic = authentic_mentions(mentions)
    return len({m.author_id for m in authentic}) < MIN_INDEPENDENT_AUTHORS


def mention_velocity(mentions: Sequence[Mention], window: timedelta) -> float:
    """Menciones autenticas por minuto en la ventana."""
    authentic = authentic_mentions(mentions)
    minutes = window.total_seconds() / 60
    return len(authentic) / minutes if minutes > 0 else 0.0


def mention_acceleration(mentions: Sequence[Mention], as_of: datetime, window: timedelta) -> float:
    """Cambio de velocidad entre la ventana actual y la anterior.

    Es la senal que separa "tiene menciones" de "esta despegando". Sin ella, una narrativa
    vieja con volumen constante parece tan interesante como una que arranca ahora.
    """
    current_start = as_of - window
    previous_start = current_start - window
    authentic = authentic_mentions(mentions)

    current = sum(1 for m in authentic if current_start < m.posted_at <= as_of)
    previous = sum(1 for m in authentic if previous_start < m.posted_at <= current_start)
    if previous == 0:
        return float(current)
    return (current - previous) / previous


def unique_author_growth(mentions: Sequence[Mention], as_of: datetime, window: timedelta) -> float:
    """Autores nuevos frente a los de la ventana anterior."""
    current_start = as_of - window
    authentic = authentic_mentions(mentions)
    before = {m.author_id for m in authentic if m.posted_at <= current_start}
    now = {m.author_id for m in authentic if current_start < m.posted_at <= as_of}
    fresh = now - before
    return len(fresh) / len(now) if now else 0.0


def cross_platform_spread(mentions: Sequence[Mention]) -> float:
    """Fraccion de plataformas distintas donde aparece, sobre las que se vigilan.

    Una narrativa real salta de plataforma. Una fabricada suele vivir en una sola.
    """
    authentic = authentic_mentions(mentions)
    if not authentic:
        return 0.0
    platforms = {m.platform for m in authentic}
    return min(1.0, len(platforms) / 4.0)


def spam_probability(mentions: Sequence[Mention]) -> float:
    """Proporcion de menciones que parecen fabricadas.

    Cuentas nuevas y bots. El ratio de cuentas recien creadas es la senal que mejor separa
    atencion real de amplificacion pagada.
    """
    if not mentions:
        return 0.0
    suspicious = sum(1 for m in mentions if m.bot_probability >= 0.7 or m.author_age_days < 7)
    return min(1.0, suspicious / len(mentions))


def influencer_score(mentions: Sequence[Mention]) -> float:
    """0-100 segun el alcance de los autores autenticos.

    Escala logaritmica: la diferencia entre 100 y 1.000 seguidores importa mas que entre
    1.000.000 y 1.010.000.
    """
    import math

    authentic = authentic_mentions(mentions)
    if not authentic:
        return 0.0
    reach = max(m.followers for m in authentic)
    if reach <= 0:
        return 0.0
    return min(100.0, math.log10(reach + 1) * 100 / 7)


def narrative_score(
    mentions: Sequence[Mention], as_of: datetime, window: timedelta = timedelta(minutes=20)
) -> float:
    """NarrativeScore 0-100.

    Devuelve 0 si la conversacion es solo del creador, por alto que salga todo lo demas.
    Es un veto, no un factor: multiplicar por un peso pequeno dejaria pasar el caso.
    """
    if is_creator_only(mentions):
        return 0.0

    velocity = min(1.0, mention_velocity(mentions, window) / 5.0)
    acceleration = max(0.0, min(1.0, mention_acceleration(mentions, as_of, window) / 3.0))
    authors = unique_author_growth(mentions, as_of, window)
    spread = cross_platform_spread(mentions)
    authenticity = 1.0 - spam_probability(mentions)

    raw = (
        0.25 * velocity
        + 0.30 * acceleration
        + 0.20 * authors
        + 0.15 * spread
        + 0.10 * (influencer_score(mentions) / 100)
    )
    return max(0.0, min(100.0, raw * authenticity * 100))
