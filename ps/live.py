"""Precio en vivo.

El analisis completo tarda 10-20s porque GeckoTerminal limita a 30 peticiones
por minuto. El precio, en cambio, puede ir a 1 Hz: pump.fun aguanta ese ritmo
sin throttling y responde en ~200ms (medido).

De ahi la separacion en dos carriles:
  - carril rapido (este modulo): una sola llamada a /coins/{mint}, ~200ms
  - carril lento (ps.analyze):   velas, trades y modelo, cada varios minutos

Asi el precio y el progreso de la curva se mueven en directo mientras los
escenarios se mantienen hasta el siguiente recalculo.
"""

import time

from . import features, sources, whale

# Reservas iniciales de toda curva de pump.fun, para el % de progreso.
_VSOL0 = 30.0


def tick(mint):
    """Una lectura ligera del estado. Lanza SourceError si la fuente falla."""
    c = sources.pump_coin(mint)
    dec = int(c.get("base_decimals") or 6)
    tok = float(10 ** dec)

    def f(k):
        try:
            v = c.get(k)
            return 0.0 if v is None else float(v)
        except (TypeError, ValueError):
            return 0.0

    supply = f("total_supply") / tok
    mcap = f("usd_market_cap") or None
    # El precio se deriva de la capitalizacion y la supply, la misma formula que
    # usan los objetivos del analisis, para que todo cuadre en pantalla.
    price = (mcap / supply) if (mcap and supply) else None

    vsol = f("virtual_sol_reserves") / 1e9
    vtok = f("virtual_token_reserves") / tok
    rtok = f("real_token_reserves") / tok
    complete = bool(c.get("complete"))

    progress = 1.0 if complete else None
    if not complete and vsol > 0 and vtok > 0:
        vtok_end = vtok - rtok
        if vtok_end > 0:
            rtok0 = (vsol * vtok) / _VSOL0 - vtok_end
            if rtok0 > 0:
                progress = max(0.0, min(1.0, (rtok0 - rtok) / rtok0))

    return {
        "ts": time.time(),
        "price": price,
        "mcap": mcap,
        "progress": progress,
        "complete": complete,
        "sol_reserves": f("real_sol_reserves") / 1e9,
        "last_trade_ms": f("last_trade_timestamp") or None,
        # el consumidor los necesita para los carriles lentos
        "_pool": c.get("pool_address") if complete else c.get("bonding_curve"),
        "_creator": c.get("creator"),
    }


def ds_flow(mint):
    """Carril medio (~8s): compras/ventas por ventana, desde DexScreener.

    DexScreener aguanta este ritmo de sobra y da el pulso inmediato de los
    ultimos 5 minutos. Son operaciones, no wallets: para eso esta gt_flow.
    """
    pair = sources.ds_token(mint)
    if not pair:
        return {}
    tx = pair.get("txns") or {}
    ch = pair.get("priceChange") or {}
    vol = pair.get("volume") or {}

    def num(d, k):
        try:
            v = d.get(k)
            return 0.0 if v is None else float(v)
        except (TypeError, ValueError):
            return 0.0

    out = {"ds_ts": time.time(), "ventanas": {}}
    for w in ("m5", "h1", "h6", "h24"):
        b = num(tx.get(w) or {}, "buys")
        s = num(tx.get(w) or {}, "sells")
        out["ventanas"][w] = {
            "buys": int(b), "sells": int(s),
            "share": (b / (b + s)) if (b + s) else None,
            "chg": num(ch, w) if ch.get(w) is not None else None,
            "vol": num(vol, w),
        }
    try:
        out["liq"] = float((pair.get("liquidity") or {}).get("usd") or 0)
    except (TypeError, ValueError):
        pass
    return out


def gt_flow(pool, creator=None, liq_usd=None, horizonte_h=None, ctx=None):
    """Carril lento (~25s): la barrera completa, con wallets unicas.

    Es la unica lectura que distingue 'muchas operaciones' de 'muchas manos',
    y la unica que detecta al creador vendiendo. Cuesta una peticion a
    GeckoTerminal, de ahi que vaya en el carril mas lento.
    """
    if not pool:
        return {}
    trades = sources.gt_trades(pool)
    if not trades:
        return {}
    f = features.flow_stats(trades, creator=creator)
    out = {
        "gt_ts": time.time(),
        "flujo": {
            "trades": f["n"], "buys": f["buys"], "sells": f["sells"],
            "buyers": f["buyers"], "sellers": f["sellers"], "both": f["both"],
            "buyer_share": f["buyer_share"], "buy_usd_share": f["buy_usd_share"],
            "buy_usd": f["buy_usd"], "sell_usd": f["sell_usd"],
            "avg_buy": f["avg_buy"], "avg_sell": f["avg_sell"],
            "wallets": f["wallets"], "hhi": f["hhi"],
            "dev_sold_usd": f["dev_sold_usd"], "dev_bought_usd": f["dev_bought_usd"],
            "decay": f["decay"],
        },
    }
    # La probabilidad de ballena se recalcula con los trades frescos: es la
    # cifra que mas se mueve cuando cambia el ritmo del mercado.
    if liq_usd and horizonte_h:
        c = dict(ctx or {})
        c.setdefault("imbalance", f["imbalance"])
        w = whale.analiza(trades, liq_usd, horizonte_h, c)
        if w.get("niveles"):
            out["ballenas"] = w
    return out
