"""Motor de probabilidad.

Estructura: softmax sobre 3 resultados, donde el sesgo de cada clase es el
log del base rate empirico y cada señal medida suma o resta desde ahi.

    P(k) = softmax( log(prior_k) + confianza * Σ_i  w_ik * z_i )

Dos propiedades que importan:

  * Sin evidencia, el modelo devuelve exactamente el base rate. No se inventa
    una opinion cuando no hay datos.
  * Cada termino w_ik * z_i queda registrado, asi que toda probabilidad se
    puede auditar linea por linea (--why).

Los priors salen de datos publicados, no de intuicion:
  - 68,67% de los tokens de pump.fun hacen su ultimo trade el mismo dia
    del lanzamiento (CoinGecko, 18,67M tokens, ene-2024 a jun-2026).
  - 4,55% siguen operando pasados 90 dias (mismo estudio).
  - ~0,26% gradua la curva (DEXTools, mediados de 2026).
Un horizonte de 6h sobre un token recien lanzado hereda ese sesgo bajista
brutal; por eso el prior de 'BAJADA' domina al principio de la curva.
"""

import math

UP, RANGE, DOWN = "SUBIDA", "RANGO", "BAJADA"
CLASSES = (UP, RANGE, DOWN)

# Base rates por regimen. Recalibrables con `pumpscope calibrar`.
PRIORS = {
    "curva_temprana": {UP: 0.15, RANGE: 0.20, DOWN: 0.65},
    "curva_media":    {UP: 0.22, RANGE: 0.25, DOWN: 0.53},
    "curva_tardia":   {UP: 0.31, RANGE: 0.27, DOWN: 0.42},
    "graduado":       {UP: 0.27, RANGE: 0.30, DOWN: 0.43},
}

# Peso de cada señal sobre cada clase: (subida, rango, bajada).
WEIGHTS = {
    "momentum":        (+0.35, +0.00, -0.30),
    "sobreextension":  (-0.28, +0.00, +0.32),
    "presion_compra":  (+0.45, +0.00, -0.45),
    "caida_desde_ath": (-0.20, -0.10, +0.30),
    "concentracion":   (-0.35, -0.05, +0.40),
    "dev_vendiendo":   (-0.90, -0.20, +1.00),
    "liquidez_fina":   (-0.25, -0.15, +0.35),
    "rotacion_rara":   (-0.20, +0.00, +0.25),
    "actividad_cae":   (-0.35, +0.05, +0.35),
    "participacion":   (+0.40, +0.05, -0.40),
    "cerca_graduar":   (+0.45, +0.00, -0.35),
    "estancado":       (-0.30, +0.10, +0.25),
    "tendencia_align": (+0.38, -0.05, -0.38),
    "agotamiento":     (-0.45, +0.05, +0.45),
}

# Señales agrupadas por lo que realmente miden. Varias de ellas son la misma
# informacion vista desde angulos distintos -- un token grande y sano tiene a
# la vez muchas wallets, liquidez profunda y HHI bajo. Sumarlas como evidencia
# independiente es triple contabilidad y dispara la confianza sin motivo (el
# problema clasico de naive-Bayes con variables correlacionadas). Por eso cada
# grupo aporta como mucho GROUP_CAP en log-odds.
GROUPS = {
    "estructura": ("participacion", "liquidez_fina", "concentracion", "rotacion_rara"),
    "momentum":   ("momentum", "sobreextension", "caida_desde_ath",
                   "tendencia_align", "agotamiento"),
    "flujo":      ("presion_compra", "actividad_cae"),
    "dev":        ("dev_vendiendo",),
    "curva":      ("cerca_graduar", "estancado"),
}
GROUP_CAP = 0.75

# Tope global: por muy alineada que este la evidencia, la microestructura de un
# memecoin no justifica alejarse mas de esto del base rate.
TOTAL_CAP = 1.60

_CLIP = 2.5


def _clip(x, lo=-_CLIP, hi=_CLIP):
    return max(lo, min(hi, x))


def regime(curve):
    if curve.get("complete"):
        return "graduado"
    p = curve.get("progress")
    if p is None:
        return "curva_temprana"
    if p < 0.25:
        return "curva_temprana"
    if p < 0.70:
        return "curva_media"
    return "curva_tardia"


def build_signals(curve, stats, flow, liq, age, mf=None):
    """Convierte metricas crudas en z-scores acotados y comparables.

    Regla dura: la ausencia de datos no es evidencia. Si una fuente falla o
    devuelve poca muestra, las señales que dependen de ella se atenuan hasta
    cero en vez de aparecer como un valor extremo. Sin este freno, un 429 de
    la API se leia como "cero wallets participando" y hundia la prediccion.
    """
    s = {}
    # Rampas de disponibilidad: 0 sin datos, 1 con muestra suficiente.
    f_ok = min(1.0, flow.get("n", 0) / 25.0)
    c_ok = min(1.0, stats.get("n", 0) / 15.0)

    s["momentum"] = _clip(stats.get("ret_z", 0.0)) * c_ok
    s["sobreextension"] = _clip(max(0.0, stats.get("overext", 0.0)) * 1.2) * c_ok
    s["presion_compra"] = _clip(flow.get("imbalance", 0.0) * 2.2) * f_ok

    dd = stats.get("dd_from_ath", 0.0)  # <= 0
    s["caida_desde_ath"] = (_clip((-dd) * 2.5) if dd < -0.25 else 0.0) * c_ok

    hhi = flow.get("hhi", 0.0)
    # HHI ~0,08 es un mercado sano con decenas de wallets; 0,35+ es una o dos
    # wallets moviendo casi todo el volumen.
    s["concentracion"] = _clip((hhi - 0.08) / 0.12) * f_ok

    dev_out = flow.get("dev_sold_usd", 0.0) - flow.get("dev_bought_usd", 0.0)
    liq_usd = max(1.0, liq.get("liq_usd", 0.0))
    # El dev vendiendo es un hecho observado, no una inferencia estadistica:
    # si aparece en la ventana cuenta entero, sin atenuar por tamaño de muestra.
    s["dev_vendiendo"] = _clip((dev_out / liq_usd) * 12.0) if dev_out > 0 else 0.0

    lq = liq.get("liq_usd", 0.0)
    # Referencia: $15k de liquidez es un token vivo pero pequeño.
    s["liquidez_fina"] = _clip(-math.log10(max(lq, 50.0) / 15000.0))

    turn = liq.get("turnover", 0.0)
    # Rotacion >8x diaria con pocas wallets huele a volumen artificial.
    s["rotacion_rara"] = _clip((turn - 8.0) / 6.0) if turn > 8.0 else 0.0

    s["actividad_cae"] = _clip(-flow.get("decay", 0.0) * 1.6) * f_ok

    w = flow.get("wallets", 0)
    # ~35 wallets distintas en la ventana de trades = participacion normal.
    s["participacion"] = _clip(math.log((max(w, 1)) / 35.0) / 0.9) * f_ok

    prog = curve.get("progress")
    if curve.get("complete") or prog is None:
        s["cerca_graduar"] = 0.0
    else:
        # Solo empuja de verdad en el ultimo tramo de la curva.
        s["cerca_graduar"] = _clip((prog - 0.55) / 0.22) if prog > 0.55 else 0.0

    ah = age.get("age_h")
    if ah and ah > 6 and prog is not None and prog < 0.35 and not curve.get("complete"):
        s["estancado"] = _clip(math.log(ah / 6.0) / 1.1)
    else:
        s["estancado"] = 0.0

    mf = mf or {}
    if mf.get("have"):
        s["tendencia_align"] = _clip(mf.get("align", 0.0) * 1.6)
        s["agotamiento"] = _clip(mf.get("fading", 0.0))
    else:
        s["tendencia_align"] = 0.0
        s["agotamiento"] = 0.0

    return s


# Traduccion de cada señal a lenguaje llano, segun el signo con que aparece.
# (frase cuando z>0, frase cuando z<0)
_FRASES = {
    "momentum": ("el precio viene subiendo con fuerza",
                 "el precio viene cayendo"),
    "sobreextension": ("esta muy estirado sobre su media (suele revertir)", None),
    "presion_compra": ("entra mas dinero del que sale",
                       "sale mas dinero del que entra"),
    "caida_desde_ath": ("arrastra un desplome desde maximos", None),
    "concentracion": ("el volumen se concentra en muy pocas wallets",
                      "el volumen esta bien repartido entre wallets"),
    "dev_vendiendo": ("el creador del token esta vendiendo", None),
    "liquidez_fina": ("la liquidez es fina y cualquier venta mueve el precio",
                      "la liquidez es profunda y aguanta ordenes grandes"),
    "rotacion_rara": ("rota mucho mas volumen del que sostiene su liquidez", None),
    "actividad_cae": ("el ritmo de operaciones se esta apagando",
                      "el ritmo de operaciones se esta acelerando"),
    # Ojo al signo: z = log(wallets/35), asi que z>0 significa MUCHAS wallets.
    "participacion": ("hay muchas wallets distintas operando",
                      "hay pocas wallets distintas operando"),
    "cerca_graduar": ("la curva esta cerca de graduar y eso tira del precio", None),
    "estancado": ("lleva horas sin avanzar en la curva", None),
    "tendencia_align": ("todas las ventanas de tiempo apuntan arriba",
                        "todas las ventanas de tiempo apuntan abajo"),
    "agotamiento": ("sube en las ventanas largas pero ya se gira en las cortas", None),
}


def reasons(pred, top=3):
    """Por que cada escenario, en lenguaje llano.

    Se recorren las contribuciones ya calculadas y, para cada clase, se toman
    las señales que mas empujan HACIA ella. Son los mismos numeros de la tabla
    de auditoria, solo que dichos en castellano: no hay una segunda logica que
    pueda desviarse de la primera.
    """
    out = {c: [] for c in CLASSES}
    for c in CLASSES:
        pos = [k for k in pred["contrib"] if k["deltas"][c] > 0.012]
        pos.sort(key=lambda k: -k["deltas"][c])
        for k in pos[:top]:
            par = _FRASES.get(k["signal"])
            if not par:
                continue
            # El signo del z decide cual de las dos frases aplica.
            frase = par[0] if k["z"] >= 0 else par[1]
            if frase:
                out[c].append(frase)
        if not out[c]:
            out[c].append("sin señales a favor: pesa solo el base rate del regimen"
                          " (%s)" % pred["regime"].replace("_", " "))
    return out


def trend_label(pred, mf=None):
    """Veredicto de tendencia en una etiqueta.

    Combina dos cosas que miden lo mismo por vias distintas: el sesgo del
    modelo (P(subida) - P(bajada)) y la alineacion de las ventanas de tiempo.
    Que ambas coincidan es lo que separa una tendencia de un rebote suelto,
    asi que la etiqueta solo llega a 'fuerte' cuando las dos empujan igual.

    Devuelve (etiqueta, clave_de_color, explicacion_corta).
    """
    p = pred["probs"]
    diff = p[UP] - p[DOWN]
    align = (mf or {}).get("align", 0.0) if (mf or {}).get("have") else 0.0
    fading = (mf or {}).get("fading", 0.0) if (mf or {}).get("have") else 0.0

    score = diff + 0.25 * align - 0.12 * fading

    if score > 0.28:
        et, col = "TENDENCIA ALCISTA FUERTE", "g"
    elif score > 0.09:
        et, col = "TENDENCIA ALCISTA", "g"
    elif score > -0.09:
        et, col = "TENDENCIA NEUTRA", "y"
    elif score > -0.28:
        et, col = "TENDENCIA BAJISTA", "red"
    else:
        et, col = "TENDENCIA BAJISTA FUERTE", "red"

    # El matiz que mas importa: subir en las ventanas largas mientras las
    # cortas ya se giran no es tendencia alcista, es un techo formandose.
    if fading > 0.5 and score > -0.09:
        et, col = "TENDENCIA AGOTANDOSE", "y"
        return et, col, ("sube en las ventanas largas pero ya se gira en las "
                         "cortas: asi se forma un techo")

    partes = []
    partes.append("el modelo da %+.0f puntos de sesgo (%.0f%% subida vs %.0f%% bajada)"
                  % (diff * 100, p[UP] * 100, p[DOWN] * 100))
    if (mf or {}).get("have"):
        if align > 0.4:
            partes.append("y las ventanas de tiempo apuntan arriba")
        elif align < -0.4:
            partes.append("y las ventanas de tiempo apuntan abajo")
        else:
            partes.append("y las ventanas de tiempo estan divididas")
    return et, col, ", ".join(partes)


def confidence(stats, flow, liq, age):
    """0..1. Con poca muestra, el modelo se repliega hacia el base rate."""
    parts = []
    parts.append(min(1.0, stats.get("n", 0) / 60.0))
    parts.append(min(1.0, flow.get("n", 0) / 120.0))
    parts.append(min(1.0, math.log10(max(liq.get("liq_usd", 0.0), 10.0) / 10.0) / 2.7))
    ah = age.get("age_h") or 0.0
    parts.append(min(1.0, ah / 1.5))
    base = sum(parts) / len(parts)
    return max(0.12, min(0.95, base))


def predict(curve, stats, flow, liq, age, mf=None):
    reg = regime(curve)
    prior = PRIORS[reg]
    sig = build_signals(curve, stats, flow, liq, age, mf)
    conf = confidence(stats, flow, liq, age)

    # 1. Delta crudo de cada señal.
    raw = {}
    for name, z in sig.items():
        if abs(z) < 1e-9:
            continue
        w = WEIGHTS[name]
        raw[name] = {c: w[i] * z * conf for i, c in enumerate(CLASSES)}

    # 2. Cada grupo se reescala si su aporte conjunto supera el tope.
    group_scale = {}
    for gname, members in GROUPS.items():
        tot = {c: sum(raw[m][c] for m in members if m in raw) for c in CLASSES}
        peak = max(abs(v) for v in tot.values()) if tot else 0.0
        group_scale[gname] = (GROUP_CAP / peak) if peak > GROUP_CAP else 1.0

    of_group = {m: g for g, ms in GROUPS.items() for m in ms}
    scaled = {}
    for name, d in raw.items():
        f = group_scale.get(of_group.get(name), 1.0)
        scaled[name] = {c: d[c] * f for c in CLASSES}

    # 3. Tope global sobre la evidencia acumulada.
    total = {c: sum(scaled[n][c] for n in scaled) for c in CLASSES}
    peak = max(abs(v) for v in total.values()) if total else 0.0
    gfac = (TOTAL_CAP / peak) if peak > TOTAL_CAP else 1.0

    scores = {c: math.log(prior[c]) for c in CLASSES}
    contrib = []
    for name, d in scaled.items():
        deltas = {c: d[c] * gfac for c in CLASSES}
        for c in CLASSES:
            scores[c] += deltas[c]
        contrib.append({"signal": name, "z": sig[name], "deltas": deltas,
                        "group": of_group.get(name, "-"),
                        "impact": max(abs(v) for v in deltas.values())})

    capped = [g for g, f in group_scale.items() if f < 0.999]

    mx = max(scores.values())
    exps = {c: math.exp(scores[c] - mx) for c in CLASSES}
    tot = sum(exps.values())
    probs = {c: exps[c] / tot for c in CLASSES}

    contrib.sort(key=lambda x: -x["impact"])
    return {"regime": reg, "prior": prior, "probs": probs,
            "signals": sig, "contrib": contrib, "confidence": conf,
            "capped_groups": capped, "global_capped": gfac < 0.999}
