"""Orquestador: recoge datos, calcula metricas y arma los 3 escenarios."""

import math
import time

from . import features, levels, model, setup as setup_mod, sources, whale

# Resolucion de vela segun la edad del token: uno de 20 minutos necesita velas
# de 1m para tener muestra; uno de 3 dias se lee mejor en 15m.
def _money(x):
    if x is None:
        return "n/d"
    ax = abs(x)
    if ax >= 1_000_000:
        return "$%.2fM" % (x / 1_000_000)
    if ax >= 1_000:
        return "$%.1fk" % (x / 1_000)
    return "$%.2f" % x


def _fmt_h(h):
    if h is None:
        return "n/d"
    if h < 1:
        return "%.0f min" % (h * 60)
    if h < 48:
        return "%.1f h" % h
    return "%.1f dias" % (h / 24.0)


def _timeframe_for(age_h):
    if age_h is None or age_h < 3:
        return ("minute", 1, 1)
    if age_h < 24:
        return ("minute", 5, 5)
    if age_h < 24 * 7:
        return ("minute", 15, 15)
    return ("hour", 1, 60)


def analyze(mint, horizon_h=6.0):
    now = time.time()
    warn = []
    sources.take_errors()   # arranca con el acumulador limpio

    try:
        coin = sources.pump_coin(mint)
    except sources.SourceError as e:
        if "404" in str(e):
            raise sources.SourceError(
                "pump.fun no conoce el mint %s.\n"
                "   Comprueba el link: puede ser un token de otra plataforma "
                "(Raydium, Moonshot...) o estar mal copiado." % mint)
        raise
    if not coin or not coin.get("mint"):
        raise sources.SourceError("pump.fun no devolvio datos para ese mint")

    sol_usd = sources.sol_price_usd()
    curve = features.curve_state(coin)
    age = features.age_stats(coin, now)

    # pump.fun ya nos dice donde cotiza el token, y esa direccion coincide con
    # la que indexa GeckoTerminal (verificado). Usarla evita una llamada de tres
    # a una API con limite de 30/min, que es el modo de fallo mas habitual.
    gt_pool = None
    pool_addr = coin.get("pool_address") if coin.get("complete") else coin.get("bonding_curve")
    if not pool_addr:
        pools = sources.gt_pools_for_token(mint)
        gt_pool = pools[0] if pools else None
        if gt_pool:
            pool_addr = gt_pool.get("attributes", {}).get("address")
    if not pool_addr:
        warn.append("No se pudo determinar el pool del token.")

    tf_unit, tf_agg, tf_min = _timeframe_for(age.get("age_h"))
    candles = sources.gt_ohlcv(pool_addr, tf_unit, tf_agg, limit=300) if pool_addr else []
    if len(candles) < 10 and tf_min > 1:
        alt = sources.gt_ohlcv(pool_addr, "minute", 1, limit=300) if pool_addr else []
        if len(alt) > len(candles):
            candles, tf_unit, tf_agg, tf_min = alt, "minute", 1, 1

    # Si el pool que dio pump.fun no esta indexado, se recurre a la busqueda.
    if not candles and pool_addr:
        pools = sources.gt_pools_for_token(mint)
        alt_addr = pools[0].get("attributes", {}).get("address") if pools else None
        if alt_addr and alt_addr != pool_addr:
            gt_pool, pool_addr = pools[0], alt_addr
            candles = sources.gt_ohlcv(pool_addr, tf_unit, tf_agg, limit=300)

    trades = sources.gt_trades(pool_addr) if pool_addr else []
    ds_pair = sources.ds_token(mint)

    # Token demasiado joven para que existan velas de 1m suficientes: las
    # reconstruimos desde los trades individuales, eligiendo el bucket que
    # produzca ~60 velas dentro de la ventana observada.
    tf_label = "%d%s" % (tf_agg, "m" if tf_unit == "minute" else "h")
    if len(candles) < 20 and len(trades) >= 24:
        span = max(1, trades[-1]["ts"] - trades[0]["ts"])
        bucket = max(1, int(span / 60))
        synth = features.candles_from_trades(trades, bucket)
        if len(synth) > len(candles):
            candles = synth
            tf_min = bucket / 60.0
            tf_label = ("%ds*" % bucket) if bucket < 60 else ("%dm*" % (bucket // 60))
            warn.append("Velas reconstruidas desde %d trades (bucket %ds): el token es "
                        "demasiado nuevo para el historico de velas." % (len(trades), bucket))

    stats = features.price_stats(candles)
    flow = features.flow_stats(trades, creator=coin.get("creator"), now=now)
    liq = features.liquidity_stats(ds_pair, gt_pool)

    price = liq.get("price_usd") or stats.get("last")
    if not price and curve.get("price_sol") and sol_usd:
        price = curve["price_sol"] * sol_usd

    if curve.get("grad_price_sol") and sol_usd:
        curve["grad_price_sol_usd"] = curve["grad_price_sol"] * sol_usd
        curve["grad_mcap_usd"] = (curve.get("grad_mcap_sol") or 0) * sol_usd

    for e in sources.take_errors():
        warn.append("Fuente caida: %s — las señales que dependian de ella se anulan." % e)

    if len(candles) < 12:
        warn.append("Pocas velas (%d): la volatilidad medida es poco fiable." % len(candles))
    if flow["n"] < 25:
        warn.append("Pocos trades (%d): el analisis de flujo se atenua proporcionalmente."
                    % flow["n"])
    if liq["liq_usd"] < 3000:
        warn.append("Liquidez muy baja ($%.0f): el slippage puede invalidar los objetivos."
                    % liq["liq_usd"])

    mf = features.multiframe(ds_pair)
    pred = model.predict(curve, stats, flow, liq, age, mf)

    # ---- escenarios -----------------------------------------------------
    # Un token de 4 minutos no permite pronosticar a 6 horas: escalar la
    # volatilidad por un factor de 10.000 produce cifras sin sentido. El
    # horizonte se recorta a lo que la historia observada sostiene (3x el
    # tramo medido) y se le dice al usuario cual se acabo usando.
    span_h = (len(candles) * tf_min) / 60.0
    eff_h = horizon_h
    if span_h > 0:
        eff_h = min(horizon_h, max(span_h * 3.0, 0.25))
    clamped = eff_h < horizon_h * 0.98
    if clamped:
        warn.append("Horizonte recortado de %.0fh a %s: solo hay %s de historico. "
                    "Pedir mas lejos seria inventar." % (
                        horizon_h, _fmt_h(eff_h), _fmt_h(span_h)))

    h_candles = max(1.0, (eff_h * 60.0) / tf_min)
    sigma = stats.get("sigma", 0.0)
    hu = stats.get("hurst", 0.5)
    move = sigma * (h_candles ** hu) if sigma > 0 else 0.0
    # Guarda final: por encima de ~1,6 en log (~5x) el modelo gaussiano ya no
    # dice nada util sobre un memecoin.
    move = min(move, 1.6)

    # --- distribucion del horizonte -------------------------------------
    # Preferimos remuestrear los retornos reales del token antes que asumir una
    # lognormal simetrica: un memecoin sube en rafagas y baja en escalones, y
    # esa asimetria es justo lo que un ±1σ borra. De aqui salen unos objetivos
    # asimetricos por construccion.
    rets = stats.get("returns") or []
    hi_n = int(round(h_candles))
    draws = 2000 if hi_n <= 500 else max(400, int(400000 / max(1, hi_n)))
    boot = features.bootstrap_horizon(rets, hi_n, draws=draws)

    def _cap(r):
        return max(-1.6, min(1.6, r)) if r is not None else None

    if boot:
        # Las bandas se recortan CON las probabilidades del modelo, no en los
        # cuartiles fijos. Fijarlas en 25-75 implicaba una particion 25/50/25
        # que contradecia lo que el modelo reportaba (p.ej. 22/30/49): eran dos
        # respuestas distintas a la misma pregunta puestas una al lado de otra.
        # Cortando la distribucion en P(bajada) y en 1-P(subida), las tres
        # regiones y las tres probabilidades son la misma particion.
        p_dn = pred["probs"][model.DOWN]
        p_up = pred["probs"][model.UP]
        q_lo = min(0.48, max(0.02, p_dn))
        q_hi = max(q_lo + 0.04, min(0.98, 1.0 - p_up))

        band_lo_r = _cap(features.quantile(boot, q_lo))
        band_hi_r = _cap(features.quantile(boot, q_hi))
        # Objetivo = mediana de la cola que queda fuera de la banda: "si rompe
        # por ahi, ¿hasta donde suele llegar?".
        up_r = _cap(features.tail_median(boot, lo_q=q_hi))
        dn_r = _cap(features.tail_median(boot, hi_q=q_lo))
        dist_src = ("bootstrap de %d retornos, cortado en las probabilidades "
                    "del modelo" % len(rets))
    else:
        band_lo_r, band_hi_r = -0.45 * move, 0.45 * move
        up_r, dn_r = move, -move
        dist_src = "lognormal (muestra insuficiente para remuestrear)"
    if move <= 0:
        warn.append("No hay volatilidad medible (sin velas utiles): se dan las "
                    "probabilidades, pero no hay objetivos de precio fiables.")

    sup, res = levels.build_levels(candles, stats, curve, price)

    def _px(r):
        return price * math.exp(r) if (price and r is not None and r != 0) else None

    up_vol, dn_vol = _px(up_r), _px(dn_r)
    up_px, up_src = levels.pick_target(price, up_vol, res, up=True)
    dn_px, dn_src = levels.pick_target(price, dn_vol, sup, up=False)
    band_hi, band_lo = _px(band_hi_r), _px(band_lo_r)

    def pct(target):
        if not target or not price:
            return None
        return (target / price - 1.0) * 100.0

    # Conversion precio -> capitalizacion.
    #
    # Se usa la supply real del token, no el cociente mcap/precio. El campo
    # usd_market_cap de pump.fun sale de su propia lectura de la curva y va
    # desfasado respecto al precio de DexScreener; en un token de 3 minutos ese
    # desfase llegaba al 35% y hacia que el nivel de GRADUACION apareciese en
    # $19,8k cuando la curva graduaba en $29,9k. Con la supply, la capitalizacion
    # de graduacion y la de los niveles salen de la misma formula y cuadran.
    try:
        mcap_api = float(coin.get("usd_market_cap") or 0) or None
    except (TypeError, ValueError):
        mcap_api = None

    ratio = curve.get("supply") or None
    if ratio and price:
        mcap_now = price * ratio
    else:
        mcap_now = mcap_api
        ratio = (mcap_api / price) if (mcap_api and price) else None

    def mcap(target):
        if not target or not ratio:
            return None
        return target * ratio

    scen = [
        {"name": model.UP, "prob": pred["probs"][model.UP],
         "target": up_px, "pct": pct(up_px), "mcap": mcap(up_px), "source": up_src,
         "label": "Continuacion / techo"},
        {"name": model.RANGE, "prob": pred["probs"][model.RANGE],
         "target": None, "pct": None, "source": "volatilidad",
         "lo": band_lo, "hi": band_hi,
         "lo_pct": pct(band_lo), "hi_pct": pct(band_hi),
         "mcap_lo": mcap(band_lo), "mcap_hi": mcap(band_hi),
         "label": "Rango / consolidacion"},
        {"name": model.DOWN, "prob": pred["probs"][model.DOWN],
         "target": dn_px, "pct": pct(dn_px), "mcap": mcap(dn_px), "source": dn_src,
         "label": "Rechazo / rebote fallido"},
    ]

    # Ballenas: probabilidad de que entre (o salga) una compra grande.
    accel = None
    try:
        vv = (ds_pair.get("volume") or {}) if ds_pair else {}
        v1h, v6h = float(vv.get("h1") or 0), float(vv.get("h6") or 0)
        if v6h > 0:
            accel = (v1h * 6.0) / v6h        # ritmo de la ultima hora vs 6h
    except (TypeError, ValueError):
        pass
    ballenas = whale.analiza(trades, liq.get("liq_usd") or 0, eff_h, {
        "accel": accel, "progress": curve.get("progress"),
        "complete": curve.get("complete"), "imbalance": flow.get("imbalance"),
    })

    tl_et, tl_col, tl_txt = model.trend_label(pred, mf)
    motivos = model.reasons(pred)
    for sc_ in scen:
        sc_["motivos"] = motivos.get(sc_["name"], [])

    for lv in sup + res:
        lv["mcap"] = mcap(lv["price"])

    # Para un token aun en curva, el techo en capitalizacion solo significa algo
    # comparado con la capitalizacion de graduacion: dice si el escenario
    # alcista llega a cerrar la curva o se queda a medio camino.
    grad_mc = curve.get("grad_mcap_usd")
    if grad_mc and not curve.get("complete"):
        for s in scen:
            if s["name"] == model.UP and s.get("mcap"):
                frac = s["mcap"] / grad_mc
                if frac >= 1.0:
                    s["mcap_vs_grad"] = "cerraria la curva (graduacion en %s)" % _money(grad_mc)
                else:
                    s["mcap_vs_grad"] = "%.0f%% del camino a graduar (%s)" % (
                        frac * 100, _money(grad_mc))
                s["mcap_grad_frac"] = frac

    res = {
        "mint": mint, "coin": coin, "sol_usd": sol_usd,
        "curve": curve, "age": age, "stats": stats, "flow": flow, "liq": liq,
        "candles": candles, "trades": trades,
        "price": price, "pred": pred, "scenarios": scen, "mf": mf,
        "trend": {"label": tl_et, "color": tl_col, "why": tl_txt},
        "whale": ballenas,
        "mcap_now": mcap_now, "mcap_ratio": ratio,
        "supports": sup, "resistances": res,
        "timeframe": tf_label,
        "tf_min": tf_min,
        "horizon_h": eff_h, "horizon_pedido_h": horizon_h, "horizon_recortado": clamped,
        "horizon_label": _fmt_h(eff_h), "span_h": span_h,
        "expected_move_pct": (math.exp(move) - 1.0) * 100.0 if move else 0.0,
        "dist_source": dist_src, "boot_n": len(boot),
        "sigma_source": stats.get("sigma_source"),
        "skew_pct": (((math.exp(up_r) - 1.0) + (math.exp(dn_r) - 1.0)) * 100.0
                     if (up_r and dn_r) else None),
        "pool": pool_addr, "warnings": warn,
    }
    # Condiciones de entrada: se evaluan sobre el resultado ya montado.
    res["setup"] = setup_mod.evalua(res)
    return res
