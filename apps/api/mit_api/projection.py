"""Proyeccion de precio a corto plazo (SPEC.md 17).

**Esto NO es una prediccion.** No hay modelo entrenado: el corpus todavia no da para uno, y
un numero concreto seria inventado.

Lo que si es honesto y esta respaldado: una PROYECCION de percentiles derivada de
(a) la curva de bonding real del token y (b) la volatilidad medida de sus propias
operaciones. Devuelve un cono, no una linea. Si el cono es ancho, es que no se sabe — y eso
es informacion, no un defecto del grafico.

La volatilidad NO escala con sqrt(t): en un memecoin los retornos no son independientes.
Se usa el exponente medido (~0,45), igual que en el simulador.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass

from mit_pumpfun.curve import CurveState, impact_curve, market_cap_lamports, spot_price_lamports
from mit_shared.types import LAMPORTS_PER_SOL

# Exponente de escalado temporal medido en memecoins (rango observado 0,32-0,57).
SCALING_EXPONENT = 0.45
# Percentiles del cono. Se muestran los extremos para que el ancho sea visible.
PERCENTILES = (0.10, 0.25, 0.50, 0.75, 0.90)
# Cuantiles de la normal estandar para esos percentiles.
_Z = {0.10: -1.2816, 0.25: -0.6745, 0.50: 0.0, 0.75: 0.6745, 0.90: 1.2816}


@dataclass(frozen=True, slots=True)
class ProjectionPoint:
    seconds_ahead: float
    percentile: float
    price_sol: float


def realized_volatility(prices: list[float]) -> float:
    """Volatilidad por segundo a partir de retornos logaritmicos observados.

    Con menos de tres puntos devuelve 0: sin muestra no se inventa una volatilidad, y un
    cono de ancho cero deja claro que no hay base para proyectar nada.
    """
    if len(prices) < 3:
        return 0.0
    returns = [math.log(b / a) for a, b in itertools.pairwise(prices) if a > 0 and b > 0]
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return math.sqrt(variance)


def project(
    curve: CurveState, prices: list[float], horizon_seconds: float = 4.0, steps: int = 8
) -> list[ProjectionPoint]:
    """Cono de percentiles hasta `horizon_seconds`.

    El precio de partida sale de la curva REAL, no del ultimo trade: la curva es la verdad
    del protocolo y el ultimo trade solo un punto de ella.
    """
    spot = float(spot_price_lamports(curve)) / LAMPORTS_PER_SOL
    sigma = realized_volatility(prices)

    points: list[ProjectionPoint] = []
    for step in range(steps + 1):
        seconds = horizon_seconds * step / steps
        scale = sigma * (seconds**SCALING_EXPONENT) if seconds > 0 else 0.0
        for percentile in PERCENTILES:
            points.append(
                ProjectionPoint(
                    seconds_ahead=round(seconds, 2),
                    percentile=percentile,
                    price_sol=spot * math.exp(_Z[percentile] * scale),
                )
            )
    return points


def token_snapshot(curve: CurveState) -> dict[str, object]:
    """Datos reales del token: capitalizacion, liquidez y coste de entrar."""
    from mit_pumpfun.curve import graduation_market_cap_lamports, progress_pct, sol_to_complete

    return {
        "price_sol": float(spot_price_lamports(curve)) / LAMPORTS_PER_SOL,
        "market_cap_sol": market_cap_lamports(curve) / LAMPORTS_PER_SOL,
        "liquidity_sol": curve.virtual_sol_reserves / LAMPORTS_PER_SOL,
        "sol_to_graduate": sol_to_complete(curve) / LAMPORTS_PER_SOL,
        "graduation_market_cap_sol": (graduation_market_cap_lamports(curve) / LAMPORTS_PER_SOL),
        "progress_pct": float(progress_pct(curve)),
        "price_impact_bps": impact_curve(curve),
    }


@dataclass(frozen=True, slots=True)
class ChainState:
    """Estado reconstruido de la cadena, sin pasar por la base."""

    curve: CurveState
    prices: list[float]
    trades: int
    last_trader: str


async def fetch_from_chain(mint: str, max_transactions: int = 12) -> ChainState | None:
    """Reconstruye el estado de un token DESDE LA CADENA, sin pasar por la base.

    Necesario porque la base solo conoce los tokens que nacieron mientras el worker escuchaba.
    Cualquier token que el usuario busque —que es el caso normal— no va a estar ahi, y
    responder "no lo conozco" cuando el dato esta a una consulta de distancia es inutil.

    Las reservas virtuales vienen dentro de cada TradeEvent, asi que una sola pasada por las
    transacciones recientes da el estado actual de la curva Y el historico de precios.
    """
    from mit_pumpfun.events import find_trade_events
    from mit_solana.rpc import RpcError, RpcLimits, RpcRateLimitedError, SolanaRpc

    async with SolanaRpc(limits=RpcLimits(requests_per_second=4.0)) as rpc:
        try:
            signatures = await rpc.get_signatures(mint, limit=40)
        except (RpcError, RpcRateLimitedError):
            return None
        if not signatures:
            return None

        events = []
        for entry in signatures[:max_transactions]:
            if entry.get("err"):
                continue
            try:
                transaction = await rpc.get_transaction(entry["signature"])
            except (RpcError, RpcRateLimitedError):
                break
            if not transaction:
                continue
            logs = (transaction.get("meta") or {}).get("logMessages") or []
            events.extend(find_trade_events(logs))

    if not events:
        return None

    # `getSignaturesForAddress` devuelve de mas reciente a mas antiguo: se ordena por tiempo
    # para que el historico de precios tenga sentido y el ultimo sea el estado actual.
    events.sort(key=lambda e: e.timestamp)
    latest = events[-1]
    return ChainState(
        curve=CurveState(
            virtual_sol_reserves=max(1, latest.virtual_sol_reserves),
            virtual_token_reserves=max(1, latest.virtual_token_reserves),
            # No observable desde el evento: se estima desde la reserva virtual para que la
            # curva sea coherente. Queda declarado como aproximacion.
            real_token_reserves=latest.virtual_token_reserves // 2,
            token_total_supply=1_000_000_000_000_000,
        ),
        prices=[
            e.virtual_sol_reserves / e.virtual_token_reserves
            for e in events
            if e.virtual_token_reserves > 0
        ],
        trades=len(events),
        last_trader=latest.user,
    )
