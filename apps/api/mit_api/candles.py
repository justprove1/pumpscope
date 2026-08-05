"""Velas OHLC reales y velas proyectadas (SPEC.md 17).

Dos cosas distintas y etiquetadas como tales:

- **Velas reales**: agregadas de los TradeEvent de la cadena. Cada operacion trae las reservas
  virtuales, asi que cada trade es un punto de precio verificable. Se conservan maximo y
  minimo porque la volatilidad se estima con Garman-Klass, no cierre a cierre: dos velas con
  el mismo cierre no son iguales si una oscilo un 40%.

- **Velas proyectadas**: NO son una prediccion. Son el cono de percentiles renderizado como
  velas — cuerpo entre p25 y p75, mechas entre p10 y p90. Si la vela sale enorme, significa
  que no se sabe, y eso es informacion.

**Alimentado por WebSocket.** El estado en vivo se llena desde una suscripcion `logsSubscribe`
(ver `LiveTracker`), no desde `getTransaction`: el endpoint publico estrangula esa llamada REST
con 429, pero el stream de logs no. El cliente lee de memoria en microsegundos; una sola
suscripcion por mint alimenta a todos los que lo miran.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import os
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import websockets
from mit_pumpfun.curve import CurveState
from mit_pumpfun.events import TradeEvent, find_trade_events
from mit_pumpfun.graduation import mentions_graduation
from mit_pumpfun.pumpswap import find_pumpswap_trades
from mit_shared.types import LAMPORTS_PER_SOL
from mit_solana.logs_stream import LogConnection, ResilientLogStream

from mit_api.trade import forget_graduation

# WebSocket publico por defecto. Se puede sustituir por Helius via env sin tocar codigo.
DEFAULT_WSS = "wss://api.mainnet-beta.solana.com"
# Un mint deja de vigilarse si nadie lo mira en este tiempo: no se mantiene una suscripcion
# viva por una pestana que el usuario cerro hace media hora.
IDLE_TIMEOUT_SECONDS = 120.0
BUCKET_SECONDS = 1


@dataclass(frozen=True, slots=True)
class Candle:
    """Vela OHLC. `projected` distingue lo observado de lo proyectado."""

    time: int
    open: float
    high: float
    low: float
    close: float
    volume_sol: float = 0.0
    trades: int = 0
    projected: bool = False

    def as_dict(self) -> dict[str, float | int | bool]:
        return {
            "time": self.time,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume_sol": round(self.volume_sol, 9),
            "trades": self.trades,
            "projected": self.projected,
        }


def build_candles(events: list[TradeEvent], bucket_seconds: int = BUCKET_SECONDS) -> list[Candle]:
    """Agrupa operaciones en velas por ventana temporal."""
    if not events:
        return []
    buckets: dict[int, list[TradeEvent]] = {}
    for event in sorted(events, key=lambda e: e.timestamp):
        if event.virtual_token_reserves <= 0:
            continue
        key = (event.timestamp // bucket_seconds) * bucket_seconds
        buckets.setdefault(key, []).append(event)

    candles: list[Candle] = []
    for key in sorted(buckets):
        group = buckets[key]
        prices = [e.virtual_sol_reserves / e.virtual_token_reserves for e in group]
        candles.append(
            Candle(
                time=key,
                open=prices[0],
                high=max(prices),
                low=min(prices),
                close=prices[-1],
                volume_sol=sum(e.sol_amount for e in group) / LAMPORTS_PER_SOL,
                trades=len(group),
            )
        )
    return candles


def realized_volatility_per_second(candles: list[Candle]) -> float:
    """Volatilidad por segundo con Garman-Klass.

    Usa el rango alto-bajo y es ~7 veces mas eficiente que cierre-a-cierre con la misma
    muestra. En velas con mechas grandes la diferencia no es cosmetica.
    """
    usable = [c for c in candles if c.high > 0 and c.low > 0 and c.open > 0 and c.close > 0]
    if len(usable) < 2:
        return 0.0
    total = 0.0
    for candle in usable:
        hl = math.log(candle.high / candle.low) ** 2
        co = math.log(candle.close / candle.open) ** 2
        total += 0.5 * hl - (2 * math.log(2) - 1) * co
    return math.sqrt(max(0.0, total / len(usable)))


# Cuantiles de la normal para el cono.
_Z = {0.10: -1.2816, 0.25: -0.6745, 0.50: 0.0, 0.75: 0.6745, 0.90: 1.2816}
SCALING_EXPONENT = 0.45


def project_candles(
    candles: list[Candle], seconds_ahead: int = 4, bucket_seconds: int = BUCKET_SECONDS
) -> list[Candle]:
    """Velas proyectadas del cono de percentiles.

    Cuerpo entre p25 y p75, mechas entre p10 y p90. NO es una prediccion: no hay modelo
    entrenado. Una vela ancha significa incertidumbre alta, no un movimiento esperado.

    La volatilidad NO escala con sqrt(t): en un memecoin los retornos no son independientes,
    y el exponente medido esta entre 0,32 y 0,57.
    """
    if not candles:
        return []
    sigma = realized_volatility_per_second(candles)
    last = candles[-1]
    projected: list[Candle] = []
    previous_close = last.close

    for step in range(1, seconds_ahead + 1):
        elapsed = step * bucket_seconds
        scale = sigma * (elapsed**SCALING_EXPONENT) if sigma > 0 else 0.0
        band = {p: previous_close * math.exp(z * scale) for p, z in _Z.items()}
        projected.append(
            Candle(
                time=last.time + elapsed,
                open=previous_close,
                high=band[0.90],
                low=band[0.10],
                close=band[0.50],
                projected=True,
            )
        )
        previous_close = band[0.50]
    return projected


@dataclass
class TokenLiveState:
    """Estado en memoria de un mint vigilado."""

    mint: str
    events: list[TradeEvent] = field(default_factory=list)
    seen: set[str] = field(default_factory=set)
    curve: CurveState | None = None
    last_refresh: float = 0.0
    last_access: float = field(default_factory=time.monotonic)
    connected: bool = False
    # El token ya no opera en la bonding curve sino en PumpSwap: gradúo.
    graduated: bool = False
    refresh_ms: float = 0.0
    error: str = ""

    def touch(self) -> None:
        self.last_access = time.monotonic()

    @property
    def idle(self) -> bool:
        return time.monotonic() - self.last_access > IDLE_TIMEOUT_SECONDS


class LiveTracker:
    """Vigila mints por WebSocket y sirve su estado desde memoria.

    En vez de pedir el historico con `getTransaction` —que el endpoint publico estrangula con
    429 al primer intento—, se suscribe a `logsSubscribe` con `mentions=[mint]`. Cada compra o
    venta llega como una notificacion cuyos logs ya traen el `TradeEvent` completo: se decodifica
    al vuelo, sin una sola llamada REST. El coste por operacion es un `base64.b64decode`.

    Contrapartida: solo se ven las operaciones ocurridas DESDE que empieza la vigilancia. No hay
    backfill. Para un token activo eso es una operacion por segundo; para uno parado no hay nada
    que mostrar, que es la respuesta correcta.
    """

    def __init__(self, wss_url: str | None = None) -> None:
        self._states: dict[str, TokenLiveState] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        helius_key = os.environ.get("HELIUS_API_KEY", "").strip()
        helius_wss = os.environ.get("HELIUS_WSS_URL", "").strip()
        if wss_url:
            self._wss_url = wss_url
        elif helius_key and helius_wss:
            self._wss_url = helius_wss
        else:
            self._wss_url = os.environ.get("SOLANA_FALLBACK_WSS_URL", DEFAULT_WSS)

    async def close(self) -> None:
        for task in self._tasks.values():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()

    def state(self, mint: str) -> TokenLiveState | None:
        state = self._states.get(mint)
        if state is not None:
            state.touch()
        return state

    def watch(self, mint: str) -> TokenLiveState:
        """Empieza a vigilar un mint (o renueva su interes)."""
        state = self._states.get(mint)
        if state is None:
            state = TokenLiveState(mint=mint)
            self._states[mint] = state
        state.touch()
        if mint not in self._tasks or self._tasks[mint].done():
            self._tasks[mint] = asyncio.create_task(self._watch_loop(state))
        return state

    async def _connect(self) -> LogConnection:
        return await websockets.connect(self._wss_url, ping_interval=20, max_size=20_000_000)

    async def _watch_loop(self, state: TokenLiveState) -> None:
        """Consume el stream WS hasta que el mint quede ocioso.

        El stream se reconecta solo ante cualquier corte; aqui solo se vigila el ocio para
        soltar la suscripcion cuando nadie mira. La suscripcion filtra por el mint, asi que
        solo llegan transacciones que lo mencionan.
        """
        stream = ResilientLogStream(state.mint, self._connect, silence_timeout=25.0)
        consumer = asyncio.create_task(self._consume(stream, state))
        try:
            # Vigilancia de ocio por tiempo (last_access), no por evento: un sondeo de 1 s es
            # el mecanismo correcto aqui, no un asyncio.Event.
            while not state.idle and not consumer.done():  # noqa: ASYNC110
                await asyncio.sleep(1.0)
            if consumer.done() and (exc := consumer.exception()) is not None:
                state.error = f"{type(exc).__name__}: {exc}"[:120]
        finally:
            state.connected = False
            consumer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await consumer

    async def _consume(self, stream: ResilientLogStream, state: TokenLiveState) -> None:
        async for notification in stream:
            state.connected = True
            self._ingest(notification, state)

    def _ingest(self, notification: dict[str, Any], state: TokenLiveState) -> None:
        """Decodifica una notificacion de log y actualiza el estado. No lanza."""
        try:
            value = ((notification.get("params") or {}).get("result") or {}).get("value") or {}
            if value.get("err"):
                return
            signature = value.get("signature")
            if signature:
                if signature in state.seen:
                    return
                state.seen.add(signature)
            logs = value.get("logs") or []
            # Un router puede empaquetar operaciones de varios tokens en una tx: solo cuentan
            # las de ESTE mint.
            fresh = [event for event in find_trade_events(logs) if event.mint == state.mint]
            if not fresh:
                # Sin eventos de la curva: puede que el token haya GRADUADO y opere ya en
                # PumpSwap. Esos eventos no traen el mint, pero la suscripcion ya filtra por el.
                # Se convierten a TradeEvent porque su forma es identica: asi velas, traccion,
                # ballenas y prerrebotes siguen funcionando sin distinguir el origen.
                swap_trades = find_pumpswap_trades(logs, state.mint)
                if swap_trades:
                    # **Ver operaciones de PumpSwap NO es ver una graduacion.** Basta con que
                    # una transaccion que menciona el mint pase por ese programa. Con esa
                    # regla el flag fallaba en 6 de cada 10 tokens, y los falsos positivos
                    # hacian que el panel se negara a operar tokens vivos. La graduacion es
                    # una instruccion concreta —`migrate`— y asi es como se reconoce.
                    if mentions_graduation(logs):
                        state.graduated = True
                        # Que lo confirme la cuenta de la curva cuanto antes, sin esperar a
                        # que caduque lo que hubiera guardado.
                        forget_graduation(state.mint)
                    fresh = [
                        TradeEvent(
                            mint=trade.mint,
                            sol_amount=trade.sol_amount,
                            token_amount=trade.token_amount,
                            is_buy=trade.is_buy,
                            user=trade.user,
                            timestamp=trade.timestamp,
                            virtual_sol_reserves=trade.virtual_sol_reserves,
                            virtual_token_reserves=trade.virtual_token_reserves,
                        )
                        for trade in swap_trades
                    ]
            if not fresh:
                return
            state.events.extend(fresh)
            state.last_refresh = time.monotonic()
            state.refresh_ms = 0.0
            state.error = ""

            # Ventana acotada: una suscripcion 24/7 con la lista creciendo es una fuga de memoria.
            if len(state.events) > 600:
                state.events = state.events[-600:]
            if len(state.seen) > 2000:
                state.seen = set(list(state.seen)[-2000:])

            latest = state.events[-1]
            state.curve = CurveState(
                virtual_sol_reserves=max(1, latest.virtual_sol_reserves),
                virtual_token_reserves=max(1, latest.virtual_token_reserves),
                real_token_reserves=latest.virtual_token_reserves // 2,
                token_total_supply=1_000_000_000_000_000,
            )
        except Exception as error:  # una notificacion corrupta no puede tumbar el stream
            state.error = f"{type(error).__name__}: {error}"[:120]


def full_precision(value: Decimal | float, places: int = 18) -> str:
    """Representacion COMPLETA, sin notacion cientifica ni redondeo visible.

    Los precios de un memecoin viven en el orden de 1e-14: cualquier redondeo los convierte
    en cero y hace la cifra inutil.
    """
    decimal = Decimal(str(value)) if not isinstance(value, Decimal) else value
    return f"{decimal:.{places}f}".rstrip("0").rstrip(".") or "0"
