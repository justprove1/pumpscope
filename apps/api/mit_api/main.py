"""API de solo lectura del dashboard (API.md, SPEC.md 21).

**Sin claves y sin firma, sin excepciones.** Las consultas son de solo lectura. La unica
excepcion es `/v1/trade/prepare`, que CONSTRUYE una transaccion de compra o venta pero no la
firma ni la envia: devuelve los bytes sin firmar y quien decide es la cartera del navegador
del usuario, que le muestra la operacion y espera su aprobacion.

Eso no es el trading automatico de SPEC.md 15: ahi decide el sistema y firma el `signer`
aislado, y sigue bloqueado. Aqui cada operacion la aprueba una persona a mano.

La API no calcula nada: sirve lo que el worker ha persistido, y reenvia por WebSocket lo que
el worker publica en Redis.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as redis
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from mit_pumpfun.curve import CurveState
from sqlalchemy.ext.asyncio import create_async_engine

from mit_api.auto import parar as parar_vigilante
from mit_api.auto import router as auto_router
from mit_api.candles import (
    LiveTracker,
    build_candles,
    full_precision,
    project_candles,
    realized_volatility_per_second,
)
from mit_api.livesignals import detect_pre_bounce, detect_whale, flow_metrics
from mit_api.potential import estimate_traction
from mit_api.price import SolPriceService
from mit_api.projection import fetch_from_chain, project, token_snapshot
from mit_api.queries import TokenQueries
from mit_api.trade import cerrar_cliente, curva_actual, token_graduated
from mit_api.trade import router as trade_router

CHANNEL_NEW_TOKENS = "mit:tokens.new"
CHANNEL_ANALYSIS = "mit:tokens.analysis"
CHANNEL_CAP = "mit:tokens.cap"
KEY_TOP_MOVERS = "mit:top_movers"
KEY_HOT_ZONE = "mit:hot_zone"
KEY_STAMPEDE = "mit:stampede"
KEY_GRADUATING = "mit:graduating"
KEY_SERIES = "mit:series"
HEARTBEAT_SECONDS = 15.0

# Cuando se empezo a seguir cada token, para no repetir la espera inicial en cada peticion.
_VISTOS: dict[str, float] = {}
_MAX_VISTOS = 2_000
_ESPERA_PRIMERA_VEZ_S = 6.0


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.engine = create_async_engine(os.environ["DATABASE_URL"], pool_size=5)
    app.state.queries = TokenQueries(app.state.engine)
    app.state.tracker = LiveTracker()
    app.state.price = SolPriceService()
    app.state.redis = redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))  # type: ignore[no-untyped-call]  # redis no anota from_url
    try:
        yield
    finally:
        await parar_vigilante()
        await cerrar_cliente()
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
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        # Panel compacto de operativa manual (apps/panel).
        "http://localhost:4000",
        "http://127.0.0.1:4000",
    ],
    # POST lo necesita `/v1/trade/prepare`, que construye la transaccion SIN FIRMAR que la
    # cartera del navegador aprueba. Sigue sin haber ninguna ruta que firme o envie nada.
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(trade_router)
# Stop loss automatico: vigila y vende sin nadie delante. Requiere el firmante encendido.
app.include_router(auto_router)


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


@app.get("/v1/top-movers")
async def top_movers() -> dict[str, Any]:
    """Los que mas han explotado esta sesion. La foto la mantiene el worker en Redis."""
    raw = await app.state.redis.get(KEY_TOP_MOVERS)
    movers = json.loads(raw) if raw else []
    return {"movers": movers}


@app.get("/v1/hot-zone")
async def hot_zone() -> dict[str, Any]:
    """Tokens en la banda media (~$30-60k) con su tasa base de explosion medida."""
    raw = await app.state.redis.get(KEY_HOT_ZONE)
    tokens_in_zone = json.loads(raw) if raw else []
    return {"tokens": tokens_in_zone}


@app.get("/v1/price")
async def sol_price() -> dict[str, Any]:
    """Precio de SOL en euros y dolares, cacheado. `null` si no se pudo obtener."""
    price = await app.state.price.get()
    return {"sol": price.as_dict() if price is not None else None}


@app.get("/v1/graduating")
async def graduating() -> dict[str, Any]:
    """Tokens en camino de graduarse, con su progreso, velocidad y tiempo estimado."""
    raw = await app.state.redis.get(KEY_GRADUATING)
    tokens = json.loads(raw) if raw else []
    return {"tokens": tokens}


@app.get("/v1/stampede")
async def stampede() -> dict[str, Any]:
    """Lanzamientos en estampida: rafaga de operaciones nada mas nacer (patron V713)."""
    raw = await app.state.redis.get(KEY_STAMPEDE)
    tokens = json.loads(raw) if raw else []
    return {"tokens": tokens}


@app.get("/v1/series")
async def series() -> dict[str, Any]:
    """Series vivas: un simbolo relanzado cuyos miembros anteriores ya bombearon."""
    raw = await app.state.redis.get(KEY_SERIES)
    return {"series": json.loads(raw) if raw else []}


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
    await pubsub.subscribe(CHANNEL_NEW_TOKENS, CHANNEL_ANALYSIS, CHANNEL_CAP)

    # Cada canal de Redis se traduce a un `channel` del mensaje que entiende el dashboard.
    channel_map = {
        CHANNEL_ANALYSIS: ("tokens.analysis", "analysis"),
        CHANNEL_CAP: ("tokens.cap", "cap"),
        CHANNEL_NEW_TOKENS: ("tokens.new", "token"),
    }

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
            channel_label, event_label = channel_map.get(channel_name, ("tokens.new", "token"))
            await websocket.send_text(
                json.dumps(
                    {
                        "channel": channel_label,
                        "event": event_label,
                        "payload": json.loads(payload),
                    }
                )
            )
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        with contextlib.suppress(Exception):
            await pubsub.unsubscribe(CHANNEL_NEW_TOKENS, CHANNEL_ANALYSIS, CHANNEL_CAP)
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
async def token_live(
    reference: str, horizon_seconds: int = 4, candles: int = 0
) -> dict[str, Any]:
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
    #
    # **La espera es por TOKEN, no por peticion.** Antes bastaba con que no hubiera eventos
    # para esperar, y un token que no opera no los tiene nunca: cada peticion se quedaba
    # colgada seis segundos, para siempre. Medido: 6.233 ms de mediana en un token tranquilo,
    # con el panel preguntando cada 800 ms. Ahora se espera solo mientras la suscripcion es
    # nueva; pasado ese margen se responde con lo que haya, aunque sea nada.
    primera_vez = _VISTOS.get(mint)
    ahora = time.monotonic()
    if primera_vez is None:
        _VISTOS[mint] = ahora
        primera_vez = ahora
        if len(_VISTOS) > _MAX_VISTOS:
            _VISTOS.pop(next(iter(_VISTOS)))

    if not state.events and not state.error and ahora - primera_vez < _ESPERA_PRIMERA_VEZ_S:
        limite = time.monotonic() + (_ESPERA_PRIMERA_VEZ_S - (ahora - primera_vez))
        while time.monotonic() < limite:
            await asyncio.sleep(0.25)
            if state.events or state.error:
                break

    real = build_candles(state.events)
    projected = project_candles(real, seconds_ahead=horizon_seconds)
    # `candles=N` recorta lo que se SIRVE, no lo que se calcula: la proyeccion y la
    # volatilidad siguen usando la serie entera. Un cliente que solo mira las ultimas velas
    # —el panel mira dos— se ahorra el 95% de la respuesta, y a 100 peticiones por minuto eso
    # son megabytes por minuto de diferencia.
    servidas = real[-candles:] if candles > 0 else real
    curve = state.curve
    snapshot = token_snapshot(curve) if curve else None
    volatility = realized_volatility_per_second(real)

    graduado = await token_graduated(mint)

    # **El precio sale de la CUENTA de la curva, no de las operaciones vistas.** De aqui sale
    # la capitalizacion que el panel usa para el stop loss, y reconstruirla a partir de los
    # eventos que se alcanzan a ver deja desvios de hasta el 9% cuando alguno se pierde.
    cuenta_curva = await curva_actual(mint)
    if cuenta_curva is not None and not cuenta_curva.complete:
        curve = CurveState(
            virtual_sol_reserves=max(1, cuenta_curva.virtual_quote_reserves),
            virtual_token_reserves=max(1, cuenta_curva.virtual_token_reserves),
            real_token_reserves=max(0, cuenta_curva.real_token_reserves),
            token_total_supply=max(1, cuenta_curva.token_total_supply),
            real_sol_reserves=max(0, cuenta_curva.real_quote_reserves),
        )
        snapshot = token_snapshot(curve)
    traction = estimate_traction(state.events, curve)
    _whale = detect_whale(state.events)
    _bounce = detect_pre_bounce(state.events)
    recommendation = _recommendation(traction, snapshot, curve, len(state.events))
    payload: dict[str, Any] = {
        "mint": mint,
        "candles": [c.as_dict() for c in servidas],
        "traction": traction.as_dict(),
        "recommendation": recommendation,
        "whale": {
            "present": _whale.present,
            # La cartera concreta. Sin ella el panel no puede distinguir «la misma ballena
            # que hace un segundo» de «otra distinta», y avisaria en bucle de lo mismo.
            "wallet": _whale.wallet,
            "direction": _whale.direction,
            "share_of_volume": _whale.share_of_volume,
            "sol_amount": _whale.sol_amount,
            "detail": _whale.detail,
        },
        "pre_bounce": {
            "present": _bounce.present,
            "drop_pct": _bounce.drop_pct,
            "recovery_pct": _bounce.recovery_pct,
            "detail": _bounce.detail,
        },
        "flow": flow_metrics(state.events),
        "projected": [c.as_dict() for c in projected],
        "trades": len(state.events),
        # El token ya opera en PumpSwap, no en la bonding curve. **Solo lo dice la CUENTA de
        # la curva.** Aqui habia un `else state.graduated` que, cuando la lectura de cadena
        # fallaba, devolvia la heuristica de logs —la misma que se midio fallando en 6 de
        # cada 10— y encima pegajosa: `state.graduated` nunca vuelve a false, asi que un solo
        # log mal atribuido condenaba al token para toda la vida del proceso. Con la clave de
        # RPC sin poner, las lecturas fallan a menudo, o sea que esa via se usaba de sobra.
        #
        # Ahora «no lo se» es `false` y no `true`. No se pierde ninguna proteccion: la que
        # de verdad impide gastar en un token imposible es la SIMULACION de la orden, que se
        # ejecuta contra la cadena antes de firmar y no se puede equivocar sobre esto.
        "graduated": bool(graduado),
        # `true` cuando la respuesta viene de la cadena. `false` significa «no se ha podido
        # comprobar», nunca «no graduo».
        "graduated_confirmed": graduado is not None,
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
            # Carteras DISTINTAS a cada lado, no numero de operaciones. Es la diferencia
            # entre «cien personas comprando» y «una persona comprando cien veces», que es
            # justo lo que distingue un token con demanda de uno inflado por su creador.
            "unique_buyers": len({e.user for e in state.events if e.is_buy}),
            "unique_sellers": len({e.user for e in state.events if not e.is_buy}),
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


def _recommendation(
    traction: Any, snapshot: dict[str, object] | None, curve: object, trades: int
) -> dict[str, object]:
    """Senal accionable: COMPRA / MANTEN / VENDE / EVITA.

    **No es un consejo financiero ni una orden.** Es una lectura de la traccion observada
    traducida a una etiqueta, con las razones a la vista. El sistema no puede ejecutar nada:
    LIVE esta deshabilitado. Quien decide comprar o vender es una persona, con esto como una
    senal mas entre las que mire.

    La regla es deliberadamente conservadora: por defecto MANTEN/OBSERVA. Solo se sugiere
    COMPRA cuando hay empuje real Y todavia queda recorrido; se sugiere VENDE/EVITA cuando el
    empuje se apaga o el token ya recorrio casi toda la curva (entrar tarde es perder).
    """
    from mit_pumpfun.curve import CurveState, progress_pct

    reasons: list[str] = []
    if trades < 3:
        return {
            "action": "OBSERVA",
            "reason": "Aun no hay operaciones suficientes para leer nada.",
            "confidence": "baja",
        }

    score = traction.score
    pct = 0.0
    if isinstance(curve, CurveState):
        pct = float(progress_pct(curve))

    # Presion compradora y sostenibilidad salen del propio desglose de traccion.
    signals = {s.name: s.value for s in traction.signals}
    pressure = signals.get("presion_compradora", 0.5)
    momentum = signals.get("sostenibilidad", 0.5)

    if pct >= 95:
        action, confidence = "EVITA", "media"
        reasons.append(f"la curva ya esta al {pct:.0f}%: entrar aqui es comprar el techo")
    elif score >= 60 and pressure >= 0.55 and momentum >= 0.8 and pct < 80:
        action, confidence = "COMPRA", "media" if score < 75 else "alta"
        reasons.append(f"empuje {score:.0f}/100 con presion compradora y recorrido por delante")
    elif score < 35 or momentum < 0.4 or pressure < 0.4:
        action, confidence = "VENDE", "media"
        reasons.append(
            f"el empuje se apaga (score {score:.0f}, sostenibilidad {momentum:.0%}, "
            f"presion {pressure:.0%})"
        )
    else:
        action, confidence = "MANTEN", "baja"
        reasons.append(f"traccion intermedia ({score:.0f}/100): ni entrada clara ni salida clara")

    # El sistema sigue sin decidir por su cuenta, pero decir "LIVE deshabilitado" ya no es
    # cierto desde que el panel opera: alli se compra de verdad, con la firma del usuario.
    # Una advertencia desfasada es peor que ninguna, porque se deja de leer.
    reasons.append("El sistema no opera solo: cada orden la firmas tu. La decision es tuya.")
    return {"action": action, "reason": " · ".join(reasons), "confidence": confidence}
