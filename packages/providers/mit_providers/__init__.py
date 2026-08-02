"""Capa de proveedores: interfaces abstractas + adaptadores intercambiables.

**Fase 0 entrega SOLO las interfaces.** `adapters/` esta vacio a proposito.

La razon no es falta de tiempo. SPEC.md 32 y CLAUDE.md 2 prohiben inventar endpoints o
comportamiento de una API no verificada. Escribir hoy un adaptador de Helius o de Jupiter
significaria escribir a partir de lo que uno recuerda de su documentacion, y eso produce
codigo que parece funcional y no lo es. Cada adaptador se escribira en Fase 1, uno a uno,
despues de verificar sus endpoints contra la documentacion vigente.

Mientras tanto, el resto del sistema puede escribirse contra estas abstracciones sin
bloquearse.
"""

from __future__ import annotations

from mit_providers.base import (
    BondingCurveProvider,
    Capability,
    EventStreamProvider,
    HolderProvider,
    MarketDataProvider,
    NewsProvider,
    OnChainReadProvider,
    Provider,
    ProviderConfig,
    QuoteProvider,
    SigningService,
    SocialProvider,
    TokenDiscoveryProvider,
    TokenRiskProvider,
    WalletGraphProvider,
)
from mit_providers.errors import (
    CircuitOpenError,
    ProviderAuthError,
    ProviderError,
    ProviderNotConfiguredError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)

__all__ = [
    "BondingCurveProvider",
    "Capability",
    "CircuitOpenError",
    "EventStreamProvider",
    "HolderProvider",
    "MarketDataProvider",
    "NewsProvider",
    "OnChainReadProvider",
    "Provider",
    "ProviderAuthError",
    "ProviderConfig",
    "ProviderError",
    "ProviderNotConfiguredError",
    "ProviderRateLimitError",
    "ProviderResponseError",
    "ProviderTimeoutError",
    "ProviderUnavailableError",
    "QuoteProvider",
    "SigningService",
    "SocialProvider",
    "TokenDiscoveryProvider",
    "TokenRiskProvider",
    "WalletGraphProvider",
]
