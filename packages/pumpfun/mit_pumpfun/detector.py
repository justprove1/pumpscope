"""NewTokenDetector (SPEC.md 6).

Deliberadamente SIN entrada/salida: recibe notificaciones ya recibidas y devuelve tokens
detectados. Todo lo que toca la red vive en `mit_solana`, y la persistencia en el worker.

Esa separacion es lo que permite probar la deteccion —incluida la resiliencia frente a
duplicados tras una reconexion— sin abrir un socket ni levantar una base de datos, y de
forma determinista.

Presupuesto de latencia (objetivo SPEC.md 6: < 1 s desde que el evento llega al proveedor):

    notificacion recibida
      -> filtro por texto        ~microsegundos  (descarta el 99,9%)
      -> base64 + Borsh          ~microsegundos
      -> dedup                   O(1)
      -> token detectado

No hay ninguna llamada de red en ese camino, porque el `CreateEvent` viaja dentro del propio
log (ver `events.py`). Es la decision de diseno que hace alcanzable el objetivo.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from mit_shared.dedup import BoundedDedup

from mit_pumpfun.decoder import DecodeError
from mit_pumpfun.events import CreateEvent, find_create_event, looks_like_creation


@dataclass(frozen=True, slots=True)
class DetectedToken:
    """Un token nuevo detectado, con su trazabilidad (SPEC.md 5)."""

    event: CreateEvent
    signature: str
    slot: int
    provider: str
    received_timestamp: datetime
    # Tiempo de nuestro propio pipeline: de tener el mensaje a tener el token decodificado.
    # Es lo unico que controlamos y lo unico que podemos optimizar.
    pipeline_latency_ms: float
    # Retraso entre el timestamp on-chain del evento y nuestra recepcion. Resolucion de
    # SEGUNDOS, porque el evento trae el tiempo en segundos: sirve para detectar retrasos
    # grandes, no para medir milisegundos. Se declara para que nadie lo confunda con una
    # medida fina.
    onchain_lag_seconds: int | None
    raw_reference: str

    @property
    def mint(self) -> str:
        return self.event.mint

    @property
    def creator(self) -> str:
        """Creador segun el evento. NO es necesariamente quien firmo la transaccion."""
        return self.event.creator


@dataclass
class DetectorStats:
    """Contadores de ingesta (SPEC.md 24)."""

    observed: int = 0
    filtered_out: int = 0
    duplicates: int = 0
    decode_errors: int = 0
    detected: int = 0
    malformed: int = 0
    pipeline_latencies_ms: list[float] = field(default_factory=list)

    def as_dict(self) -> dict[str, int]:
        return {
            "observed": self.observed,
            "filtered_out": self.filtered_out,
            "duplicates": self.duplicates,
            "decode_errors": self.decode_errors,
            "detected": self.detected,
            "malformed": self.malformed,
        }


class NewTokenDetector:
    """Convierte notificaciones de `logsSubscribe` en tokens detectados.

    Idempotente por firma: la misma notificacion procesada dos veces produce un token la
    primera vez y `None` la segunda. Es lo que hace que una reconexion con reenvio de
    eventos no duplique nada.
    """

    def __init__(
        self,
        provider: str = "solana-rpc",
        dedup_capacity: int = 100_000,
    ) -> None:
        self._provider = provider
        self._dedup = BoundedDedup(dedup_capacity)
        self.stats = DetectorStats()

    @property
    def provider(self) -> str:
        return self._provider

    def observe(
        self, notification: dict[str, Any], received_timestamp: datetime | None = None
    ) -> DetectedToken | None:
        """Procesa una notificacion. Devuelve el token si es una creacion nueva.

        Devuelve `None` —sin lanzar— para todo lo demas: no es una creacion, ya se habia
        visto, o la transaccion fallo. Con ~361 eventos por segundo, el camino de descarte
        es el camino normal y tiene que ser barato y silencioso.
        """
        started = time.perf_counter()
        self.stats.observed += 1
        received = received_timestamp or datetime.now(UTC)

        value = notification.get("params", {}).get("result", {}).get("value", {})
        if not isinstance(value, dict):
            self.stats.malformed += 1
            return None

        # Una transaccion fallida no crea nada, por mucho que invoque al programa.
        if value.get("err") is not None:
            self.stats.filtered_out += 1
            return None

        logs = value.get("logs") or []
        if not looks_like_creation(logs):
            self.stats.filtered_out += 1
            return None

        signature = value.get("signature")
        if not signature:
            self.stats.malformed += 1
            return None

        # El dedup va ANTES de decodificar: un evento repetido no debe costar ni un base64.
        if not self._dedup.add(signature):
            self.stats.duplicates += 1
            return None

        try:
            event = find_create_event(logs)
        except DecodeError:
            # El programa cambio de formato. Es ruidoso a proposito: se cuenta y el llamante
            # decide si alerta. Tragarselo dejaria de detectar tokens en silencio.
            self.stats.decode_errors += 1
            return None

        if event is None:
            # Los logs decian "Create" pero no hay CreateEvent. Puede pasar si el filtro
            # barato acierta por casualidad con otra instruccion que empieza igual.
            self.stats.filtered_out += 1
            return None

        elapsed_ms = (time.perf_counter() - started) * 1000.0
        self.stats.detected += 1
        self.stats.pipeline_latencies_ms.append(elapsed_ms)

        lag = None
        if event.timestamp > 0:
            lag = int(received.timestamp()) - event.timestamp

        context = notification.get("params", {}).get("result", {}).get("context", {})
        return DetectedToken(
            event=event,
            signature=signature,
            slot=int(context.get("slot", 0)),
            provider=self._provider,
            received_timestamp=received,
            pipeline_latency_ms=elapsed_ms,
            onchain_lag_seconds=lag,
            raw_reference=signature,
        )

    def already_seen(self, signature: str) -> bool:
        return signature in self._dedup
