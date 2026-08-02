"""Condiciones de entrada.

Esto NO dice si comprar. Dice que hay medido ahora mismo, cuanto cuesta entrar
y salir, y cual es el valor esperado que se desprende de los propios escenarios
del modelo. La decision es de quien opera.

La pieza que casi nadie calcula es el valor esperado. Se tienen tres escenarios
con probabilidad y objetivo, asi que la esperanza del movimiento es inmediata:

    VE = P(subida)·r_subida + P(rango)·r_rango + P(bajada)·r_bajada

y a eso hay que restarle el coste de ida y vuelta, que en un pool pequeño se
come el resultado antes de que la direccion importe. Un VE negativo significa
que, con estos numeros, la operacion pierde de media aunque acierte a veces.
"""

import math

# Comisiones de plataforma (ida + vuelta), aproximadas.
FEE_CURVA = 0.02        # pump.fun: ~1% por lado
FEE_AMM = 0.005         # PumpSwap: ~0,25% por lado


def deslizamiento(size_usd, liq_usd):
    """Cuanto mueve el precio tu propia orden, en una curva x·y=k."""
    if not liq_usd or liq_usd <= 0 or not size_usd:
        return None
    q = liq_usd / 2.0
    return (1.0 + size_usd / q) ** 2 - 1.0


def coste_ida_vuelta(size_usd, liq_usd, en_curva):
    """Lo que se paga por entrar y salir, antes de que el precio haga nada."""
    slip = deslizamiento(size_usd, liq_usd)
    if slip is None:
        return None
    fee = FEE_CURVA if en_curva else FEE_AMM
    # Se paga deslizamiento al entrar y otra vez al salir.
    return 2.0 * slip + fee


def valor_esperado(scen):
    """Esperanza del movimiento segun los tres escenarios, en %."""
    ve = 0.0
    ok = False
    for s in scen:
        p = s.get("prob") or 0.0
        if s["name"] == "RANGO":
            lo, hi = s.get("lo_pct"), s.get("hi_pct")
            r = ((lo + hi) / 2.0) if (lo is not None and hi is not None) else 0.0
        else:
            r = s.get("pct")
            if r is None:
                continue
        ve += p * r
        ok = True
    return ve if ok else None


def evalua(a, tamano_usd=None):
    """Reune las condiciones medidas. No emite recomendacion."""
    scen = a["scenarios"]
    flow = a["flow"]
    liq = a["liq"]["liq_usd"]
    curve = a["curve"]
    pred = a["pred"]
    stats = a["stats"]
    en_curva = not curve.get("complete")

    up = next((s for s in scen if s["name"] == "SUBIDA"), {})
    dn = next((s for s in scen if s["name"] == "BAJADA"), {})

    # Tamaño de referencia: el 0,5% de la liquidez es una posicion que el pool
    # digiere sin destrozar el precio. Sirve para ilustrar el coste.
    ref = tamano_usd or max(50.0, min(2000.0, (liq or 0) * 0.005))

    out = {
        "tamano_ref": ref,
        "liq": liq,
        "en_curva": en_curva,
        "ve_pct": valor_esperado(scen),
        "coste_pct": (coste_ida_vuelta(ref, liq, en_curva) or 0) * 100,
        "slip_pct": (deslizamiento(ref, liq) or 0) * 100,
        "rr": None,
        "ve_neto_pct": None,
        "rojas": [], "verdes": [], "descartes": [],
        "condiciones": None, "color": "y",
    }

    # Riesgo / recompensa: cuanto se puede ganar frente a cuanto se arriesga,
    # tomando los objetivos del propio modelo.
    if up.get("pct") and dn.get("pct"):
        riesgo = abs(dn["pct"])
        if riesgo > 0:
            out["rr"] = up["pct"] / riesgo

    if out["ve_pct"] is not None:
        out["ve_neto_pct"] = out["ve_pct"] - out["coste_pct"]

    # --- descartes: hechos observados que invalidan la entrada -----------
    if flow.get("dev_sold_usd", 0) > 0:
        out["descartes"].append(
            "el creador del token esta vendiendo (%s en la ventana)"
            % _money(flow["dev_sold_usd"]))
    if flow.get("hhi", 0) > 0.30:
        out["descartes"].append(
            "una sola wallet mueve el %.0f%% del volumen (HHI %.2f)"
            % (flow.get("top_wallet_share", 0) * 100, flow["hhi"]))
    if liq and liq < 2500:
        out["descartes"].append(
            "liquidez de %s: salir moveria el precio mas que cualquier tesis"
            % _money(liq))
    if out["ve_neto_pct"] is not None and out["ve_neto_pct"] < -8:
        out["descartes"].append(
            "valor esperado neto del %.1f%%: el coste se come cualquier acierto"
            % out["ve_neto_pct"])

    # --- banderas rojas ---------------------------------------------------
    if out["ve_neto_pct"] is not None and -8 <= out["ve_neto_pct"] < 0:
        out["rojas"].append("valor esperado neto negativo (%.1f%%)" % out["ve_neto_pct"])
    if out["rr"] is not None and out["rr"] < 1.0:
        out["rojas"].append("se arriesga mas de lo que se puede ganar (R/R %.2f)"
                            % out["rr"])
    if pred["probs"]["BAJADA"] > 0.5:
        out["rojas"].append("el escenario mas probable es a la baja (%.0f%%)"
                            % (pred["probs"]["BAJADA"] * 100))
    if out["coste_pct"] > 6:
        out["rojas"].append("entrar y salir cuesta %.1f%% antes de acertar"
                            % out["coste_pct"])
    if flow.get("decay", 0) < -0.35:
        out["rojas"].append("el ritmo de operaciones cae un %.0f%%"
                            % (abs(flow["decay"]) * 100))
    dd = stats.get("dd_from_ath", 0)
    if dd < -0.6:
        out["rojas"].append("cotiza un %.0f%% por debajo de su maximo" % (abs(dd) * 100))

    # --- banderas verdes --------------------------------------------------
    if out["ve_neto_pct"] is not None and out["ve_neto_pct"] > 3:
        out["verdes"].append("valor esperado neto positivo (+%.1f%%)"
                             % out["ve_neto_pct"])
    if out["rr"] is not None and out["rr"] >= 1.8:
        out["verdes"].append("R/R favorable (%.2f a 1)" % out["rr"])
    if pred["probs"]["SUBIDA"] > 0.45:
        out["verdes"].append("la subida es el escenario dominante (%.0f%%)"
                             % (pred["probs"]["SUBIDA"] * 100))
    if flow.get("buyer_share", 0.5) > 0.58:
        out["verdes"].append("%.0f%% de las wallets estan comprando"
                             % (flow["buyer_share"] * 100))
    if out["coste_pct"] < 2:
        out["verdes"].append("coste de ida y vuelta bajo (%.1f%%)" % out["coste_pct"])
    if en_curva and (curve.get("progress") or 0) > 0.7:
        out["verdes"].append("curva al %.0f%%, cerca de graduar"
                             % ((curve.get("progress") or 0) * 100))
    if flow.get("wallets", 0) > 80:
        out["verdes"].append("%d wallets distintas operando" % flow["wallets"])

    # --- estado de las condiciones ---------------------------------------
    if out["descartes"]:
        out["condiciones"] = "CONDICIONES ADVERSAS"
        out["color"] = "red"
    elif len(out["rojas"]) >= 3 or (out["ve_neto_pct"] or 0) < -2:
        out["condiciones"] = "CONDICIONES DESFAVORABLES"
        out["color"] = "red"
    elif len(out["verdes"]) >= 3 and not out["rojas"]:
        out["condiciones"] = "CONDICIONES FAVORABLES"
        out["color"] = "g"
    else:
        out["condiciones"] = "CONDICIONES MIXTAS"
        out["color"] = "y"
    return out


def _money(x):
    if x is None:
        return "n/d"
    if abs(x) >= 1_000_000:
        return "$%.2fM" % (x / 1e6)
    if abs(x) >= 1_000:
        return "$%.1fk" % (x / 1e3)
    return "$%.0f" % x
