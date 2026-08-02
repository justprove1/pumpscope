"""Contratos de mercado: velas, trades, cotizaciones y rutas."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from mit_data_models.enums import Side, Venue


class Candle(BaseModel):
    """Vela OHLCV.

    Se conservan alto y bajo porque la volatilidad se estima con Garman-Klass, no cierre a
    cierre: el estimador cierre-a-cierre tira el recorrido intravela y en velas con mechas
    grandes eso no es cosmetico.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    mint: str
    open_time: datetime
    resolution_seconds: int = Field(gt=0)
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume_sol: Decimal = Decimal(0)
    trade_count: int = 0


class TradeEvent(BaseModel):
    """Una compra o venta individual, con la wallet que la hizo.

    La wallet es lo que permite detectar al creador vendiendo, los bundles y los clusters.
    Un feed de trades sin wallet sirve para pintar un grafico y para poco mas.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    mint: str
    signature: str
    instruction_index: int = 0
    slot: int
    block_time: datetime
    wallet: str
    side: Side
    venue: Venue = Venue.BONDING_CURVE
    sol_amount: Decimal = Field(ge=0)
    token_amount: int = Field(ge=0)
    price_sol: Decimal | None = None
    is_creator: bool = False


class QuoteRequest(BaseModel):
    """Peticion de cotizacion."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_mint: str
    output_mint: str
    amount: int = Field(gt=0)
    slippage_bps: int = Field(ge=0, le=10_000)
    only_direct_routes: bool = False


class Quote(BaseModel):
    """Cotizacion recibida.

    `requested_at` y `received_at` NO son decorativos: la antiguedad de la cotizacion es un
    veto de ejecucion (`max_quote_age_ms`). Una cotizacion caducada se descarta y se recotiza.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    input_mint: str
    output_mint: str
    in_amount: int
    out_amount: int
    min_out_amount: int
    price_impact_bps: int | None = None
    slippage_bps: int
    route: list[str] = Field(default_factory=list)
    requested_at: datetime
    received_at: datetime
    raw_reference: str | None = None

    @property
    def age_ms(self) -> int:
        """Antiguedad de la cotizacion en milisegundos."""
        return int((self.received_at - self.requested_at).total_seconds() * 1000)


class SimulationResult(BaseModel):
    """Resultado de `simulateTransaction`.

    Simular la VENTA antes de comprar es obligatorio: es la unica deteccion fiable de
    honeypot y de imposibilidad practica de salida (veto `sell_simulation_failed`).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    succeeded: bool
    units_consumed: int | None = None
    logs: list[str] = Field(default_factory=list)
    error: str | None = None
