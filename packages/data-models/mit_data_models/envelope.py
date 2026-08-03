"""Envelope de observacion (SPEC.md 5).

Todo dato que entre desde fuera viaja dentro de un `Observation`. No es burocracia: es lo que
permite responder despues las preguntas que importan cuando algo sale mal.

    - Quien lo dijo                      -> `provider`
    - Cuando lo dijo la fuente           -> `provider_timestamp`
    - Cuando lo recibimos nosotros       -> `received_timestamp`
    - En que punto de la cadena          -> `blockchain_slot`
    - Cuanto tardo                       -> `latency_ms`
    - Cuanto nos fiamos                  -> `confidence`
    - De donde salio exactamente         -> `raw_reference`

Un valor sin envelope no se persiste ni alimenta features. Si dos fuentes discrepan, la
comparacion se hace entre envelopes, y la divergencia baja la confianza de ambas.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class Observation[T](BaseModel):
    """Un dato observado, con su procedencia y su fiabilidad."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str = Field(min_length=1, max_length=48)
    normalized_value: T
    received_timestamp: datetime
    provider_timestamp: datetime | None = None
    blockchain_slot: int | None = Field(default=None, ge=0)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    latency_ms: int | None = Field(default=None, ge=0)
    raw_reference: str | None = None

    @property
    def is_stale(self) -> bool:
        """STUB Fase 1: el umbral de obsolescencia depende del tipo de dato y del modo.

        Se implementa junto al RiskEngine, que es quien define el limite (veto `stale_data`).
        """
        raise NotImplementedError


class ProviderHealth(BaseModel):
    """Salud de un proveedor. Alimenta `provider_health` y las metricas de Prometheus."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    status: str
    observed_at: datetime
    latency_p50_ms: int | None = None
    latency_p95_ms: int | None = None
    latency_p99_ms: int | None = None
    requests: int = 0
    errors: int = 0
    error_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    missing_data_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    divergence_pct: float | None = None
    circuit_open: bool = False
    last_error: str | None = None
