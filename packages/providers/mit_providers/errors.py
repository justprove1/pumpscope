"""Errores de proveedores.

La jerarquia importa porque el comportamiento ante cada uno es distinto: un rate limit se
reintenta con espera, un timeout se reintenta con backoff, y una respuesta que no valida NO
se reintenta nunca (repetirla dara el mismo resultado y quema cuota).
"""

from __future__ import annotations

from mit_shared.errors import MitError


class ProviderError(MitError):
    """Raiz de los errores de proveedor."""

    def __init__(self, provider: str, message: str) -> None:
        self.provider = provider
        super().__init__(f"[{provider}] {message}")


class ProviderTimeoutError(ProviderError):
    """La llamada excedio su timeout. Reintentable con backoff."""


class ProviderRateLimitError(ProviderError):
    """Rate limit alcanzado. Reintentable tras `retry_after_seconds`."""

    def __init__(self, provider: str, message: str, retry_after_seconds: float | None) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(provider, message)


class ProviderUnavailableError(ProviderError):
    """El proveedor no responde o devuelve 5xx. Reintentable; puede abrir el circuito."""


class ProviderAuthError(ProviderError):
    """Credencial ausente, invalida o sin permisos. NO reintentable."""


class ProviderResponseError(ProviderError):
    """La respuesta no cumple el contrato esperado.

    NO reintentable: repetir la llamada dara lo mismo. Suele significar que la API cambio,
    asi que es un error que debe llegar al operador, no perderse en un log.
    """


class CircuitOpenError(ProviderError):
    """El circuit breaker esta abierto: no se intenta la llamada."""


class ProviderNotConfiguredError(ProviderError):
    """El proveedor existe como interfaz pero no tiene credencial ni adaptador activo.

    Es el estado normal de casi todos los proveedores en Fase 0 (DATA_PROVIDERS.md 7).
    """
