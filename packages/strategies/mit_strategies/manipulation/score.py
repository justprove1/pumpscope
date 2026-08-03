"""ManipulationRiskScore 0-100 (SPEC.md 8).

**Determinista y explicable.** Mismo contexto, mismo score, siempre — hay un property test que
lo verifica. Y nunca un numero suelto: el informe lleva TODAS las razones con sus cifras.

La agregacion es una suma de puntos por severidad, acotada a 100. No se usa una media: un
token con un unico hallazgo critico no debe quedar diluido por diez comprobaciones limpias.
En riesgo, lo peor manda; no el promedio.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from mit_strategies.manipulation.coordination import (
    detect_fresh_wallet_cohort,
    detect_identical_amounts,
    detect_same_slot_bundles,
)
from mit_strategies.manipulation.sybil import (
    detect_common_funding,
    detect_creator_funded_buyers,
)
from mit_strategies.manipulation.token_integrity import (
    detect_impersonation,
    detect_metadata_anomalies,
    detect_supply_concentration,
)
from mit_strategies.manipulation.trading import (
    detect_concentrated_volume,
    detect_creator_dumping,
    detect_creator_history,
    detect_self_trading,
)
from mit_strategies.manipulation.types import Finding, Severity, TokenContext

Detector = Callable[[TokenContext], list[Finding]]

# El orden es fijo para que el informe sea reproducible byte a byte.
DETECTORS: tuple[Detector, ...] = (
    detect_same_slot_bundles,
    detect_identical_amounts,
    detect_fresh_wallet_cohort,
    detect_common_funding,
    detect_creator_funded_buyers,
    detect_self_trading,
    detect_concentrated_volume,
    detect_creator_dumping,
    detect_creator_history,
    detect_impersonation,
    detect_metadata_anomalies,
    detect_supply_concentration,
)

MAX_SCORE = 100


@dataclass(frozen=True, slots=True)
class ManipulationReport:
    """Score con todas sus razones. El score sin razones no es auditable."""

    mint: str
    score: int
    findings: tuple[Finding, ...] = ()
    # Numero de detectores que corrieron: distinguir "limpio" de "no comprobado" importa.
    detectors_run: int = 0

    @property
    def reasons(self) -> tuple[str, ...]:
        return tuple(f.reason for f in self.findings)

    @property
    def worst_severity(self) -> Severity | None:
        if not self.findings:
            return None
        order = list(Severity)
        return max((f.severity for f in self.findings), key=order.index)

    def as_dict(self) -> dict[str, Any]:
        return {
            "mint": self.mint,
            "score": self.score,
            "detectors_run": self.detectors_run,
            "worst_severity": self.worst_severity.value if self.worst_severity else None,
            "findings": [
                {
                    "detector": f.detector,
                    "severity": f.severity.value,
                    "reason": f.reason,
                    "evidence": f.evidence,
                }
                for f in self.findings
            ],
        }


def _sort_key(finding: Finding) -> tuple[int, str, str]:
    order = list(Severity)
    return (-order.index(finding.severity), finding.detector, finding.reason)


def analyze(context: TokenContext, detectors: Sequence[Detector] = DETECTORS) -> ManipulationReport:
    """Ejecuta todos los detectores y agrega el resultado.

    Si un detector falla, se deja constancia y los demas siguen: perder una comprobacion es
    malo, perder las doce por un bug en una es peor.
    """
    findings: list[Finding] = []
    for detector in detectors:
        try:
            findings.extend(detector(context))
        except Exception as error:
            findings.append(
                Finding(
                    detector=getattr(detector, "__name__", "unknown"),
                    severity=Severity.INFO,
                    reason=f"El detector fallo y su resultado no esta disponible: {error}",
                    evidence={"error": str(error)[:200]},
                )
            )

    findings.sort(key=_sort_key)
    score = min(MAX_SCORE, sum(f.points for f in findings))
    return ManipulationReport(
        mint=context.mint,
        score=score,
        findings=tuple(findings),
        detectors_run=len(detectors),
    )
