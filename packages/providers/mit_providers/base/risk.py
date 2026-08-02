"""Proveedores externos de riesgo de token (SPEC.md 4.E).

Segunda opinion, nunca fuente unica. El RugRiskScore y el ManipulationRiskScore propios se
calculan on-chain (DATA_PROVIDERS.md 4); esto sirve para contrastar, y una discrepancia es
informacion util por si misma.

INTERFACES ABSTRACTAS, SIN IMPLEMENTACION.
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass, field

from mit_data_models import Observation

from mit_providers.base.common import Provider


@dataclass(frozen=True, slots=True)
class ExternalRiskAssessment:
    """Valoracion de riesgo de un tercero.

    `raw_flags` conserva las etiquetas originales del proveedor sin traducir. Normalizarlas
    a un vocabulario propio perderia el matiz justo cuando hay que decidir si creerselas.
    """

    provider: str
    mint: str
    risk_score: float | None = None
    verdict: str | None = None
    raw_flags: tuple[str, ...] = field(default_factory=tuple)


class TokenRiskProvider(Provider):
    """Riesgo de token segun un tercero."""

    @abstractmethod
    async def assess(self, mint: str) -> Observation[ExternalRiskAssessment]:
        """Valoracion externa del token.

        Nunca es un veto por si sola: alimenta el score, y su ausencia no bloquea nada.
        """
