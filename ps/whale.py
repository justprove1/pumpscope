"""Probabilidad de que entre una ballena.

El problema y por que no se resuelve contando
---------------------------------------------
La via obvia -- contar cuantas compras grandes hubo y dividir por el tiempo --
se rompe en cuanto se prueba con datos reales. En ANSEM, con 2 millones de
liquidez, la mayor compra en 40 minutos fue de 3.034 dolares: ni una sola
superaba el umbral. Contar exceedances devuelve cero, y de cero no se saca
probabilidad. Pero que no se haya visto una ballena en 40 minutos no significa
que no pueda entrar en la proxima hora.

La via que si funciona: teoria de valores extremos
--------------------------------------------------
En vez de contar los pocos eventos grandes, se estima la FORMA DE LA COLA con
toda la muestra. Los tamaños de operacion en un memecoin siguen una ley de
potencias, asi que con el estimador de Hill sobre las k mayores se obtiene el
indice de cola alfa, y de ahi la probabilidad de que una compra cualquiera
supere el umbral -- aunque nunca se haya observado una que lo hiciera.

    P(compra > u) ~ (k/n) · (u / X_(k))^(-alfa)

Con eso, la tasa de llegada de ballenas es:

    lambda = (compras por segundo) · P(compra > umbral)

y la probabilidad de al menos una en el horizonte, bajo llegadas de Poisson:

    P(>=1) = 1 - exp(-lambda · H)

El umbral no es una cifra en dolares
-------------------------------------
"Ballena" no puede ser "$10.000": eso es enorme en un pool de 20k e
irrelevante en uno de 2M. Se define por IMPACTO: la compra que mueve el precio
un 1%. En una curva x·y=k, comprando A contra una reserva de cotizacion Q, el
precio se multiplica por (1 + A/Q)^2. Con Q ~ liquidez/2:

    A = liquidez · (sqrt(1 + impacto) - 1) / 2

Asi el umbral se adapta solo a cada token y significa lo mismo en todos.
"""

import math

# Impacto en precio que define a una ballena (1% por defecto).
IMPACTO_OBJETIVO = 0.01

# Niveles de tamaño.
#
# Un solo umbral no informa: a 6 horas vista, "entra al menos una compra que
# mueva el precio un 1%" sale 100% en cualquier token vivo, y un 100% no
# distingue nada. Con varios niveles la cifra vuelve a discriminar -- lo
# interesante no es si entrara alguien, sino de que tamaño.
NIVELES = [
    ("ballena", 0.01, "mueve el precio ~1%"),
    ("ballena grande", 0.05, "mueve el precio ~5%"),
    ("mega ballena", 0.20, "mueve el precio ~20%"),
]

# El estimador de Hill es notoriamente sensible a k: en un token medido, alfa
# iba de 4,09 (k=10) a 0,94 (k=30). Se agrega sobre un rango de k y se toma la
# mediana, que es la practica estandar para no depender de una eleccion.
_K_MIN = 8


def umbral(liq_usd, impacto=IMPACTO_OBJETIVO):
    """Tamaño de compra que mueve el precio `impacto` en una curva x·y=k."""
    if not liq_usd or liq_usd <= 0:
        return None
    return liq_usd * (math.sqrt(1.0 + impacto) - 1.0) / 2.0


def impacto_de(size_usd, liq_usd):
    """Movimiento de precio que provoca una compra de ese tamaño."""
    if not liq_usd or liq_usd <= 0 or not size_usd:
        return None
    q = liq_usd / 2.0
    return (1.0 + size_usd / q) ** 2 - 1.0


def _hill(orden, k):
    """Indice de cola alfa sobre las k mayores observaciones (ya ordenadas desc)."""
    if len(orden) < k + 2 or orden[k] <= 0:
        return None
    s = sum(math.log(orden[i] / orden[k]) for i in range(k) if orden[i] > 0)
    if s <= 0:
        return None
    return k / s


def prob_cola(sizes, thr):
    """P(una operacion cualquiera supere `thr`), por valores extremos.

    Se promedia sobre varios k y se devuelve la mediana. Si el umbral cae
    dentro del rango observado se usa la frecuencia empirica, que es mas
    fiable que extrapolar.
    """
    xs = [x for x in sizes if x and x > 0]
    n = len(xs)
    if n < 20 or not thr or thr <= 0:
        return None, None, 0
    orden = sorted(xs, reverse=True)

    # Si hay suficientes observaciones por encima del umbral, no hace falta
    # extrapolar: se cuenta.
    supera = sum(1 for x in xs if x >= thr)
    if supera >= 5:
        return supera / n, None, supera

    k_max = min(40, max(_K_MIN + 1, n // 3))
    probs, alfas = [], []
    for k in range(_K_MIN, k_max + 1):
        a = _hill(orden, k)
        if not a:
            continue
        # alfa <= 1 implica una cola de media infinita: eso no describe
        # tamaños de operacion, que estan acotados por el saldo de las
        # wallets. Se acota por abajo en 1,1 para no inflar la extrapolacion.
        a = max(1.1, min(6.0, a))
        u = orden[k]
        if u <= 0:
            continue
        p = (k / n) * ((thr / u) ** (-a)) if thr > u else (k / n)
        if 0 < p <= 1:
            probs.append(p)
            alfas.append(a)
    if not probs:
        return None, None, supera
    probs.sort()
    alfas.sort()
    med = probs[len(probs) // 2]
    return med, alfas[len(alfas) // 2], supera


def analiza(trades, liq_usd, horizonte_h, contexto=None):
    """Probabilidad de entrada (y de salida) de una ballena en el horizonte."""
    out = {
        "umbral": None, "alfa": None, "observadas": 0,
        "p_compra": None, "p_venta": None,
        "lambda_h": None, "lambda_venta_h": None,
        "espera_min": None, "impacto_pct": None,
        "tam_tipico": None, "horizonte_h": horizonte_h,
        "ajuste": 1.0, "motivos": [], "fiable": False,
    }
    thr = umbral(liq_usd)
    if not thr or not trades:
        return out
    out["umbral"] = thr
    out["impacto_pct"] = IMPACTO_OBJETIVO * 100

    compras = [t["usd"] for t in trades if t["kind"] == "buy" and t["usd"] > 0]
    ventas = [t["usd"] for t in trades if t["kind"] == "sell" and t["usd"] > 0]
    span = max(1, trades[-1]["ts"] - trades[0]["ts"])
    if len(compras) < 20:
        return out

    p_c, alfa, sup = prob_cola(compras, thr)
    p_v, _, _ = prob_cola(ventas, thr)
    out["alfa"] = alfa
    out["observadas"] = sup
    if p_c is None:
        return out

    # --- tasa base de llegada -------------------------------------------
    tasa_compras = len(compras) / span          # compras por segundo
    lam = tasa_compras * p_c                    # ballenas por segundo

    # --- ajuste por contexto --------------------------------------------
    # La tasa observada es del pasado inmediato. Estas correcciones la
    # proyectan segun lo que esta cambiando ahora mismo.
    ctx = contexto or {}
    fac = 1.0
    accel = ctx.get("accel")
    if accel and accel > 1.3:
        f = min(2.0, accel ** 0.6)
        fac *= f
        out["motivos"].append("el volumen se esta acelerando (%.1fx)" % accel)
    elif accel and accel < 0.6:
        fac *= 0.65
        out["motivos"].append("el volumen se esta enfriando")

    prog = ctx.get("progress")
    if prog is not None and not ctx.get("complete") and prog > 0.7:
        fac *= 1.35
        out["motivos"].append("la curva esta al %.0f%%: tramo que atrae "
                              "compradores grandes" % (prog * 100))

    imb = ctx.get("imbalance")
    if imb is not None and imb > 0.15:
        fac *= 1.2
        out["motivos"].append("domina la presion compradora")
    elif imb is not None and imb < -0.15:
        fac *= 0.8
        out["motivos"].append("domina la presion vendedora")

    # Agrupamiento: las ballenas se siguen unas a otras. Si hubo una en el
    # ultimo tramo de la ventana, la siguiente es mas probable.
    reciente = [t for t in trades
                if t["kind"] == "buy" and t["usd"] >= thr
                and t["ts"] >= trades[-1]["ts"] - span * 0.25]
    if reciente:
        fac *= 1.4
        out["motivos"].append("ya entro una ballena hace poco (suelen agruparse)")

    fac = max(0.4, min(3.0, fac))
    out["ajuste"] = fac
    lam *= fac

    H = horizonte_h * 3600.0
    out["lambda_h"] = lam * 3600.0
    out["p_compra"] = 1.0 - math.exp(-lam * H)
    if p_v is not None:
        lam_v = (len(ventas) / span) * p_v * fac
        out["lambda_venta_h"] = lam_v * 3600.0
        out["p_venta"] = 1.0 - math.exp(-lam_v * H)
    if lam > 0:
        out["espera_min"] = (1.0 / lam) / 60.0

    # Tamaño tipico de la que entre: mediana condicionada a superar el umbral.
    # Para una Pareto de indice alfa, la mediana por encima de u es u·2^(1/alfa).
    if alfa:
        out["tam_tipico"] = thr * (2.0 ** (1.0 / alfa))
    else:
        grandes = sorted(x for x in compras if x >= thr)
        out["tam_tipico"] = grandes[len(grandes) // 2] if grandes else thr

    out["impacto_tipico_pct"] = (impacto_de(out["tam_tipico"], liq_usd) or 0) * 100
    out["fiable"] = len(compras) >= 60 and span >= 300

    # --- niveles de tamaño ----------------------------------------------
    max_obs = max(compras) if compras else 0.0
    out["max_observado"] = max_obs
    niveles = []
    for nombre, imp, desc in NIVELES:
        u = umbral(liq_usd, imp)
        pc, al, ob = prob_cola(compras, u)
        pv, _, _ = prob_cola(ventas, u)
        if pc is None:
            continue
        lam_n = tasa_compras * pc * fac
        lam_nv = (len(ventas) / span) * pv * fac if pv is not None else None
        # Cuanto se esta extrapolando: si el umbral queda muy por encima de
        # la mayor compra observada, la cifra ya no es medicion sino ajuste
        # de cola llevado lejos. Se expone para que se pueda ponderar.
        veces = (u / max_obs) if max_obs else None
        niveles.append({
            "nombre": nombre, "desc": desc, "umbral": u,
            "sobre_max": veces,
            "extrapolado": bool(veces and veces > 1.0),
            "muy_extrapolado": bool(veces and veces > 8.0),
            "impacto_pct": imp * 100,
            "p_compra": 1.0 - math.exp(-lam_n * H),
            "p_venta": (1.0 - math.exp(-lam_nv * H)) if lam_nv is not None else None,
            "esperadas": lam_n * H,
            "lambda_h": lam_n * 3600.0,
            "espera_min": (1.0 / lam_n) / 60.0 if lam_n > 0 else None,
            "observadas": ob,
        })
    out["niveles"] = niveles

    # Titular: el nivel cuya probabilidad todavia discrimina. Un 100% no
    # informa; se busca el mayor nivel que siga por debajo del 92%.
    tit = None
    for n in niveles:
        if n["p_compra"] < 0.92 and not n["muy_extrapolado"]:
            tit = n
            break
    if tit is None:                      # todos saturan o son extrapolacion
        for n in niveles:
            if not n["muy_extrapolado"]:
                tit = n
    out["titular"] = tit or (niveles[-1] if niveles else None)
    return out
