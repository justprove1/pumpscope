"""Calculo de metricas. Todo sale de datos medidos, nada esta inventado.

La pieza no obvia es la matematica de la curva: el umbral de graduacion NO son
"$69.000". Esa cifra es un artefacto de cuando SOL valia ~$168. El umbral real
esta fijado en SOL y se deriva de la invariante x*y=k de la propia curva, asi
que se recalcula desde las reservas en vivo del token.
"""

import math
import time

# Unica constante del protocolo: toda curva de pump.fun arranca con 30 SOL
# virtuales. El resto (tokens iniciales, umbral de graduacion) se deriva.
VSOL0 = 30.0


def _safe(d, key, default=0.0):
    try:
        v = d.get(key)
        return default if v is None else float(v)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------
# Estado de la curva de bonding
# --------------------------------------------------------------------------

def curve_state(coin):
    """Deriva precio, progreso y precio de graduacion desde las reservas reales."""
    dec = int(coin.get("base_decimals") or 6)
    tok = float(10 ** dec)

    vsol = _safe(coin, "virtual_sol_reserves") / 1e9
    vtok = _safe(coin, "virtual_token_reserves") / tok
    rtok = _safe(coin, "real_token_reserves") / tok
    rsol = _safe(coin, "real_sol_reserves") / 1e9
    supply = _safe(coin, "total_supply") / tok

    st = {
        "complete": bool(coin.get("complete")),
        "vsol": vsol, "vtok": vtok, "rtok": rtok, "rsol": rsol,
        "supply": supply,
        "price_sol": None, "grad_price_sol": None, "grad_mcap_sol": None,
        "progress": None, "sol_to_grad": None, "x_to_grad": None,
    }
    if vsol <= 0 or vtok <= 0:
        return st

    st["price_sol"] = vsol / vtok
    k = vsol * vtok

    # El "offset virtual" (vtok - rtok) es constante durante toda la curva:
    # ambas reservas bajan a la par segun se venden tokens. En la graduacion
    # rtok llega a 0, luego vtok_final == ese offset.
    vtok_end = vtok - rtok
    if vtok_end <= 0:
        return st

    # Ya graduado: las reservas quedan congeladas y proyectar la curva no
    # significa nada. Solo se marca el progreso como completo.
    if st["complete"]:
        st["progress"] = 1.0
        return st

    vsol_end = k / vtok_end
    st["grad_price_sol"] = vsol_end / vtok_end
    st["grad_mcap_sol"] = supply * st["grad_price_sol"] if supply else None

    # Tokens vendibles al inicio, deducidos de la invariante y de VSOL0.
    vtok0 = k / VSOL0
    rtok0 = vtok0 - vtok_end
    if rtok0 > 0:
        st["progress"] = max(0.0, min(1.0, (rtok0 - rtok) / rtok0))
    st["sol_to_grad"] = max(0.0, (vsol_end - VSOL0) - rsol)
    if st["price_sol"] > 0:
        st["x_to_grad"] = st["grad_price_sol"] / st["price_sol"]
    return st


# --------------------------------------------------------------------------
# Volatilidad y estructura de precio
# --------------------------------------------------------------------------

def _stdev(xs):
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def candles_from_trades(trades, bucket_s):
    """Construye velas OHLCV agregando trades.

    GeckoTerminal solo publica velas de 1 minuto hacia arriba, asi que un token
    de 5 minutos de vida devuelve 3 o 4 velas: muestra inservible. Como si
    tenemos los ~300 trades individuales, reconstruimos las velas nosotros con
    el bucket que haga falta.
    """
    pts = [t for t in trades if t.get("price", 0) > 0 and t.get("ts")]
    if len(pts) < 4:
        return []

    buckets = {}
    for t in pts:
        b = int(t["ts"] // bucket_s) * bucket_s
        c = buckets.get(b)
        if c is None:
            buckets[b] = {"t": b, "o": t["price"], "h": t["price"],
                          "l": t["price"], "c": t["price"], "v": t["usd"]}
        else:
            c["h"] = max(c["h"], t["price"])
            c["l"] = min(c["l"], t["price"])
            c["c"] = t["price"]
            c["v"] += t["usd"]
    return [buckets[k] for k in sorted(buckets)]


def garman_klass(candles):
    """Volatilidad por vela usando O/H/L/C en vez de solo cierres.

    El estimador cierre-a-cierre tira a la basura el recorrido intravela: dos
    velas con el mismo cierre cuentan igual aunque una haya oscilado un 40%.
    Garman-Klass usa el rango alto-bajo y la apertura, y es del orden de 7 veces
    mas eficiente con la misma muestra. En un memecoin, donde las velas tienen
    mechas enormes, la diferencia no es cosmetica.

        sigma^2 = 0,5·ln(H/L)^2 − (2·ln2 − 1)·ln(C/O)^2
    """
    vals = []
    for c in candles:
        h, l, o, cl = c["h"], c["l"], c["o"], c["c"]
        if min(h, l, o, cl) <= 0 or h < l:
            continue
        hl = math.log(h / l)
        co = math.log(cl / o)
        v = 0.5 * hl * hl - (2.0 * math.log(2.0) - 1.0) * co * co
        if v > 0:
            vals.append(v)
    if len(vals) < 3:
        return 0.0
    return math.sqrt(sum(vals) / len(vals))


def bootstrap_horizon(returns, horizon, draws=2000, block=5, seed=12345):
    """Distribucion del retorno acumulado a `horizon` velas, por remuestreo.

    Asumir una lognormal simetrica es justo el error que hace que los objetivos
    de un memecoin salgan mal: su distribucion de retornos es asimetrica y de
    colas gordas. Aqui se remuestrean bloques de los retornos observados del
    propio token -- por bloques, no sueltos, para conservar la autocorrelacion
    (las rafagas van seguidas) -- y se acumulan hasta el horizonte.

    Devuelve la lista ordenada de retornos logaritmicos simulados, de la que
    salen cuantiles reales en vez de multiplos de sigma.

    El generador es determinista (congruencial lineal con semilla fija) para que
    dos ejecuciones seguidas del mismo token den el mismo resultado.
    """
    n = len(returns)
    if n < 30 or horizon < 1:
        return []

    horizon = int(min(horizon, 20000))
    state = seed
    out = []
    for _ in range(draws):
        total = 0.0
        left = horizon
        while left > 0:
            state = (1103515245 * state + 12345) & 0x7FFFFFFF
            start = state % n
            take = block if left >= block else left
            for j in range(take):
                total += returns[(start + j) % n]
            left -= take
        out.append(total)
    out.sort()
    return out


def quantile(sorted_vals, q):
    """Cuantil por interpolacion lineal sobre una lista ya ordenada."""
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def tail_median(sorted_vals, lo_q=None, hi_q=None):
    """Mediana de una cola. Responde a 'si rompe, ¿hasta donde suele llegar?'."""
    if not sorted_vals:
        return None
    n = len(sorted_vals)
    if hi_q is not None:      # cola inferior: valores por debajo de hi_q
        seg = sorted_vals[:max(1, int(hi_q * n))]
    else:                     # cola superior
        seg = sorted_vals[min(n - 1, int(lo_q * n)):]
    if not seg:
        return None
    return seg[len(seg) // 2]


def hurst(returns, max_lag=8):
    """Exponente de escalado de la volatilidad, por varianza agregada.

    Es la pieza que evita mentir en los objetivos. Escalar con sqrt(t) asume
    que los retornos son independientes; en un memecoin no lo son -- hay
    rafagas cortas y violentas seguidas de reversion. Agregando los retornos
    en bloques de tamaño m y midiendo como cae la desviacion tipica, la
    pendiente de log(sigma_m) frente a log(m) da el exponente real.

        H = 0,5  -> difusion clasica (sqrt del tiempo)
        H < 0,5  -> reversion a la media: extrapolar con sqrt exagera
        H > 0,5  -> tendencia persistente

    Con muestra insuficiente devuelve 0,5, que es no asumir nada extra.
    """
    n = len(returns)
    if n < 24:
        return 0.5, False

    xs, ys = [], []
    for m in range(1, max_lag + 1):
        if n // m < 8:
            break
        blocks = [sum(returns[i:i + m]) for i in range(0, (n // m) * m, m)]
        sd = _stdev(blocks)
        if sd <= 0:
            continue
        xs.append(math.log(m))
        ys.append(math.log(sd))

    if len(xs) < 3:
        return 0.5, False

    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    den = sum((x - mx) ** 2 for x in xs)
    if den <= 0:
        return 0.5, False
    slope = sum((xs[i] - mx) * (ys[i] - my) for i in range(len(xs))) / den
    return max(0.30, min(0.65, slope)), True


def price_stats(candles):
    """Volatilidad realizada, ATR y momentum, medidos sobre las velas."""
    out = {
        "n": len(candles), "last": None, "sigma": 0.0, "atr_pct": 0.0,
        "ret_recent": 0.0, "ret_z": 0.0, "overext": 0.0,
        "ath": None, "dd_from_ath": 0.0, "vol_recent": 0.0, "vol_prior": 0.0,
        "hurst": 0.5, "hurst_fitted": False,
    }
    if not candles:
        return out

    closes = [c["c"] for c in candles if c["c"] > 0]
    if not closes:
        return out
    out["last"] = closes[-1]

    rets = [math.log(closes[i] / closes[i - 1])
            for i in range(1, len(closes)) if closes[i - 1] > 0 and closes[i] > 0]
    # Se prefiere Garman-Klass; el cierre-a-cierre queda de respaldo cuando las
    # velas no traen rango util (p.ej. reconstruidas con pocos trades).
    out["sigma_cc"] = _stdev(rets)
    gk = garman_klass(candles)
    out["sigma_gk"] = gk
    out["sigma"] = gk if gk > 0 else out["sigma_cc"]
    out["sigma_source"] = "Garman-Klass" if gk > 0 else "cierre-a-cierre"
    out["returns"] = rets
    out["hurst"], out["hurst_fitted"] = hurst(rets)

    # ATR normalizado: rango medio por vela en % del precio.
    trs = []
    for i, c in enumerate(candles):
        prev_close = candles[i - 1]["c"] if i else c["o"]
        tr = max(c["h"] - c["l"], abs(c["h"] - prev_close), abs(c["l"] - prev_close))
        if c["c"] > 0:
            trs.append(tr / c["c"])
    if trs:
        out["atr_pct"] = sum(trs[-30:]) / len(trs[-30:])

    # Momentum de las ultimas ~12 velas, expresado en sigmas (z-score) para que
    # sea comparable entre tokens con volatilidades muy distintas.
    look = min(12, len(closes) - 1)
    if look > 0 and closes[-1 - look] > 0:
        out["ret_recent"] = math.log(closes[-1] / closes[-1 - look])
        denom = out["sigma"] * math.sqrt(look)
        out["ret_z"] = out["ret_recent"] / denom if denom > 0 else 0.0

    # Sobreextension: distancia a la media movil, en sigmas. Detecta velas
    # parabolicas donde la reversion es el escenario dominante.
    ma_n = min(20, len(closes))
    ma = sum(closes[-ma_n:]) / ma_n
    if ma > 0 and out["sigma"] > 0:
        out["overext"] = math.log(closes[-1] / ma) / (out["sigma"] * math.sqrt(ma_n))

    highs = [c["h"] for c in candles if c["h"] > 0]
    if highs:
        out["ath"] = max(highs)
        if out["ath"] > 0:
            out["dd_from_ath"] = (closes[-1] - out["ath"]) / out["ath"]

    half = max(1, len(candles) // 4)
    out["vol_recent"] = sum(c["v"] for c in candles[-half:])
    out["vol_prior"] = sum(c["v"] for c in candles[-4 * half:-half]) or 0.0
    return out


# --------------------------------------------------------------------------
# Flujo de ordenes: quien compra, quien vende, y si el dev esta saliendo
# --------------------------------------------------------------------------

def flow_stats(trades, creator=None, now=None):
    """Analiza los ultimos trades wallet por wallet."""
    now = now or time.time()
    out = {
        "n": len(trades), "buys": 0, "sells": 0,
        "buy_usd": 0.0, "sell_usd": 0.0, "imbalance": 0.0,
        "wallets": 0, "hhi": 0.0, "top_wallet_share": 0.0,
        "dev_sold_usd": 0.0, "dev_bought_usd": 0.0, "dev_active": False,
        "rate_recent": 0.0, "rate_prior": 0.0, "decay": 0.0,
        "window_s": 0,
        "buyers": 0, "sellers": 0, "both": 0,
        "buyer_share": 0.5, "buy_usd_share": 0.5,
        "avg_buy": 0.0, "avg_sell": 0.0,
    }
    if not trades:
        return out

    by_wallet = {}
    for t in trades:
        if t["kind"] == "buy":
            out["buys"] += 1
            out["buy_usd"] += t["usd"]
        elif t["kind"] == "sell":
            out["sells"] += 1
            out["sell_usd"] += t["usd"]
        by_wallet[t["wallet"]] = by_wallet.get(t["wallet"], 0.0) + t["usd"]
        if creator and t["wallet"] == creator:
            out["dev_active"] = True
            if t["kind"] == "sell":
                out["dev_sold_usd"] += t["usd"]
            else:
                out["dev_bought_usd"] += t["usd"]

    total_usd = out["buy_usd"] + out["sell_usd"]
    if total_usd > 0:
        out["imbalance"] = (out["buy_usd"] - out["sell_usd"]) / total_usd

    out["wallets"] = len(by_wallet)
    if by_wallet and total_usd > 0:
        shares = sorted((v / total_usd for v in by_wallet.values()), reverse=True)
        # Indice Herfindahl: ~0 = muchos participantes, ~1 = una sola wallet
        # moviendo todo el volumen (señal clasica de manipulacion / wash).
        out["hhi"] = sum(s * s for s in shares)
        out["top_wallet_share"] = shares[0]

    # Barrera compradores / vendedores.
    #
    # Se mide en wallets distintas, no en numero de operaciones: 300 compras de
    # un mismo bot no son 300 compradores. La divergencia entre ambas lecturas
    # es en si misma la señal -- muchas operaciones repartidas entre pocas
    # wallets es acumulacion artificial.
    buyers, sellers = set(), set()
    for t in trades:
        if t["kind"] == "buy":
            buyers.add(t["wallet"])
        elif t["kind"] == "sell":
            sellers.add(t["wallet"])
    out["buyers"] = len(buyers)
    out["sellers"] = len(sellers)
    out["both"] = len(buyers & sellers)     # wallets que entran y salen: rotadores
    tot_w = out["buyers"] + out["sellers"]
    out["buyer_share"] = (out["buyers"] / tot_w) if tot_w else 0.5
    tot_usd2 = out["buy_usd"] + out["sell_usd"]
    out["buy_usd_share"] = (out["buy_usd"] / tot_usd2) if tot_usd2 else 0.5
    out["avg_buy"] = (out["buy_usd"] / out["buys"]) if out["buys"] else 0.0
    out["avg_sell"] = (out["sell_usd"] / out["sells"]) if out["sells"] else 0.0

    t0, t1 = trades[0]["ts"], trades[-1]["ts"]
    span = max(1, t1 - t0)
    out["window_s"] = span
    mid = t0 + span / 2.0
    recent = [t for t in trades if t["ts"] >= mid]
    prior = [t for t in trades if t["ts"] < mid]
    half_span = max(1.0, span / 2.0)
    out["rate_recent"] = len(recent) / half_span
    out["rate_prior"] = len(prior) / half_span
    if out["rate_prior"] > 0:
        out["decay"] = (out["rate_recent"] - out["rate_prior"]) / out["rate_prior"]
    return out


# --------------------------------------------------------------------------

def multiframe(ds_pair):
    """Coherencia del movimiento entre ventanas (m5, h1, h6, h24).

    Una subida en la que las cuatro ventanas apuntan arriba es una tendencia;
    una en la que h6 esta muy arriba pero m5 ya gira es un techo formandose.
    Distinguirlas es la diferencia entre entrar y quedarse pillado.
    """
    out = {"m5": None, "h1": None, "h6": None, "h24": None,
           "align": 0.0, "fading": 0.0, "have": False}
    if not ds_pair:
        return out
    ch = ds_pair.get("priceChange") or {}
    vals = {}
    for k in ("m5", "h1", "h6", "h24"):
        try:
            v = ch.get(k)
            if v is not None:
                vals[k] = float(v)
                out[k] = vals[k]
        except (TypeError, ValueError):
            pass
    if len(vals) < 2:
        return out
    out["have"] = True

    pos = sum(1 for v in vals.values() if v > 0)
    # -1 (todo abajo) .. +1 (todo arriba)
    out["align"] = (2.0 * pos / len(vals)) - 1.0

    # Agotamiento: las ventanas largas suben pero las cortas ya se giran.
    long_up = (vals.get("h6") or vals.get("h24") or 0.0)
    short = vals.get("m5")
    if long_up > 20 and short is not None and short < 0:
        out["fading"] = min(2.0, (long_up / 100.0) + (abs(short) / 10.0))
    return out


def liquidity_stats(ds_pair, gt_pool):
    out = {"liq_usd": 0.0, "vol24_usd": 0.0, "turnover": 0.0,
           "txns_h1": 0, "price_usd": None, "fdv": None}
    if ds_pair:
        out["liq_usd"] = _safe(ds_pair.get("liquidity") or {}, "usd")
        out["vol24_usd"] = _safe(ds_pair.get("volume") or {}, "h24")
        h1 = (ds_pair.get("txns") or {}).get("h1") or {}
        out["txns_h1"] = int(_safe(h1, "buys") + _safe(h1, "sells"))
        out["price_usd"] = _safe(ds_pair, "priceUsd") or None
        out["fdv"] = _safe(ds_pair, "fdv") or None
    if (not out["liq_usd"]) and gt_pool:
        a = gt_pool.get("attributes", {})
        out["liq_usd"] = _safe(a, "reserve_in_usd")
        out["vol24_usd"] = _safe(a.get("volume_usd") or {}, "h24")
        out["price_usd"] = out["price_usd"] or _safe(a, "base_token_price_usd") or None
        out["fdv"] = out["fdv"] or _safe(a, "fdv_usd") or None
    if out["liq_usd"] > 0:
        # Rotacion: volumen 24h / liquidez. Muy alta con pocas wallets es la
        # firma del volumen falso; muy baja significa que el token esta muerto.
        out["turnover"] = out["vol24_usd"] / out["liq_usd"]
    return out


def age_stats(coin, now=None):
    now = now or time.time()
    created = _safe(coin, "created_timestamp") / 1000.0
    last = _safe(coin, "last_trade_timestamp") / 1000.0
    return {
        "created": created or None,
        "age_h": (now - created) / 3600.0 if created else None,
        "since_trade_min": (now - last) / 60.0 if last else None,
    }
