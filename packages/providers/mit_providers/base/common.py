"""Contrato base que cumple TODO proveedor.

CLAUDE.md 2 y SPEC.md 32 exigen, sin excepciones: timeout, validacion de respuesta, manejo de
errores y retry con backoff. Esas obligaciones no se dejan a la buena voluntad de cada
adaptador: se declaran aqui, en el contrato.

INTERFACES ABSTRACTAS, SIN IMPLEMENTACION. Fase 0 no escribe un solo adaptador, porque
escribir un adaptador exige haber verificado antes los endpoints reales en la documentacion
vigente del proveedor (SPEC.md 32).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from types import TracebackType

from mit_data_models import ProviderHealth


class Capability(StrEnum):
    """Que sabe hacer un proveedor.

    Permite al registro elegir proveedor por capacidad en vez de por nombre, y degradar a
    otro cuando el primario cae, sin que el llamante se entere.
    """

    ONCHAIN_READ = "onchain_read"
    ONCHAIN_STREAM = "onchain_stream"
    TOKEN_DISCOVERY = "token_discovery"  # noqa: S105  (token SPL, no credencial)
    MARKET_DATA = "market_data"
    QUOTES = "quotes"
    SWAP_BUILD = "swap_build"
    HOLDERS = "holders"
    SOCIAL = "social"
    NEWS = "news"
    TOKEN_RISK = "token_risk"  # noqa: S105  (token SPL, no credencial)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Backoff exponencial con jitter.

    El jitter no es un detalle: sin el, N clientes que fallan a la vez reintentan a la vez y
    tumban al proveedor justo cuando se estaba recuperando.
    """

    max_attempts: int = 3
    initial_delay_seconds: float = 0.2
    max_delay_seconds: float = 5.0
    multiplier: float = 2.0
    jitter: bool = True


@dataclass(frozen=True, slots=True)
class RateLimitPolicy:
    """Limite propio del adaptador.

    Se configura POR DEBAJO del limite documentado del proveedor. Que nos corten por exceso
    de peticiones es un fallo nuestro, no suyo.
    """

    requests_per_second: float
    burst: int = 1


@dataclass(frozen=True, slots=True)
class CircuitBreakerPolicy:
    """Deja de llamar a un proveedor que esta fallando, y lo reintenta pasado un tiempo."""

    failure_threshold: int = 5
    recovery_timeout_seconds: float = 30.0
    half_open_max_calls: int = 1


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    """Configuracion de un adaptador.

    `requires_credential` + `credential_present` es lo que permite que el sistema arranque y
    funcione en modo degradado declarado, en vez de fallar entero porque falta una API key
    opcional (DATA_PROVIDERS.md 1).
    """

    name: str
    base_url: str | None = None
    timeout_seconds: float = 5.0
    requires_credential: bool = False
    credential_present: bool = False
    enabled: bool = False
    capabilities: frozenset[Capability] = field(default_factory=frozenset)
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    rate_limit: RateLimitPolicy | None = None
    circuit_breaker: CircuitBreakerPolicy = field(default_factory=CircuitBreakerPolicy)

    @property
    def is_usable(self) -> bool:
        """Un proveedor solo es usable si esta habilitado y tiene lo que necesita."""
        return self.enabled and (self.credential_present or not self.requires_credential)


class Provider(ABC):
    """Interfaz base de todo proveedor de datos.

    Se usa como context manager asincrono para que cerrar la conexion no dependa de que el
    llamante se acuerde.
    """

    @property
    @abstractmethod
    def config(self) -> ProviderConfig:
        """Configuracion efectiva de este proveedor."""

    @property
    @abstractmethod
    def capabilities(self) -> frozenset[Capability]:
        """Capacidades que ofrece esta instancia."""

    @abstractmethod
    async def health(self) -> ProviderHealth:
        """Estado actual del proveedor.

        No debe lanzar excepcion: un proveedor caido devuelve `status='down'`. Si esto
        fallara, el propio sistema de vigilancia se convertiria en un punto de fallo.
        """

    @abstractmethod
    async def close(self) -> None:
        """Cierra conexiones y libera recursos. Idempotente."""

    async def __aenter__(self) -> Provider:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()
