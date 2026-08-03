"""Reglas de elegibilidad: los 17 vetos duros (SPEC.md 12).

**Un veto no se compensa con puntuacion.** Un OpportunityScore de 100 no anula un RugRisk por
encima del umbral. Por eso esto no es un factor mas del score: es una puerta que se cierra.

Cada veto activado se registra con su VALOR y su UMBRAL, para que la decision se pueda
reconstruir despues y para poder discutir el umbral con datos en vez de con impresiones.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mit_data_models.enums import EligibilityVeto


@dataclass(frozen=True, slots=True)
class EligibilityThresholds:
    """Umbrales de RISK_POLICY.md. Sin calibrar todavia: son un punto de partida."""

    min_data_confidence: float = 60.0
    max_rug_risk: float = 35.0
    max_manipulation_risk: float = 40.0
    min_liquidity_lamports: int = 5_000_000_000
    max_price_impact_bps: int = 300
    max_top10_pct_adjusted: float = 35.0
    max_cluster_pct: float = 25.0
    max_pump_pct: float = 300.0
    max_spread_bps: int = 500
    max_data_age_ms: int = 3_000
    max_source_divergence_pct: float = 5.0


@dataclass(frozen=True, slots=True)
class EligibilityInputs:
    """Todo lo que miran los vetos. Cerrado: no entra nada de un modelo generativo."""

    data_confidence: float = 100.0
    rug_risk: float = 0.0
    manipulation_risk: float = 0.0
    liquidity_lamports: int = 10_000_000_000
    has_verified_exit_route: bool = True
    sell_simulation_succeeded: bool = True
    price_impact_bps: int = 0
    creator_history_critical: bool = False
    top10_pct_adjusted: float = 0.0
    dominant_cluster_pct: float = 0.0
    pump_pct: float = 0.0
    narrative_exhausted: bool = False
    spread_bps: int = 0
    data_age_ms: int = 0
    source_divergence_pct: float = 0.0
    sufficient_sol: bool = True
    daily_risk_limit_reached: bool = False


@dataclass(frozen=True, slots=True)
class VetoRecord:
    """Un veto activado, con su cifra y su umbral."""

    veto: EligibilityVeto
    reason: str
    value: float | str
    threshold: float | str


@dataclass(frozen=True, slots=True)
class EligibilityResult:
    eligible: bool
    vetoes: tuple[VetoRecord, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, object]:
        return {
            "eligible": self.eligible,
            "vetoes": [
                {
                    "veto": v.veto.value,
                    "reason": v.reason,
                    "value": v.value,
                    "threshold": v.threshold,
                }
                for v in self.vetoes
            ],
        }


def evaluate(
    inputs: EligibilityInputs, thresholds: EligibilityThresholds | None = None
) -> EligibilityResult:
    """Aplica los 17 vetos. Devuelve TODOS los que fallan, no solo el primero.

    Devolver todos importa: si se para en el primero, arreglar un problema destapa el
    siguiente y el operador cree que iba mejorando.
    """
    t = thresholds or EligibilityThresholds()
    vetoes: list[VetoRecord] = []

    def veto(kind: EligibilityVeto, reason: str, value: float | str, limit: float | str) -> None:
        vetoes.append(VetoRecord(veto=kind, reason=reason, value=value, threshold=limit))

    if inputs.data_confidence < t.min_data_confidence:
        veto(
            EligibilityVeto.LOW_DATA_CONFIDENCE,
            f"confianza en los datos {inputs.data_confidence:.0f} < {t.min_data_confidence:.0f}",
            inputs.data_confidence,
            t.min_data_confidence,
        )
    if inputs.rug_risk > t.max_rug_risk:
        veto(
            EligibilityVeto.HIGH_RUG_RISK,
            f"RugRiskScore {inputs.rug_risk:.0f} > {t.max_rug_risk:.0f}",
            inputs.rug_risk,
            t.max_rug_risk,
        )
    if inputs.manipulation_risk > t.max_manipulation_risk:
        veto(
            EligibilityVeto.HIGH_MANIPULATION_RISK,
            f"ManipulationRiskScore {inputs.manipulation_risk:.0f} > {t.max_manipulation_risk:.0f}",
            inputs.manipulation_risk,
            t.max_manipulation_risk,
        )
    if inputs.liquidity_lamports < t.min_liquidity_lamports:
        veto(
            EligibilityVeto.INSUFFICIENT_LIQUIDITY,
            f"liquidez {inputs.liquidity_lamports / 1e9:.3f} SOL < "
            f"{t.min_liquidity_lamports / 1e9:.3f} SOL",
            inputs.liquidity_lamports,
            t.min_liquidity_lamports,
        )
    if not inputs.has_verified_exit_route:
        veto(EligibilityVeto.NO_EXIT_ROUTE, "no hay ruta de salida verificable", False, True)
    if not inputs.sell_simulation_succeeded:
        veto(
            EligibilityVeto.SELL_SIMULATION_FAILED,
            "la simulacion de venta fallo: posible honeypot",
            False,
            True,
        )
    if inputs.price_impact_bps > t.max_price_impact_bps:
        veto(
            EligibilityVeto.PRICE_IMPACT_TOO_HIGH,
            f"impacto {inputs.price_impact_bps} bps > {t.max_price_impact_bps} bps",
            inputs.price_impact_bps,
            t.max_price_impact_bps,
        )
    if inputs.creator_history_critical:
        veto(
            EligibilityVeto.CREATOR_HISTORY_CRITICAL,
            "el creador tiene historial critico",
            True,
            False,
        )
    if inputs.top10_pct_adjusted > t.max_top10_pct_adjusted:
        veto(
            EligibilityVeto.HOLDER_CONCENTRATION,
            f"top 10 ajustado {inputs.top10_pct_adjusted:.0f}% > {t.max_top10_pct_adjusted:.0f}%",
            inputs.top10_pct_adjusted,
            t.max_top10_pct_adjusted,
        )
    if inputs.dominant_cluster_pct > t.max_cluster_pct:
        veto(
            EligibilityVeto.DANGEROUS_CLUSTER,
            f"cluster dominante {inputs.dominant_cluster_pct:.0f}% > {t.max_cluster_pct:.0f}%",
            inputs.dominant_cluster_pct,
            t.max_cluster_pct,
        )
    if inputs.pump_pct > t.max_pump_pct:
        veto(
            EligibilityVeto.ALREADY_PUMPED,
            f"ya subio {inputs.pump_pct:.0f}% > {t.max_pump_pct:.0f}%",
            inputs.pump_pct,
            t.max_pump_pct,
        )
    if inputs.narrative_exhausted:
        veto(EligibilityVeto.NARRATIVE_EXHAUSTED, "la narrativa esta agotada", True, False)
    if inputs.spread_bps > t.max_spread_bps:
        veto(
            EligibilityVeto.EXCESSIVE_SPREAD,
            f"spread {inputs.spread_bps} bps > {t.max_spread_bps} bps",
            inputs.spread_bps,
            t.max_spread_bps,
        )
    if inputs.data_age_ms > t.max_data_age_ms:
        veto(
            EligibilityVeto.STALE_DATA,
            f"datos de hace {inputs.data_age_ms} ms > {t.max_data_age_ms} ms",
            inputs.data_age_ms,
            t.max_data_age_ms,
        )
    if inputs.source_divergence_pct > t.max_source_divergence_pct:
        veto(
            EligibilityVeto.SOURCE_DIVERGENCE,
            f"divergencia entre fuentes {inputs.source_divergence_pct:.1f}% > "
            f"{t.max_source_divergence_pct:.1f}%",
            inputs.source_divergence_pct,
            t.max_source_divergence_pct,
        )
    if not inputs.sufficient_sol:
        veto(
            EligibilityVeto.INSUFFICIENT_SOL,
            "SOL insuficiente para compra, fees y salida",
            False,
            True,
        )
    if inputs.daily_risk_limit_reached:
        veto(
            EligibilityVeto.DAILY_RISK_LIMIT_REACHED,
            "limite diario de riesgo alcanzado",
            True,
            False,
        )

    return EligibilityResult(eligible=not vetoes, vetoes=tuple(vetoes))
