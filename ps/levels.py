"""Soportes y resistencias a partir de estructura real de mercado.

Dos fuentes de niveles:
  1. Pivotes fractales sobre las velas, agrupados por cercania y ponderados por
     el volumen que se negocio en ellos (un maximo con volumen pesa mas).
  2. Niveles estructurales: el ATH y -- para tokens aun en la curva -- el precio
     exacto de graduacion, que actua como iman porque es un umbral mecanico del
     protocolo, no una linea psicologica.
"""

import math


def _pivots(candles, left=2, right=2):
    """Maximos y minimos locales (fractales de Williams)."""
    highs, lows = [], []
    n = len(candles)
    for i in range(left, n - right):
        win = candles[i - left:i + right + 1]
        c = candles[i]
        if c["h"] >= max(w["h"] for w in win):
            highs.append((c["h"], c["v"]))
        if c["l"] <= min(w["l"] for w in win):
            lows.append((c["l"], c["v"]))
    return highs, lows


def _cluster(points, tol):
    """Agrupa niveles a menos de `tol` (relativo) y los pondera por volumen."""
    if not points:
        return []
    points = sorted(points, key=lambda p: p[0])
    clusters, cur = [], [points[0]]
    for p in points[1:]:
        ref = cur[0][0]
        if ref > 0 and abs(p[0] - ref) / ref <= tol:
            cur.append(p)
        else:
            clusters.append(cur)
            cur = [p]
    clusters.append(cur)

    out = []
    for cl in clusters:
        wsum = sum(v for _, v in cl) or float(len(cl))
        price = sum(p * (v or 1.0) for p, v in cl) / (sum((v or 1.0) for _, v in cl))
        out.append({"price": price, "weight": wsum, "touches": len(cl)})
    return out


def build_levels(candles, stats, curve, price):
    """Devuelve (soportes, resistencias) ordenados por cercania al precio."""
    sup, res = [], []
    if not price or price <= 0:
        return sup, res

    tol = max(0.015, min(0.08, stats.get("atr_pct", 0.02) * 1.5))
    highs, lows = _pivots(candles) if len(candles) >= 8 else ([], [])

    for lv in _cluster(highs, tol):
        if lv["price"] > price * 1.004:
            lv["kind"] = "pivote"
            res.append(lv)
    for lv in _cluster(lows, tol):
        if lv["price"] < price * 0.996:
            lv["kind"] = "pivote"
            sup.append(lv)

    ath = stats.get("ath")
    if ath and ath > price * 1.004:
        res.append({"price": ath, "weight": 0.0, "touches": 1, "kind": "ATH"})

    # El precio de graduacion es un nivel mecanico: al alcanzarlo la curva se
    # cierra y el token migra al AMM. Solo aplica si aun no ha graduado.
    gp = curve.get("grad_price_sol_usd")
    if gp and not curve.get("complete") and gp > price * 1.004:
        res.append({"price": gp, "weight": 0.0, "touches": 1, "kind": "GRADUACION"})

    res.sort(key=lambda l: l["price"])
    sup.sort(key=lambda l: -l["price"])
    return _dedupe(sup, tol), _dedupe(res, tol)


def _dedupe(lvls, tol):
    """Funde niveles casi identicos; gana la etiqueta mas informativa.

    Un pivote que coincide con el ATH es un solo nivel, no dos.
    """
    rank = {"GRADUACION": 3, "ATH": 2, "pivote": 1}
    out = []
    for lv in lvls:
        prev = out[-1] if out else None
        if prev and prev["price"] > 0 and abs(lv["price"] - prev["price"]) / prev["price"] <= tol * 0.5:
            if rank.get(lv.get("kind"), 0) > rank.get(prev.get("kind"), 0):
                prev["kind"] = lv["kind"]
            prev["touches"] += lv.get("touches", 1)
            continue
        out.append(lv)
    return out


def vol_target(price, sigma, horizon_candles, k, hurst=0.5):
    """Objetivo por volatilidad: precio * exp(k * sigma * H^hurst).

    El exponente se mide sobre los propios retornos del token (ver
    features.hurst) en lugar de asumir 0,5. En memecoins suele salir por
    debajo, porque las rafagas revierten y sqrt(t) sobreestima el recorrido.
    """
    if not price or sigma <= 0 or horizon_candles <= 0:
        return None
    return price * math.exp(k * sigma * (horizon_candles ** hurst))


def pick_target(price, vol_level, levels, up):
    """Elige el objetivo final.

    Si hay un nivel estructural cerca del objetivo por volatilidad, gana el
    nivel: el precio tiende a reaccionar donde hay ordenes, no en una cifra
    estadistica redonda. Si no, se queda el objetivo por volatilidad.
    """
    if not vol_level or not price or price <= 0:
        return None, "volatilidad"

    # Un nivel pegado al precio no describe un escenario: si el objetivo de
    # bajada esta a -0,6%, eso es ruido, no un movimiento. Se exige que el
    # nivel cubra al menos el 40% del recorrido esperado por volatilidad.
    reach = abs(math.log(vol_level / price))
    min_move = 0.40 * reach

    best, best_d = None, None
    for lv in levels:
        p = lv["price"]
        if p <= 0:
            continue
        if up and p <= price:
            continue
        if (not up) and p >= price:
            continue
        if abs(math.log(p / price)) < min_move:
            continue
        d = abs(math.log(p / vol_level))
        if d <= 0.45 and (best_d is None or d < best_d):
            best, best_d = lv, d
    if best:
        return best["price"], best.get("kind", "nivel")
    return vol_level, "volatilidad"
