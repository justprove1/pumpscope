"""Metricas de ingesta (SPEC.md 24).

Se miden percentiles y no medias. Una media de latencia esconde exactamente lo que importa:
en un sistema que compite por llegar primero, el p99 es el que decide si pierdes la
operacion, y una media de 40 ms puede convivir con un p99 de 3 segundos.

La ventana es deslizante y acotada. Guardar todas las latencias de un proceso 24/7 es una
fuga de memoria, y ademas mezclaria el rendimiento de hace ocho horas con el de ahora.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class LatencySummary:
    """Resumen de latencias en milisegundos."""

    count: int
    p50: float
    p95: float
    p99: float
    maximum: float

    def as_dict(self) -> dict[str, float]:
        return {
            "count": float(self.count),
            "p50_ms": self.p50,
            "p95_ms": self.p95,
            "p99_ms": self.p99,
            "max_ms": self.maximum,
        }


class LatencyWindow:
    """Ventana deslizante de latencias con percentiles."""

    __slots__ = ("_samples",)

    def __init__(self, capacity: int = 4096) -> None:
        if capacity <= 0:
            msg = "la capacidad debe ser positiva"
            raise ValueError(msg)
        self._samples: deque[float] = deque(maxlen=capacity)

    def __len__(self) -> int:
        return len(self._samples)

    def record(self, milliseconds: float) -> None:
        self._samples.append(milliseconds)

    def summary(self) -> LatencySummary:
        if not self._samples:
            return LatencySummary(count=0, p50=0.0, p95=0.0, p99=0.0, maximum=0.0)
        ordered = sorted(self._samples)
        return LatencySummary(
            count=len(ordered),
            p50=_percentile(ordered, 0.50),
            p95=_percentile(ordered, 0.95),
            p99=_percentile(ordered, 0.99),
            maximum=ordered[-1],
        )


def _percentile(ordered: list[float], fraction: float) -> float:
    """Percentil por rango mas cercano sobre una lista YA ordenada.

    Sin interpolar: con latencias reales la interpolacion inventa un valor que nunca se
    midio, y para decidir umbrales operativos es preferible un numero que ocurrio de verdad.
    """
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, round(fraction * len(ordered)) - 1))
    return ordered[index]


@dataclass
class IngestMetrics:
    """Contadores y latencias de la ingesta.

    `events_per_second` se calcula sobre el tiempo transcurrido real, no sobre una ventana
    fija, para que el numero signifique algo desde el primer segundo de vida del proceso.
    """

    started_at: float = field(default_factory=time.monotonic)
    events_received: int = 0
    events_discarded: int = 0
    tokens_detected: int = 0
    duplicates: int = 0
    decode_errors: int = 0
    connection_failures: int = 0
    reconnections: int = 0
    persistence_errors: int = 0
    pipeline_latency: LatencyWindow = field(default_factory=LatencyWindow)
    persistence_latency: LatencyWindow = field(default_factory=LatencyWindow)

    @property
    def uptime_seconds(self) -> float:
        return max(1e-9, time.monotonic() - self.started_at)

    @property
    def events_per_second(self) -> float:
        return self.events_received / self.uptime_seconds

    def snapshot(self) -> dict[str, object]:
        """Estado actual, listo para servir por la API o exportar a Prometheus."""
        return {
            "uptime_seconds": round(self.uptime_seconds, 1),
            "events_received": self.events_received,
            "events_discarded": self.events_discarded,
            "events_per_second": round(self.events_per_second, 2),
            "tokens_detected": self.tokens_detected,
            "duplicates": self.duplicates,
            "decode_errors": self.decode_errors,
            "connection_failures": self.connection_failures,
            "reconnections": self.reconnections,
            "persistence_errors": self.persistence_errors,
            "pipeline_latency": self.pipeline_latency.summary().as_dict(),
            "persistence_latency": self.persistence_latency.summary().as_dict(),
        }
