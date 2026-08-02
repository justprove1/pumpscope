"""Datos de mercado, cotizaciones y construccion de swaps (SPEC.md 4.C, 4.D).

INTERFACES ABSTRACTAS, SIN IMPLEMENTACION.
"""

from __future__ import annotations

from abc import abstractmethod
from datetime import datetime
from decimal import Decimal

from mit_data_models import Candle, LiquidityState, Observation, Quote, QuoteRequest

from mit_providers.base.common import Provider


class MarketDataProvider(Provider):
    """Precio, velas y liquidez.

    Fuente SECUNDARIA por contrato (SPEC.md 4.D): corrobora, no decide. Ninguna orden se
    ejecuta sobre un precio que venga solo de aqui.
    """

    @abstractmethod
    async def get_price(self, mint: str) -> Observation[Decimal]:
        """Precio actual en SOL."""

    @abstractmethod
    async def get_candles(
        self,
        mint: str,
        resolution_seconds: int,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 500,
    ) -> Observation[list[Candle]]:
        """Velas OHLCV.

        Se piden con alto y bajo porque la volatilidad se estima con Garman-Klass: el
        estimador cierre-a-cierre descarta el recorrido intravela y es unas 7 veces menos
        eficiente con la misma muestra.
        """

    @abstractmethod
    async def get_liquidity(self, mint: str) -> Observation[LiquidityState]:
        """Liquidez y profundidad efectiva."""


class QuoteProvider(Provider):
    """Cotizaciones y rutas de swap.

    SPEC.md 4.C: no confiar en una sola cotizacion. Antes de operar se pide una NUEVA y se
    verifica importe de entrada, de salida, price impact, ruta, antiguedad y liquidez
    efectiva. Por eso `Quote` lleva `requested_at` y `received_at`: la antiguedad es un veto.
    """

    @abstractmethod
    async def get_quote(self, request: QuoteRequest) -> Observation[Quote]:
        """Cotiza un swap."""

    @abstractmethod
    async def build_swap_transaction(
        self, quote: Quote, user_public_key: str
    ) -> Observation[bytes]:
        """Construye la transaccion de swap, SIN firmar.

        Devuelve bytes serializados. Este metodo nunca ve una clave privada: firmar es
        competencia exclusiva del servicio signer (SECURITY.md 2).
        """
