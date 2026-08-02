"""Deteccion de tokens nuevos y estado de Pump.fun / PumpSwap (SPEC.md 4.B, 6).

INTERFACES ABSTRACTAS, SIN IMPLEMENTACION.
"""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import AsyncIterator

from mit_data_models import BondingCurveState, Observation, TokenIdentity, TradeEvent

from mit_providers.base.common import Provider


class TokenDiscoveryProvider(Provider):
    """Descubrimiento de tokens nuevos.

    Objetivo de latencia: registrar el token en menos de 1 segundo desde que el evento llega
    al proveedor (SPEC.md 6). Por eso `stream_new_tokens` es lo primario y `get_recent_tokens`
    existe solo para rellenar huecos tras un reinicio: el polling nunca sera lo bastante
    rapido para llegar el primero.
    """

    @abstractmethod
    def stream_new_tokens(self) -> AsyncIterator[Observation[TokenIdentity]]:
        """Tokens nuevos segun se crean."""

    @abstractmethod
    async def get_recent_tokens(self, limit: int = 100) -> Observation[list[TokenIdentity]]:
        """Tokens recientes. Para recuperacion tras reinicio, no para operar."""

    @abstractmethod
    async def get_token(self, mint: str) -> Observation[TokenIdentity | None]:
        """Identidad de un token concreto."""


class BondingCurveProvider(Provider):
    """Estado de la bonding curve y de la migracion."""

    @abstractmethod
    async def get_curve_state(self, mint: str) -> Observation[BondingCurveState]:
        """Estado actual de la curva.

        El umbral de graduacion se DERIVA de las reservas de este token, no de una constante
        en dolares (ver `BondingCurveState`).
        """

    @abstractmethod
    def stream_curve_updates(self, mint: str) -> AsyncIterator[Observation[BondingCurveState]]:
        """Cambios de la curva en tiempo real."""

    @abstractmethod
    async def get_recent_trades(self, mint: str, limit: int = 300) -> Observation[list[TradeEvent]]:
        """Operaciones recientes CON la wallet de cada una.

        La wallet es imprescindible: sin ella no se detecta al creador vendiendo, ni los
        bundles, ni los clusters. Un feed de trades anonimo no vale para este sistema.
        """

    @abstractmethod
    def stream_trades(self, mint: str) -> AsyncIterator[Observation[TradeEvent]]:
        """Operaciones en tiempo real."""
