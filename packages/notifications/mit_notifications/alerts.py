"""Alertas (SPEC.md 22).

**Cada alerta lleva datos verificables, nunca un mensaje vago.** "Actividad sospechosa" no
sirve para nada: el operador no puede comprobarlo ni decidir. "El creador vendio 0,070 SOL a
los 3 minutos, firma 5zk9..." si.

La deduplicacion no es un lujo: sin ella, una condicion persistente genera una alerta por
tick y el operador aprende a ignorar el canal. Una alerta ignorada es peor que ninguna.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum


class AlertChannel(StrEnum):
    TELEGRAM = "telegram"
    DISCORD = "discord"
    EMAIL = "email"
    WEBPUSH = "webpush"
    INTERNAL = "internal"


class AlertSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class Alert:
    """Una alerta con su evidencia."""

    alert_type: str
    severity: AlertSeverity
    title: str
    # Datos concretos y comprobables. Sin esto la alerta no se envia.
    facts: dict[str, str | int | float]
    mint: str | None = None
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.facts:
            msg = (
                f"la alerta '{self.alert_type}' no aporta datos verificables: "
                f"un mensaje vago no es una alerta"
            )
            raise ValueError(msg)

    @property
    def dedup_key(self) -> str:
        """Clave estable: mismo tipo, mismo token y mismos datos -> misma clave."""
        payload = json.dumps(
            {"type": self.alert_type, "mint": self.mint, "facts": self.facts},
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:32]

    def render(self) -> str:
        """Texto con las cifras dentro. Legible por un humano con prisa."""
        detail = " · ".join(f"{k}={v}" for k, v in sorted(self.facts.items()))
        prefix = f"[{self.severity.value.upper()}] {self.title}"
        if self.mint:
            return f"{prefix}\nmint: {self.mint}\n{detail}"
        return f"{prefix}\n{detail}"

    def as_dict(self) -> dict[str, object]:
        return {
            "alert_type": self.alert_type,
            "severity": self.severity.value,
            "title": self.title,
            "facts": dict(self.facts),
            "mint": self.mint,
            "dedup_key": self.dedup_key,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass
class AlertDispatcher:
    """Decide que alertas salen. NO envia: eso son adaptadores de Fase 6.

    Separado a proposito: la logica de deduplicacion y prioridad se prueba sin red.
    """

    cooldown: timedelta = timedelta(minutes=10)
    _recent: dict[str, datetime] = field(default_factory=dict)

    def should_send(self, alert: Alert, now: datetime) -> bool:
        """`False` si es un repetido dentro de la ventana de silencio.

        Las criticas SIEMPRE pasan: silenciar un kill switch porque ya se aviso hace ocho
        minutos es exactamente el fallo que no se puede permitir.
        """
        if alert.severity is AlertSeverity.CRITICAL:
            self._recent[alert.dedup_key] = now
            return True
        last = self._recent.get(alert.dedup_key)
        if last is not None and now - last < self.cooldown:
            return False
        self._recent[alert.dedup_key] = now
        return True

    def channels_for(self, severity: AlertSeverity) -> tuple[AlertChannel, ...]:
        """A mas gravedad, mas canales. Una critica no puede depender de uno solo."""
        if severity is AlertSeverity.CRITICAL:
            return (
                AlertChannel.TELEGRAM,
                AlertChannel.DISCORD,
                AlertChannel.EMAIL,
                AlertChannel.WEBPUSH,
            )
        if severity is AlertSeverity.WARNING:
            return (AlertChannel.TELEGRAM, AlertChannel.DISCORD)
        return (AlertChannel.INTERNAL,)
