"""StrategyLab: versionado y aprobacion manual (SPEC.md 20).

**El sistema NO modifica el riesgo real de forma autonoma.** Una estrategia nueva recorre
cuatro etapas y la ultima la firma una persona:

    historico -> fuera de muestra -> paper -> APROBACION MANUAL -> version desplegada

No se puede saltar una etapa ni auto-aprobar. Y toda version es reversible: se conserva la
anterior para poder volver.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class Stage(StrEnum):
    """Etapas de SPEC.md 20, en orden obligatorio."""

    DRAFT = "draft"
    BACKTESTED = "backtested"
    OUT_OF_SAMPLE = "out_of_sample"
    PAPER = "paper"
    APPROVED = "approved"
    RETIRED = "retired"


_ORDER = [Stage.DRAFT, Stage.BACKTESTED, Stage.OUT_OF_SAMPLE, Stage.PAPER, Stage.APPROVED]


class PromotionError(RuntimeError):
    """Se intento saltar una etapa o auto-aprobar."""


@dataclass
class StrategyVersion:
    """Una version con su historial de promociones."""

    name: str
    version: int
    params: dict[str, float]
    stage: Stage = Stage.DRAFT
    approved_by: str | None = None
    approved_at: datetime | None = None
    history: list[str] = field(default_factory=list)

    @property
    def is_deployable(self) -> bool:
        return self.stage is Stage.APPROVED and self.approved_by is not None

    def promote(self, target: Stage, *, operator: str = "", now: datetime | None = None) -> None:
        """Avanza UNA etapa. No se salta ninguna.

        La aprobacion exige `operator`: el sistema no puede aprobarse a si mismo, y sin
        nombre no hay a quien preguntar cuando algo salga mal.
        """
        if target not in _ORDER:
            msg = f"{target} no es una etapa promocionable"
            raise PromotionError(msg)
        current = _ORDER.index(self.stage) if self.stage in _ORDER else -1
        if _ORDER.index(target) != current + 1:
            msg = (
                f"no se puede pasar de {self.stage.value} a {target.value}: "
                f"las etapas de SPEC.md 20 se recorren una a una"
            )
            raise PromotionError(msg)
        if target is Stage.APPROVED and not operator:
            msg = "la aprobacion es MANUAL: exige identificar a la persona que aprueba"
            raise PromotionError(msg)

        self.stage = target
        if target is Stage.APPROVED:
            self.approved_by = operator
            self.approved_at = now
        self.history.append(f"{target.value} por {operator or 'sistema'}")


@dataclass
class StrategyLab:
    """Registro de versiones. Toda promocion queda registrada y es reversible."""

    _versions: dict[str, list[StrategyVersion]] = field(default_factory=dict)

    def register(self, name: str, params: dict[str, float]) -> StrategyVersion:
        versions = self._versions.setdefault(name, [])
        version = StrategyVersion(name=name, version=len(versions) + 1, params=dict(params))
        versions.append(version)
        return version

    def deployed(self, name: str) -> StrategyVersion | None:
        """Ultima version aprobada, o `None` si no hay ninguna."""
        approved = [v for v in self._versions.get(name, []) if v.is_deployable]
        return approved[-1] if approved else None

    def rollback(self, name: str) -> StrategyVersion | None:
        """Vuelve a la version aprobada anterior. Toda version es reversible."""
        approved = [v for v in self._versions.get(name, []) if v.is_deployable]
        if len(approved) < 2:
            return None
        approved[-1].stage = Stage.RETIRED
        approved[-1].history.append("retirada por rollback")
        return approved[-2]
