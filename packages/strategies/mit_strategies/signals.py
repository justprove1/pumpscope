"""Motor de senales (SPEC.md 13).

Una senal lleva TODOS los campos que exige SPEC.md 13, incluida la explicacion de por que se
abre o se cierra. Una senal sin explicacion no se puede auditar, y en la primera racha de
perdidas nadie sabra que estaba fallando.

**El importe no lo decide este modulo.** Lo calcula el `RiskEngine` y se copia aqui. La
separacion es de seguridad: quien detecta la oportunidad no puede decidir cuanto se arriesga.

`ADD_FORBIDDEN` es el estado por defecto de toda posicion abierta: nada de averaging down
automatico en esta version.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from mit_data_models.enums import SignalType

from mit_strategies.eligibility import EligibilityResult
from mit_strategies.scores import OpportunityBreakdown


@dataclass(frozen=True, slots=True)
class SignalThresholds:
    """Umbrales de decision. Sin calibrar: se ajustan con backtesting (Fase 3)."""

    watch: float = 40.0
    prepare: float = 55.0
    enter_small: float = 65.0
    enter: float = 80.0
    min_confidence: float = 0.5


@dataclass(frozen=True, slots=True)
class Signal:
    """Una senal con todos los campos de SPEC.md 13."""

    timestamp: datetime
    mint: str
    signal_type: SignalType
    score: float
    confidence: float
    top_features: dict[str, float] = field(default_factory=dict)
    risks: tuple[str, ...] = ()
    # Calculado por el RiskEngine, NO por este modulo.
    recommended_size_lamports: int = 0
    expected_price_lamports: int = 0
    expected_slippage_bps: int = 0
    invalidation: tuple[str, ...] = ()
    max_duration_seconds: int = 3600
    planned_exit: str = ""
    eligibility_vetoes: tuple[str, ...] = ()
    explanation: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "mint": self.mint,
            "signal_type": self.signal_type.value,
            "score": round(self.score, 2),
            "confidence": round(self.confidence, 4),
            "top_features": self.top_features,
            "risks": list(self.risks),
            "recommended_size_lamports": self.recommended_size_lamports,
            "expected_price_lamports": self.expected_price_lamports,
            "expected_slippage_bps": self.expected_slippage_bps,
            "invalidation": list(self.invalidation),
            "max_duration_seconds": self.max_duration_seconds,
            "planned_exit": self.planned_exit,
            "eligibility_vetoes": list(self.eligibility_vetoes),
            "explanation": self.explanation,
        }


def generate(
    *,
    timestamp: datetime,
    mint: str,
    breakdown: OpportunityBreakdown,
    eligibility: EligibilityResult,
    confidence: float,
    recommended_size_lamports: int = 0,
    thresholds: SignalThresholds | None = None,
) -> Signal:
    """Genera la senal de entrada. DETERMINISTA.

    Un veto de elegibilidad produce `IGNORE` **por alto que sea el score**: no hay puntuacion
    que compense un veto (SPEC.md 12).
    """
    t = thresholds or SignalThresholds()
    score = breakdown.opportunity
    vetoes = tuple(v.veto.value for v in eligibility.vetoes)
    reasons = tuple(v.reason for v in eligibility.vetoes)

    if not eligibility.eligible:
        return Signal(
            timestamp=timestamp,
            mint=mint,
            signal_type=SignalType.IGNORE,
            score=score,
            confidence=confidence,
            risks=reasons,
            eligibility_vetoes=vetoes,
            explanation=(
                f"IGNORE con score {score:.0f}: {len(vetoes)} veto(s) de elegibilidad. "
                f"Ninguna puntuacion compensa un veto."
            ),
        )

    if confidence < t.min_confidence:
        signal_type = SignalType.WATCH
    elif score >= t.enter:
        signal_type = SignalType.ENTER
    elif score >= t.enter_small:
        signal_type = SignalType.ENTER_SMALL
    elif score >= t.prepare:
        signal_type = SignalType.PREPARE
    elif score >= t.watch:
        signal_type = SignalType.WATCH
    else:
        signal_type = SignalType.IGNORE

    top = dict(sorted(breakdown.contributions.items(), key=lambda kv: -kv[1])[:3])
    drivers = ", ".join(f"{k} {v:.1f}" for k, v in top.items())
    size = recommended_size_lamports if signal_type in _ENTRY_SIGNALS else 0

    return Signal(
        timestamp=timestamp,
        mint=mint,
        signal_type=signal_type,
        score=score,
        confidence=confidence,
        top_features=top,
        risks=tuple(f"{k}: -{v:.1f}" for k, v in breakdown.penalties.items() if v > 0),
        recommended_size_lamports=size,
        invalidation=(
            "el score cae por debajo del umbral de entrada",
            "la narrativa pasa a agotada",
            "la liquidez de salida baja del minimo",
        ),
        planned_exit="parcial al +20%, trailing 25% sobre el maximo, time stop a 60 min",
        explanation=(
            f"{signal_type.value} con score {score:.0f} y confianza {confidence:.0%}. "
            f"Principales aportaciones: {drivers}."
        ),
    )


_ENTRY_SIGNALS = frozenset({SignalType.ENTER, SignalType.ENTER_SMALL})


def exit_signal(
    *, timestamp: datetime, mint: str, stop_name: str, reason: str, emergency: bool = False
) -> Signal:
    """Senal de salida a partir de un stop disparado."""
    return Signal(
        timestamp=timestamp,
        mint=mint,
        signal_type=SignalType.EMERGENCY_EXIT if emergency else SignalType.EXIT,
        score=0.0,
        confidence=1.0,
        risks=(reason,),
        explanation=f"Salida por stop {stop_name}: {reason}",
    )
