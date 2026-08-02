"""Buscador de memecoins en posible tendencia alcista.

La idea no es encontrar lo que MAS ha subido -- eso ya lo hace cualquier
pantalla de trending, y comprar ahi suele ser comprar el techo. Lo que se busca
es lo que esta EMPEZANDO a subir y aun tiene sitio:

  * aceleracion de volumen: el tramo reciente pesa mas que el anterior
  * compradores unicos creciendo, no solo numero de operaciones
  * movimiento positivo pero NO parabolico (>+300% en 1h se penaliza: es tarde)
  * las ventanas cortas confirman a las largas, sin girarse todavia
  * liquidez suficiente para poder salir

Se apoya en /trending_pools y /new_pools de GeckoTerminal, que devuelven 20
pools por peticion con momentum multi-ventana, volumen por tramos y
compradores/vendedores unicos. Es decir: 20 candidatos puntuables por llamada,
sin gastar el limite de peticiones en consultas individuales.
"""

import math
import time

from . import sources

GT = sources.GT_API


def _f(d, k, default=0.0):
    try:
        v = d.get(k)
        return default if v is None else float(v)
    except (TypeError, ValueError):
        return default


def _fetch(path):
    try:
        return sources.get_json("%s/%s" % (GT, path)).get("data", []) or []
    except sources.SourceError as e:
        sources._soft_errors.append("busqueda: %s (%s)" % (path.split("?")[0], e))
        return []


def candidates(pages=1):
    """Reune pools candidatas de varias ventanas, deduplicadas por direccion."""
    seen, out = set(), []
    paths = [
        "networks/solana/trending_pools?page=1&duration=5m",
        "networks/solana/trending_pools?page=1&duration=1h",
        "networks/solana/trending_pools?page=1&duration=6h",
        "networks/solana/new_pools?page=1",
    ]
    for p in paths[:2 + pages]:
        for pool in _fetch(p):
            addr = pool.get("attributes", {}).get("address")
            if addr and addr not in seen:
                seen.add(addr)
                out.append(pool)
    return out


def score(pool, now=None):
    """Puntua una pool. Devuelve None si no cumple los minimos."""
    now = now or time.time()
    a = pool.get("attributes", {})
    rel = pool.get("relationships", {})
    dex = (rel.get("dex", {}).get("data", {}) or {}).get("id", "")
    base = (rel.get("base_token", {}).get("data", {}) or {}).get("id", "")
    mint = base.split("_", 1)[1] if "_" in base else base

    # PumpSwap aloja mas que memecoins graduadas: acciones tokenizadas (HOOD,
    # NVDA) y tokens de empresas cotizan ahi y colaban en el ranking con un 99%
    # de compradores, que no es demanda organica sino un mercado unilateral.
    # El marcador fiable de origen pump.fun es que el mint termina en 'pump';
    # el dex 'pump-fun' es la curva, asi que ese es pump.fun por definicion.
    if dex == "pumpswap":
        if not mint.endswith("pump"):
            return None
    elif dex != "pump-fun":
        return None

    liq = _f(a, "reserve_in_usd")
    if liq < 6000:
        return None                      # imposible salir sin destrozar el precio

    ch = a.get("price_change_percentage") or {}
    vol = a.get("volume_usd") or {}
    tx = a.get("transactions") or {}

    def c(k):
        return _f(ch, k)

    def v(k):
        return _f(vol, k)

    m5, m15, h1, h6 = c("m5"), c("m15"), c("h1"), c("h6")
    v5, v15, v60 = v("m5"), v("m15"), v("h1")
    if v60 < 3000:
        return None                      # sin actividad real que analizar

    reasons, pen = [], []
    pts = 0.0

    # --- 1. aceleracion de volumen -------------------------------------
    # v15 anualizado a 1h frente al volumen real de 1h.
    accel = (v15 * 4.0) / v60 if v60 > 0 else 0.0
    if accel > 1.25:
        g = min(2.2, math.log(accel) * 2.4)
        pts += g
        reasons.append("volumen acelerando %.1fx" % accel)
    elif accel < 0.55:
        pts -= 1.1
        pen.append("volumen enfriandose")

    # --- 2. compradores unicos ------------------------------------------
    t15 = tx.get("m15") or {}
    byr, slr = _f(t15, "buyers"), _f(t15, "sellers")
    tot = byr + slr
    if tot >= 8:
        share = byr / tot
        if share > 0.92:
            # Una subida organica tiene gente tomando beneficios. Un 95-99% de
            # compradores no es entusiasmo, es un mercado donde casi nadie
            # puede o quiere vender todavia: suele preceder al primer muro.
            pts += 0.6
            pen.append("%.0f%% compradores: casi nadie vende aun, poco realista"
                       % (share * 100))
        else:
            pts += (share - 0.5) * 4.4
            if share > 0.58:
                reasons.append("%.0f%% de wallets comprando (%d/%d)"
                               % (share * 100, byr, int(tot)))
            elif share < 0.42:
                pen.append("%.0f%% de wallets vendiendo" % ((1 - share) * 100))
        # Muchas operaciones repartidas entre pocas wallets: compra repetida.
        b, s2 = _f(t15, "buys"), _f(t15, "sells")
        if b + s2 > 0 and byr > 0:
            per = b / byr
            if per > 4.5:
                pts -= 1.3
                pen.append("%.1f compras por wallet (repeticion, no demanda nueva)" % per)

    # --- 3. momentum en zona util ---------------------------------------
    if 1.0 < h1 <= 60:
        pts += 1.5
        reasons.append("subida temprana +%.0f%% 1h" % h1)
    elif 60 < h1 <= 180:
        pts += 0.5
        reasons.append("+%.0f%% 1h, ya avanzada" % h1)
    elif h1 > 300:
        pts -= 2.0
        pen.append("+%.0f%% 1h: parabolica, entrada tardia" % h1)
    elif h1 < -25:
        pts -= 1.6
        pen.append("%.0f%% 1h, cayendo" % h1)

    # --- 4. confirmacion entre ventanas ---------------------------------
    if m5 > 0 and m15 > 0 and h1 > 0:
        pts += 1.2
        reasons.append("las 3 ventanas cortas alineadas arriba")
    if h6 > 80 and m15 < 0:
        pts -= 1.7
        pen.append("techo formandose: sube en 6h pero ya gira en 15m")
    if m5 < -12:
        pts -= 1.0
        pen.append("ultimos 5m girando abajo")

    # --- 5. liquidez y rotacion -----------------------------------------
    turn = v("h24") / liq if liq > 0 else 0.0
    if turn > 30:
        pts -= 1.2
        pen.append("rotacion %.0fx sobre la liquidez (posible volumen inflado)" % turn)
    if liq > 25000:
        pts += 0.5
        reasons.append("liquidez %s" % _money(liq))

    # --- 6. edad ---------------------------------------------------------
    age_h = None
    created = a.get("pool_created_at")
    if created:
        try:
            import calendar
            age_h = (now - calendar.timegm(
                time.strptime(created, "%Y-%m-%dT%H:%M:%SZ"))) / 3600.0
        except (ValueError, TypeError):
            age_h = None
    if age_h is not None and age_h < 0.25:
        pts -= 0.8
        pen.append("menos de 15 min de vida: sin historico")

    return {
        "mint": mint, "pool": a.get("address"), "name": a.get("name", "?"),
        "dex": dex, "score": pts,
        "price": _f(a, "base_token_price_usd"),
        "fdv": _f(a, "fdv_usd"), "liq": liq,
        "vol_h1": v60, "vol_m15": v15, "accel": accel,
        "m5": m5, "m15": m15, "h1": h1, "h6": h6,
        "buyers": int(byr), "sellers": int(slr),
        "age_h": age_h, "reasons": reasons, "penalties": pen,
    }


def _money(x):
    if x is None:
        return "n/d"
    if abs(x) >= 1_000_000:
        return "$%.2fM" % (x / 1e6)
    if abs(x) >= 1_000:
        return "$%.1fk" % (x / 1e3)
    return "$%.0f" % x


def find(limit=8, pages=1):
    """Devuelve los candidatos mejor puntuados, de mas a menos."""
    sources.take_errors()
    rows = []
    for pool in candidates(pages=pages):
        try:
            r = score(pool)
        except Exception:
            r = None
        if r:
            rows.append(r)
    rows.sort(key=lambda r: -r["score"])
    return rows[:limit], sources.take_errors()
