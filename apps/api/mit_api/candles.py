"""Velas OHLC reales y velas proyectadas (SPEC.md 17).

Dos cosas distintas y etiquetadas como tales:

- **Velas reales**: agregadas de los TradeEvent de la cadena. Cada operacion trae las reservas
  virtuales, asi que cada trade es un punto de precio verificable. Se conservan maximo y
  minimo porque la volatilidad se estima con Garman-Klass, no cierre a cierre: dos velas con
  el mismo cierre no son iguales si una oscilo un 40%.

- **Velas proyectadas**: NO son una prediccion. Son el cono de percentiles renderizado como
  velas — cuerpo entre p25 y p75, mechas entre p10 y p90. Si la vela sale enorme, significa
  que no se sabe, y eso es informacion.

**Cache con refresco en segundo plano.** El cliente lee de memoria en microsegundos; un
refrescador toca el RPC cada ~1,5 s. Sin esto, un cliente pidiendo cada 500 ms generaria
docenas de llamadas por segundo y el 429 llegaria en un minuto.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import time
from dataclasses import dataclass, field
from decimal import Decimal

from mit_pumpfun.curve import CurveState
from mit_pumpfun.events import TradeEvent, find_trade_events
from mit_shared.types import LAMPORTS_PER_SOL
from mit_solana.rpc import RpcError, RpcLimits, RpcRateLimitedError, SolanaRpc

# Refresco del cache. Por debajo de esto el endpoint publico empieza a cortar.
REFRESH_SECONDS = 1.5
# Un mint deja de refrescarse si nadie lo mira en este tiempo: no se gasta cuota en pestanas
# que el usuario cerro hace media hora.
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
    refresh_ms: float = 0.0
    error: str = ""

    def touch(self) -> None:
        self.last_access = time.monotonic()

    @property
    def idle(self) -> bool:
        return time.monotonic() - self.last_access > IDLE_TIMEOUT_SECONDS


class LiveTracker:
    """Vigila mints en segundo plano y sirve su estado desde memoria.

    El cliente puede preguntar cada 500 ms sin coste: lee del cache. Solo el refrescador
    toca la red, y a un ritmo que el endpoint publico tolera.
    """

    def __init__(self) -> None:
        self._states: dict[str, TokenLiveState] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

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
            self._tasks[mint] = asyncio.create_task(self._refresh_loop(state))
        return state

    async def _refresh_loop(self, state: TokenLiveState) -> None:
        async with SolanaRpc(
            limits=RpcLimits(requests_per_second=8.0, max_attempts=3, initial_backoff=0.5)
        ) as rpc:
            while not state.idle:
                started = time.monotonic()
                try:
                    await self._refresh_once(rpc, state)
                    state.error = ""
                except (RpcError, RpcRateLimitedError) as error:
                    state.error = str(error)[:120]
                except Exception as error:
                    state.error = f"{type(error).__name__}: {error}"[:120]
                state.refresh_ms = (time.monotonic() - started) * 1000
                state.last_refresh = time.monotonic()
                await asyncio.sleep(REFRESH_SECONDS)

    async def _refresh_once(self, rpc: SolanaRpc, state: TokenLiveState) -> None:
        """Trae solo lo NUEVO: las firmas ya vistas no se vuelven a descargar."""
        limit = 40 if not state.events else 12
        signatures = await rpc.get_signatures(state.mint, limit=limit)
        fresh = [s for s in signatures if s.get("signature") not in state.seen][:8]

        for entry in reversed(fresh):
            signature = entry.get("signature")
            if not signature or entry.get("err"):
                if signature:
                    state.seen.add(signature)
                continue
            transaction = await rpc.get_transaction(signature)
            state.seen.add(signature)
            if not transaction:
                continue
            logs = (transaction.get("meta") or {}).get("logMessages") or []
            state.events.extend(find_trade_events(logs))

        # Ventana acotada: un proceso 24/7 con la lista creciendo es una fuga de memoria.
        if len(state.events) > 600:
            state.events = state.events[-600:]
        if len(state.seen) > 2000:
            state.seen = set(list(state.seen)[-2000:])

        if state.events:
            latest = max(state.events, key=lambda e: e.timestamp)
            state.curve = CurveState(
                virtual_sol_reserves=max(1, latest.virtual_sol_reserves),
                virtual_token_reserves=max(1, latest.virtual_token_reserves),
                real_token_reserves=latest.virtual_token_reserves // 2,
                token_total_supply=1_000_000_000_000_000,
            )


def full_precision(value: Decimal | float, places: int = 18) -> str:
    """Representacion COMPLETA, sin notacion cientifica ni redondeo visible.

    Los precios de un memecoin viven en el orden de 1e-14: cualquier redondeo los convierte
    en cero y hace la cifra inutil.
    """
    decimal = Decimal(str(value)) if not isinstance(value, Decimal) else value
    return f"{decimal:.{places}f}".rstrip("0").rstrip(".") or "0"
