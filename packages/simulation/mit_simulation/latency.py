"""Cadena de latencia de una operacion (SPEC.md 17).

**Es la variable dominante, no un detalle.** En un token de dos minutos, el tiempo entre que
el evento ocurre y la transaccion entra en un bloque suele mover el precio mas que el margen
de la operacion entera. Un simulador que asume ejecucion instantanea no se equivoca un poco:
se equivoca en el unico factor que decide el resultado.

Seis etapas, cada una con su distribucion medida, no una constante:

    t1 deteccion    evento on-chain -> nuestro proceso
    t2 decision     features + scores + riesgo
    t3 cotizacion   peticion de quote y respuesta
    t4 construccion armado de la transaccion
    t5 firma        ida y vuelta al signer aislado
    t6 inclusion    envio -> confirmacion en bloque

Los valores por defecto de t1 y t2 NO son inventados: salen de lo medido en Fase 1 contra
mainnet con el RPC publico (deteccion 1-2 s de retraso del proveedor, pipeline propio 0,15 ms).
Los de t3 a t6 son estimaciones declaradas y se recalibraran contra ejecucion real antes de
operar (SIMULATION.md 5).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Distribution:
    """Lognormal acotada, en milisegundos.

    Lognormal y no normal porque las latencias de red tienen cola derecha: el p99 esta muy
    lejos de la mediana, y es el p99 el que decide si pierdes la operacion.
    """

    median_ms: float
    sigma: float = 0.5
    minimum_ms: float = 0.0
    maximum_ms: float = 60_000.0

    def sample(self, rng: random.Random) -> float:
        if self.sigma <= 0:
            return self.median_ms
        value = self.median_ms * rng.lognormvariate(0.0, self.sigma)
        return max(self.minimum_ms, min(self.maximum_ms, value))


@dataclass(frozen=True, slots=True)
class LatencyModel:
    """Las seis etapas. `sample()` devuelve el desglose completo, no solo el total.

    Devolver el desglose no es cosmetico: cuando la ejecucion real se desvie de la simulada
    hay que saber QUE etapa se desvio, o la recalibracion es adivinar.
    """

    # Medido en Fase 1: el RPC publico entrega el evento con 1-2 s de retraso.
    detection: Distribution = field(default_factory=lambda: Distribution(1500.0, 0.35))
    # Medido en Fase 1: pipeline propio p50 0,15 ms, p99 0,28 ms.
    decision: Distribution = field(default_factory=lambda: Distribution(0.2, 0.4))
    quote: Distribution = field(default_factory=lambda: Distribution(180.0, 0.6))
    build: Distribution = field(default_factory=lambda: Distribution(15.0, 0.4))
    sign: Distribution = field(default_factory=lambda: Distribution(25.0, 0.5))
    inclusion: Distribution = field(default_factory=lambda: Distribution(900.0, 0.8))

    def sample(self, rng: random.Random) -> LatencyBreakdown:
        return LatencyBreakdown(
            detection_ms=self.detection.sample(rng),
            decision_ms=self.decision.sample(rng),
            quote_ms=self.quote.sample(rng),
            build_ms=self.build.sample(rng),
            sign_ms=self.sign.sample(rng),
            inclusion_ms=self.inclusion.sample(rng),
        )


@dataclass(frozen=True, slots=True)
class LatencyBreakdown:
    """Latencia de una operacion concreta, etapa por etapa."""

    detection_ms: float
    decision_ms: float
    quote_ms: float
    build_ms: float
    sign_ms: float
    inclusion_ms: float

    @property
    def total_ms(self) -> float:
        return (
            self.detection_ms
            + self.decision_ms
            + self.quote_ms
            + self.build_ms
            + self.sign_ms
            + self.inclusion_ms
        )

    @property
    def quote_age_ms(self) -> float:
        """Antiguedad de la cotizacion en el momento de entrar en bloque.

        Es lo que se compara con `max_quote_age_ms`: la cotizacion envejece desde que se
        pide hasta que la transaccion se confirma, no hasta que se firma.
        """
        return self.build_ms + self.sign_ms + self.inclusion_ms

    def as_dict(self) -> dict[str, float]:
        return {
            "detection_ms": round(self.detection_ms, 3),
            "decision_ms": round(self.decision_ms, 3),
            "quote_ms": round(self.quote_ms, 3),
            "build_ms": round(self.build_ms, 3),
            "sign_ms": round(self.sign_ms, 3),
            "inclusion_ms": round(self.inclusion_ms, 3),
            "total_ms": round(self.total_ms, 3),
        }


# Escenario adverso para el criterio de SPEC.md 18: una estrategia solo es candidata si
# sobrevive a latencias peores que la mediana. Se usa el percentil alto, no el tipico.
STRESSED_LATENCY = LatencyModel(
    detection=Distribution(3500.0, 0.4),
    decision=Distribution(1.0, 0.4),
    quote=Distribution(600.0, 0.7),
    build=Distribution(40.0, 0.4),
    sign=Distribution(80.0, 0.5),
    inclusion=Distribution(4000.0, 0.9),
)
