"""Contratos sociales y narrativos (SPEC.md 4.F, 9)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from mit_data_models.enums import NarrativeState, SocialPlatform


class SocialPost(BaseModel):
    """Publicacion social normalizada.

    `author_account_age_days` y `bot_probability` no son adorno: el ratio de cuentas nuevas es
    la senal que separa atencion real de amplificacion artificial.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    platform: SocialPlatform
    external_id: str
    posted_at: datetime
    author_id: str | None = None
    author_followers: int | None = Field(default=None, ge=0)
    author_account_age_days: int | None = Field(default=None, ge=0)
    content_hash: str | None = None
    url: str | None = None
    lang: str | None = None
    country: str | None = None
    engagement: dict[str, int] = Field(default_factory=dict)
    sentiment: float | None = Field(default=None, ge=-1.0, le=1.0)
    bot_probability: float | None = Field(default=None, ge=0.0, le=1.0)
    entities: list[str] = Field(default_factory=list)
    mints: list[str] = Field(default_factory=list)


class NewsItem(BaseModel):
    """Noticia normalizada."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str
    title: str
    url: str
    published_at: datetime
    summary: str | None = None
    lang: str | None = None
    country: str | None = None
    entities: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    quality_score: float | None = Field(default=None, ge=0.0, le=100.0)


class NarrativeAssessment(BaseModel):
    """Salida del LLM del NarrativeEngine.

    **Este es el unico contrato por el que un LLM entra en el sistema, y es de solo lectura
    hacia el resto.** Alimenta `NarrativeScore` y nada mas: no toca importes, ni limites de
    riesgo, ni firma. `extra="forbid"` es deliberado — si el modelo devuelve un campo que no
    esta aqui, la respuesta se rechaza entera en vez de colarse.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    narrative: str = Field(min_length=1, max_length=256)
    state: NarrativeState
    score: float = Field(ge=0.0, le=100.0)
    confidence: float = Field(ge=0.0, le=1.0)
    # Razones con cifras concretas, no adjetivos.
    reasons: list[str] = Field(default_factory=list, max_length=10)
    entities: list[str] = Field(default_factory=list)
    # Modelo que la produjo: sin esto la decision no es reconstruible.
    llm_model: str | None = None


class NarrativeSummary(BaseModel):
    """Estado agregado de una narrativa (SPEC.md 9)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    slug: str
    label: str
    state: NarrativeState
    score: float | None = None
    confidence: float | None = None
    first_seen_at: datetime
    mention_velocity: float | None = None
    mention_acceleration: float | None = None
    unique_author_growth: float | None = None
    influencer_score: float | None = None
    news_quality_score: float | None = None
    cross_platform_spread: float | None = None
    spam_probability: float | None = Field(default=None, ge=0.0, le=1.0)
    half_life_minutes: int | None = None
