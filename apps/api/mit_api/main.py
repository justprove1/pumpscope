"""API de solo lectura del dashboard (API.md, SPEC.md 21).

**Solo lectura, sin excepciones.** No hay ni una ruta que abra o cierre una posicion, cambie
un limite o toque una clave. En Fase 1 no existe siquiera un motor de ejecucion al que
llamar, y esta restriccion es del contrato, no de la implementacion.

La API no calcula nada: sirve lo que el worker ha persistido, y reenvia por WebSocket lo que
el worker publica en Redis.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as redis
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from mit_pumpfun.curve import CurveState
from sqlalchemy.ext.asyncio import create_async_engine

from mit_api.candles import (
    LiveTracker,
    build_candles,
    full_precision,
    project_candles,
    realized_volatility_per_second,
)
from mit_api.projection import fetch_from_chain, project, token_snapshot
from mit_api.queries import TokenQueries

CHANNEL_NEW_TOKENS = "mit:tokens.new"
CHANNEL_ANALYSIS = "mit:tokens.analysis"
HEARTBEAT_SECONDS = 15.0


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.engine = create_async_engine(os.environ["DATABASE_URL"], pool_size=5)
    app.state.queries = TokenQueries(app.state.engine)
    app.state.tracker = LiveTracker()
    app.state.redis = redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))  # type: ignore[no-untyped-call]  # redis no anota from_url
    try:
        yield
    finally:
        await app.state.tracker.close()
        await app.state.redis.aclose()
        await app.state.engine.dispose()


app = FastAPI(
    title="Memecoin Intelligence Terminal",
    version="0.1.0",
    description="API de solo lectura. Fase 1: deteccion de tokens, sin trading.",
    lifespan=lifespan,
)

# El dashboard corre en otro puerto en desarrollo. Solo localhost: esta API no se publica.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/status")
async def status() -> dict[str, Any]:
    """Estado del sistema. El modo se declara siempre, para que nunca haya duda."""
    return {
        "mode": "DRY_RUN",
        "phase": 1,
        "trading_enabled": False,
        "live_trading_enabled": os.environ.get("ENABLE_LIVE_TRADING", "false") == "true",
        "signer_mode": os.environ.get("SIGNER_MODE", "disabled"),
    }


@app.get("/v1/tokens")
async def tokens(limit: int = 50) -> dict[str, Any]:
    """Radar: ultimos tokens detectados (SPEC.md 21)."""
    queries: TokenQueries = app.state.queries
    capped = max(1, min(limit, 200))
    rows = await queries.recent_tokens(limit=capped)
    return {"count": len(rows), "tokens": rows}


@app.websocket("/v1/stream")
async def stream(websocket: WebSocket) -> None:
    """Tokens nuevos en vivo.

    Se reenvia lo que el worker publica en Redis. El heartbeat existe porque un WebSocket
    sin trafico lo cierra cualquier proxy intermedio, y el cliente no distinguiria un
    sistema tranquilo de uno muerto.
    """
    await websocket.accept()
    client = app.state.redis
    pubsub = client.pubsub()
    await pubsub.subscribe(CHANNEL_NEW_TOKENS, CHANNEL_ANALYSIS)

    try:
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=HEARTBEAT_SECONDS
            )
            if message is None:
                await websocket.send_text(json.dumps({"channel": "system", "event": "heartbeat"}))
                continue
            data = message["data"]
            payload = data.decode() if isinstance(data, bytes) else str(data)
            raw_channel = message.get("channel")
            channel_name = (
                raw_channel.decode() if isinstance(raw_channel, bytes) else str(raw_channel)
            )
            is_analysis = channel_name == CHANNEL_ANALYSIS
            await websocket.send_text(
                json.dumps(
                    {
                        "channel": "tokens.analysis" if is_analysis else "tokens.new",
                        "event": "analysis" if is_analysis else "token",
                        "payload": json.loads(payload),
                    }
                )
            )
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        with contextlib.suppress(Exception):
            await pubsub.unsubscribe(CHANNEL_NEW_TOKENS, CHANNEL_ANALYSIS)
            await pubsub.aclose()


def _extract_mint(reference: str) -> str:
    """Acepta un mint pelado o un enlace de pump.fun / DexScreener / Solscan."""
    cleaned = reference.strip().split("?")[0].rstrip("/")
    return cleaned.split("/")[-1] if "/" in cleaned else cleaned


@app.get("/v1/tokens/{reference}/detail")
async def token_detail(reference: str, horizon_seconds: float = 4.0) -> dict[str, Any]:
    """Datos reales del token y proyeccion de precio.

    `projection` es un CONO DE PERCENTILES, no una prediccion. No hay modelo entrenado y un
    numero concreto seria inventado. Si el cono sale ancho, significa que no se sabe.
    """
    from sqlalchemy import text

    mint = _extract_mint(reference)
    engine = app.state.engine

    async with engine.connect() as connection:
        token = (
            (
                await connection.execute(
                    text(
                        "SELECT mint, symbol, name, uri, creator_address, first_seen_at "
                        "FROM tokens WHERE mint = :mint"
                    ),
                    {"mint": mint},
                )
            )
            .mappings()
            .first()
        )
        curve_row = (
            (
                await connection.execute(
                    text(
                        "SELECT virtual_sol_reserves, virtual_token_reserves, real_token_reserves "
                        "FROM bonding_curve_snapshots WHERE mint = :mint "
                        "ORDER BY observed_at DESC LIMIT 1"
                    ),
                    {"mint": mint},
                )
            )
            .mappings()
            .first()
        )
        history = (
            (
                await connection.execute(
                    text(
                        "SELECT price_sol FROM price_snapshots WHERE mint = :mint "
                        "ORDER BY observed_at ASC LIMIT 200"
                    ),
                    {"mint": mint},
                )
            )
            .scalars()
            .all()
        )

    source = "base de datos"
    if curve_row is not None:
        curve = CurveState(
            virtual_sol_reserves=int(curve_row["virtual_sol_reserves"] or 1),
            virtual_token_reserves=int(curve_row["virtual_token_reserves"] or 1),
            real_token_reserves=int(curve_row["real_token_reserves"] or 0),
            token_total_supply=1_000_000_000_000_000,
        )
        prices = [float(p) for p in history if p]
    else:
        # No esta en la base: se lee de la CADENA. Responder "no lo conozco" cuando el dato
        # esta a una consulta de distancia no le sirve a nadie.
        onchain = await fetch_from_chain(mint)
        if onchain is None:
            return {
                "found": False,
                "mint": mint,
                "detail": (
                    "No se encontro actividad on-chain para este mint. Comprueba que la "
                    "direccion es correcta y que el token existe en Pump.fun."
                ),
            }
        curve = onchain.curve
        prices = onchain.prices
        source = "cadena (en vivo)"
    snapshot = token_snapshot(curve)

    return {
        "found": True,
        "mint": mint,
        "source": source,
        "symbol": token["symbol"] if token else "?",
        "name": token["name"] if token else "(no detectado en vivo)",
        "uri": token["uri"] if token else None,
        "creator": token["creator_address"] if token else None,
        "first_seen_at": (
            token["first_seen_at"].isoformat()
            if token is not None and token["first_seen_at"]
            else None
        ),
        "snapshot": snapshot,
        "history": prices,
        "projection": [
            {
                "seconds_ahead": point.seconds_ahead,
                "percentile": point.percentile,
                "price_sol": point.price_sol,
            }
            for point in project(curve, prices, horizon_seconds=horizon_seconds)
        ],
        "disclaimer": (
            "Proyeccion de percentiles a partir de la curva real y la volatilidad medida. "
            "NO es una prediccion: no hay modelo entrenado. Un cono ancho significa que no "
            "se sabe."
        ),
    }


@app.get("/v1/tokens/{reference}/live")
async def token_live(reference: str, horizon_seconds: int = 4) -> dict[str, Any]:
    """Velas reales + velas proyectadas, servidas DESDE MEMORIA.

    El cliente puede pedir esto cada 500 ms sin coste: un refrescador toca el RPC cada ~1,5 s
    y esta ruta solo lee el cache. Sin ese desacoplo, un cliente rapido generaria docenas de
    llamadas por segundo y el endpoint publico cortaria en un minuto.

    `projected: true` marca las velas del cono. NO son una prediccion: no hay modelo
    entrenado. Una vela proyectada ancha significa incertidumbre, no movimiento esperado.
    """
    mint = _extract_mint(reference)
    tracker: LiveTracker = app.state.tracker
    state = tracker.watch(mint)

    # Primera visita: se espera al primer refresco para no devolver un vacio enganoso.
    if not state.events and not state.error:
        for _ in range(24):
            await asyncio.sleep(0.25)
            if state.events or state.error:
                break

    real = build_candles(state.events)
    projected = project_candles(real, seconds_ahead=horizon_seconds)
    curve = state.curve
    snapshot = token_snapshot(curve) if curve else None
    volatility = realized_volatility_per_second(real)

    payload: dict[str, Any] = {
        "mint": mint,
        "candles": [c.as_dict() for c in real],
        "projected": [c.as_dict() for c in projected],
        "trades": len(state.events),
        "volatility_per_second": round(volatility, 8),
        "refresh_ms": round(state.refresh_ms, 1),
        "error": state.error or None,
        "disclaimer": (
            "Las velas marcadas projected son un cono de percentiles derivado de la "
            "volatilidad medida (Garman-Klass), NO una prediccion: no hay modelo entrenado."
        ),
    }

    if snapshot is not None and curve is not None:
        # Cifras COMPLETAS, sin notacion cientifica: un precio de 1e-14 redondeado es cero,
        # y un cero no sirve para nada.
        payload["snapshot"] = {
            **snapshot,
            "price_sol_exact": full_precision(float(snapshot["price_sol"]), 24),  # type: ignore[arg-type]
            "market_cap_sol_exact": full_precision(float(snapshot["market_cap_sol"]), 12),  # type: ignore[arg-type]
            "liquidity_sol_exact": full_precision(float(snapshot["liquidity_sol"]), 12),  # type: ignore[arg-type]
            "virtual_sol_reserves": curve.virtual_sol_reserves,
            "virtual_token_reserves": curve.virtual_token_reserves,
            "invariant_k": str(curve.invariant),
            "buys": sum(1 for e in state.events if e.is_buy),
            "sells": sum(1 for e in state.events if not e.is_buy),
            "unique_traders": len({e.user for e in state.events}),
            "volume_sol": full_precision(
                sum(e.sol_amount for e in state.events) / 1_000_000_000, 9
            ),
        }
    return payload


@app.get("/v1/tokens/{reference}/simulate")
async def token_simulate(
    reference: str, size_sol: float = 0.05, seeds: int = 200, hold_seconds: int = 60
) -> dict[str, Any]:
    """Simula una operacion sobre el token, con costes reales.

    Usa el simulador de Fase 3: latencia en seis etapas, slippage sobre la curva real,
    priority fees, transacciones fallidas, cotizaciones caducadas, MEV y fills parciales.

    Se devuelve la DISTRIBUCION sobre N semillas, no un numero. Si la mediana gana pero el
    percentil 10 arruina, la operacion no es viable por buena que sea su media.
    """
    from datetime import timedelta

    from mit_shared.types import LAMPORTS_PER_SOL
    from mit_simulation import (
        Decision,
        DecisionContext,
        EventDrivenSimulator,
        MarketEvent,
    )

    mint = _extract_mint(reference)
    tracker: LiveTracker = app.state.tracker
    state = tracker.watch(mint)
    if not state.events:
        for _ in range(20):
            await asyncio.sleep(0.25)
            if state.events:
                break
    if len(state.events) < 3:
        return {
            "ok": False,
            "detail": "No hay suficientes operaciones observadas para simular sobre este token.",
        }

    ordered = sorted(state.events, key=lambda e: e.timestamp)
    events = [
        MarketEvent(
            at=datetime.fromtimestamp(e.timestamp, tz=UTC),
            mint=mint,
            curve=CurveState(
                virtual_sol_reserves=max(1, e.virtual_sol_reserves),
                virtual_token_reserves=max(1, e.virtual_token_reserves),
                real_token_reserves=e.virtual_token_reserves // 2,
                token_total_supply=1_000_000_000_000_000,
            ),
        )
        for e in ordered
    ]

    lamports = max(1, int(size_sol * LAMPORTS_PER_SOL))

    def factory() -> Any:
        bought = {"done": False}

        def strategy(context: DecisionContext) -> Decision:
            if context.open_position is None and not bought["done"]:
                bought["done"] = True
                return Decision("buy", lamports=lamports, reason="entrada simulada")
            if context.open_position is not None:
                held = context.now - context.open_position.opened_at
                if held >= timedelta(seconds=hold_seconds):
                    return Decision("sell", reason="salida por tiempo")
            return Decision("hold")

        return strategy

    simulator = EventDrivenSimulator(initial_capital_lamports=10 * LAMPORTS_PER_SOL)
    runs = simulator.monte_carlo(events, factory, seeds=list(range(max(10, min(seeds, 400)))))
    results = [
        (r.final_equity_lamports - r.initial_capital_lamports) / LAMPORTS_PER_SOL for r in runs
    ]
    results.sort()
    trades = [t for r in runs for t in r.trades]

    def percentile(fraction: float) -> float:
        if not results:
            return 0.0
        index = min(len(results) - 1, max(0, round(fraction * len(results)) - 1))
        return results[index]

    failures: dict[str, int] = {}
    for run in runs:
        for status, count in run.failed_fills.items():
            failures[status] = failures.get(status, 0) + count

    return {
        "ok": True,
        "mint": mint,
        "size_sol": size_sol,
        "hold_seconds": hold_seconds,
        "runs": len(runs),
        "trades_closed": len(trades),
        "p10_sol": round(percentile(0.10), 9),
        "median_sol": round(percentile(0.50), 9),
        "p90_sol": round(percentile(0.90), 9),
        "worst_sol": round(results[0], 9) if results else 0.0,
        "best_sol": round(results[-1], 9) if results else 0.0,
        "losing_runs": sum(1 for value in results if value < 0),
        "avg_entry_slippage_bps": (
            round(sum(t.entry_slippage_bps for t in trades) / len(trades), 1) if trades else 0.0
        ),
        "avg_latency_ms": (
            round(sum(t.entry_latency_ms for t in trades) / len(trades), 1) if trades else 0.0
        ),
        "fees_sol": (
            round(sum(t.fees_lamports for t in trades) / LAMPORTS_PER_SOL / max(1, len(runs)), 9)
        ),
        "stuck_positions": sum(r.stuck_positions for r in runs),
        "failures": failures,
        "disclaimer": (
            "Simulacion con costes reales: latencia de seis etapas, slippage sobre la curva, "
            "fees, fallos y MEV. Es una DISTRIBUCION, no una promesa. Ninguna orden se envia."
        ),
    }
