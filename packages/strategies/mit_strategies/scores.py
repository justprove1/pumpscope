"""Los 13 scores y el OpportunityScore (SPEC.md 11).

Modo HEURISTIC: pesos configurables y EXPLICABLES. No hay modelo aqui — eso es Fase 5.

Dos decisiones:

1. **Los pesos son datos, no constantes escondidas.** Van en `ScoreWeights`, se persisten con
   cada score calculado y se pueden discutir. Un peso que solo existe dentro de una formula
   no se puede auditar ni mejorar.

2. **Los riesgos RESTAN, no promedian.** Manipulacion y rug entran con signo negativo y sin
   diluirse: un token con rug alto no debe salvarse porque su narrativa sea buena. La media
   ponderada es justo el error que convierte una alarma en un matiz.

Un score alto NO autoriza comprar. La compra la deciden las reglas de elegibilidad
(`eligibility.py`) y el `RiskEngine`. Este modulo solo ordena candidatos.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields

SCORE_MIN = 0.0
SCORE_MAX = 100.0


def clamp(value: float) -> float:
    """Acota a 0-100. Todo score sale de aqui, asi que ninguno puede escaparse del rango."""
    return max(SCORE_MIN, min(SCORE_MAX, value))


@dataclass(frozen=True, slots=True)
class TokenScores:
    """Los 13 scores independientes de SPEC.md 11.

    Independientes a proposito: cada uno mide una cosa y se puede inspeccionar por separado.
    Colapsarlos antes de tiempo pierde la informacion que explica la decision.
    """

    narrative: float = 0.0
    momentum: float = 0.0
    liquidity: float = 0.0
    holder_quality: float = 0.0
    distribution: float = 0.0
    creator: float = 0.0
    whale: float = 0.0
    social_authenticity: float = 0.0
    exit_liquidity: float = 0.0
    manipulation_risk: float = 0.0
    rug_risk: float = 0.0
    execution_quality: float = 0.0
    data_confidence: float = 0.0

    def __post_init__(self) -> None:
        for f in fields(self):
            value = getattr(self, f.name)
            if not SCORE_MIN <= value <= SCORE_MAX:
                msg = f"{f.name}={value} fuera de rango 0-100"
                raise ValueError(msg)

    def as_dict(self) -> dict[str, float]:
        return {f.name: round(getattr(self, f.name), 2) for f in fields(self)}


@dataclass(frozen=True, slots=True)
class ScoreWeights:
    """Pesos del modo heuristico. Orientativos: SPEC.md 11 los da como punto de partida."""

    narrative: float = 0.18
    momentum: float = 0.15
    liquidity: float = 0.12
    holder_quality: float = 0.10
    distribution: float = 0.10
    creator: float = 0.08
    whale: float = 0.07
    social_authenticity: float = 0.08
    exit_liquidity: float = 0.07
    data_confidence: float = 0.05
    # Penalizaciones: RESTAN del resultado, no se promedian con lo demas.
    manipulation_penalty: float = 0.60
    rug_penalty: float = 0.80

    @property
    def positive_total(self) -> float:
        return (
            self.narrative
            + self.momentum
            + self.liquidity
            + self.holder_quality
            + self.distribution
            + self.creator
            + self.whale
            + self.social_authenticity
            + self.exit_liquidity
            + self.data_confidence
        )


@dataclass(frozen=True, slots=True)
class OpportunityBreakdown:
    """El score final CON su desglose. Sin desglose no es explicable."""

    opportunity: float
    contributions: dict[str, float] = field(default_factory=dict)
    penalties: dict[str, float] = field(default_factory=dict)
    weights: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "opportunity": round(self.opportunity, 2),
            "contributions": {k: round(v, 3) for k, v in self.contributions.items()},
            "penalties": {k: round(v, 3) for k, v in self.penalties.items()},
            "weights": self.weights,
        }


def opportunity_score(
    scores: TokenScores, weights: ScoreWeights | None = None
) -> OpportunityBreakdown:
    """OpportunityScore 0-100, con el desglose de cada aportacion.

    DETERMINISTA: mismos scores y pesos, mismo resultado. Sin azar y sin estado.
    """
    w = weights or ScoreWeights()

    contributions = {
        "narrative": scores.narrative * w.narrative,
        "momentum": scores.momentum * w.momentum,
        "liquidity": scores.liquidity * w.liquidity,
        "holder_quality": scores.holder_quality * w.holder_quality,
        "distribution": scores.distribution * w.distribution,
        "creator": scores.creator * w.creator,
        "whale": scores.whale * w.whale,
        "social_authenticity": scores.social_authenticity * w.social_authenticity,
        "exit_liquidity": scores.exit_liquidity * w.exit_liquidity,
        "data_confidence": scores.data_confidence * w.data_confidence,
    }
    positive = sum(contributions.values()) / max(1e-9, w.positive_total)

    penalties = {
        "manipulation": scores.manipulation_risk * w.manipulation_penalty,
        "rug": scores.rug_risk * w.rug_penalty,
    }
    return OpportunityBreakdown(
        opportunity=clamp(positive - sum(penalties.values())),
        contributions=contributions,
        penalties=penalties,
        weights={
            "narrative": w.narrative,
            "momentum": w.momentum,
            "liquidity": w.liquidity,
            "holder_quality": w.holder_quality,
            "distribution": w.distribution,
            "creator": w.creator,
            "whale": w.whale,
            "social_authenticity": w.social_authenticity,
            "exit_liquidity": w.exit_liquidity,
            "data_confidence": w.data_confidence,
            "manipulation_penalty": w.manipulation_penalty,
            "rug_penalty": w.rug_penalty,
        },
    )
