"""Renderizado del informe en terminal."""

import time

C = {
    "r": "\033[0m", "b": "\033[1m", "dim": "\033[2m",
    "g": "\033[32m", "y": "\033[33m", "red": "\033[31m",
    "cy": "\033[36m", "mag": "\033[35m",
}
_NO_COLOR = False


def _c(key):
    return "" if _NO_COLOR else C[key]


def set_color(enabled):
    global _NO_COLOR
    _NO_COLOR = not enabled


def money(x):
    if x is None:
        return "n/d"
    if x == 0:
        return "$0"
    ax = abs(x)
    if ax >= 1_000_000:
        return "$%.2fM" % (x / 1_000_000)
    if ax >= 1_000:
        return "$%.1fk" % (x / 1_000)
    if ax >= 1:
        return "$%.2f" % x
    if ax >= 1e-6:
        return "$%.8f" % x
    return "$%.10f" % x


def money_full(x):
    """Capitalizacion con todos los digitos.

    '$7.23M' esconde 10.000 dolares de diferencia entre dos lecturas, que es
    justo lo que hay que ver cuando se compara la actual con un objetivo.
    Separador de miles a la española (punto).
    """
    if x is None:
        return "n/d"
    if x == 0:
        return "$0"
    ax = abs(x)
    if ax >= 1000:
        ent = "{:,.0f}".format(x).replace(",", ".")
        return "$" + ent
    if ax >= 1:
        return ("$" + "{:,.2f}".format(x)).replace(",", "@").replace(
            ".", ",").replace("@", ".")
    return money(x)


def _bar(p, width=24):
    n = int(round(p * width))
    return "█" * n + "·" * (width - n)


def _hdr(t):
    return "\n%s%s%s\n%s" % (_c("b"), t, _c("r"), _c("dim") + "─" * 66 + _c("r"))


def _wrap(txt, indent="  "):
    import textwrap as _tw
    return "\n".join(_tw.fill(txt, width=76, initial_indent=indent,
                               subsequent_indent=indent).splitlines())


def render(a, show_why=False, explicar=None):
    coin = a["coin"]
    curve = a["curve"]
    stats = a["stats"]
    flow = a["flow"]
    liq = a["liq"]
    pred = a["pred"]
    age = a["age"]
    out = []

    name = coin.get("name") or "?"
    sym = coin.get("symbol") or "?"
    out.append("\n%s%s (%s)%s  %s%s%s" % (
        _c("b"), name, sym, _c("r"), _c("dim"), a["mint"], _c("r")))

    age_h = age.get("age_h")
    age_s = ("%.0f min" % (age_h * 60)) if age_h and age_h < 2 else (
        "%.1f h" % age_h if age_h and age_h < 48 else
        ("%.1f dias" % (age_h / 24) if age_h else "n/d"))

    mcap_usd = None
    try:
        mcap_usd = float(coin.get("usd_market_cap") or 0) or None
    except (TypeError, ValueError):
        pass

    out.append("%sPrecio%s %s   %sMcap%s %s   %sLiq%s %s   %sVol24%s %s   %sEdad%s %s" % (
        _c("dim"), _c("r"), money(a["price"]),
        _c("dim"), _c("r"), money_full(mcap_usd),
        _c("dim"), _c("r"), money(liq["liq_usd"]),
        _c("dim"), _c("r"), money(liq["vol24_usd"]),
        _c("dim"), _c("r"), age_s))

    tr = a.get("trend")
    if tr:
        out.append("%s%s %s %s%s" % (
            _c(tr["color"]) + _c("b"), "▎", tr["label"], "", _c("r")))
        out.append("  %s%s%s" % (_c("dim"), tr["why"], _c("r")))

    # ---- curva ----------------------------------------------------------
    out.append(_hdr("ESTADO DE LA CURVA"))
    if curve.get("complete"):
        out.append("  %sGRADUADO%s — la curva se cerro y el token cotiza en AMM." %
                   (_c("g"), _c("r")))
    else:
        p = curve.get("progress")
        if p is not None:
            col = _c("g") if p > 0.6 else (_c("y") if p > 0.2 else _c("red"))
            out.append("  Progreso  %s%s%s %s%.1f%%%s" %
                       (col, _bar(p, 28), _c("r"), col, p * 100, _c("r")))
        gm = curve.get("grad_mcap_usd")
        gp = curve.get("grad_price_sol_usd")
        xg = curve.get("x_to_grad")
        if gm:
            out.append("  Gradua en %s de mcap  (%.1f SOL — umbral fijado en SOL, no en USD)"
                       % (money_full(gm), curve.get("grad_mcap_sol") or 0))
        if gp and xg and xg > 1:
            out.append("  Precio de graduacion %s  →  %s%.1fx%s desde aqui  |  faltan %.1f SOL"
                       % (money(gp), _c("cy"), xg, _c("r"), curve.get("sol_to_grad") or 0))

    # ---- escenarios -----------------------------------------------------
    hz = a["horizon_label"]
    if a.get("horizon_recortado"):
        hz += " (recortado de %.0fh)" % a["horizon_pedido_h"]
    out.append(_hdr("3 ESCENARIOS  ·  horizonte %s  ·  velas %s" % (hz, a["timeframe"])))
    order = sorted(a["scenarios"], key=lambda s: -s["prob"])
    cols = {"SUBIDA": _c("g"), "RANGO": _c("y"), "BAJADA": _c("red")}
    for s in order:
        col = cols[s["name"]]
        out.append("\n  %s%-7s%s %s%s%s %s%5.1f%%%s   %s" % (
            col + _c("b"), s["name"], _c("r"),
            col, _bar(s["prob"], 20), _c("r"),
            _c("b"), s["prob"] * 100, _c("r"),
            _c("dim") + s["label"] + _c("r")))
        if s["name"] == "RANGO":
            if s.get("lo") and s.get("hi"):
                out.append("      zona  %s — %s   (%+.1f%% / %+.1f%%)" % (
                    money(s["lo"]), money(s["hi"]),
                    s["lo_pct"] or 0, s["hi_pct"] or 0))
                if s.get("mcap_lo") and s.get("mcap_hi"):
                    out.append("      %smcap  %s — %s%s" % (
                        _c("dim"), money_full(s["mcap_lo"]),
                        money_full(s["mcap_hi"]), _c("r")))
            else:
                out.append("      %ssin volatilidad medible para acotar la zona%s"
                           % (_c("dim"), _c("r")))
        elif s.get("target"):
            out.append("      objetivo  %s  (%s%+.1f%%%s)   %sorigen: %s%s" % (
                money(s["target"]), col, s["pct"] or 0, _c("r"),
                _c("dim"), s["source"], _c("r")))
            if s.get("mcap"):
                extra = ("   %s" % s["mcap_vs_grad"]) if s.get("mcap_vs_grad") else ""
                word = "techo en mcap" if s["name"] == "SUBIDA" else "suelo en mcap"
                out.append("      %s%s  %s%s%s%s%s" % (
                    _c("dim"), word, col, money_full(s["mcap"]), _c("r"),
                    _c("dim"), extra + _c("r")))
        else:
            out.append("      %ssin objetivo: faltan datos de precio%s"
                       % (_c("dim"), _c("r")))

        for m in s.get("motivos", []):
            out.append("      %s· %s%s" % (col, m, _c("r")))

    hu = stats.get("hurst", 0.5)
    hu_note = ("H=%.2f medido" % hu) if stats.get("hurst_fitted") else "H=0.50 por defecto"
    if stats.get("hurst_fitted") and hu < 0.45:
        hu_note += ", revierte a la media"
    elif stats.get("hurst_fitted") and hu > 0.55:
        hu_note += ", tendencia persistente"
    out.append("\n  %sMovimiento esperado 1σ en %s: ±%.1f%%   (escalado %s)%s" % (
        _c("dim"), a["horizon_label"], a["expected_move_pct"], hu_note, _c("r")))
    out.append("  %svolatilidad por %s · objetivos por %s%s" % (
        _c("dim"), a.get("sigma_source") or "n/d", a.get("dist_source") or "n/d", _c("r")))
    sk = a.get("skew_pct")
    if sk is not None and abs(sk) > 3:
        lado = "al alza" if sk > 0 else "a la baja"
        out.append("  %sdistribucion asimetrica %s (%+.1f%% neto): el recorrido no es"
                   " simetrico%s" % (_c("dim"), lado, sk, _c("r")))

    # ---- niveles --------------------------------------------------------
    if a["resistances"] or a["supports"]:
        out.append(_hdr("NIVELES DETECTADOS"))
        def _mc(lv):
            return ("mcap %-14s" % money_full(lv.get("mcap"))) if lv.get("mcap") else " " * 20

        for lv in a["resistances"][:4][::-1]:
            out.append("  R  %s%12s%s  %s%s %s%s" % (
                _c("red"), money(lv["price"]), _c("r"),
                _c("dim"), _mc(lv), lv.get("kind", "pivote"), _c("r")))
        out.append("  %s→  %s  %sprecio actual%s" % (
            _c("b"), money(a["price"]),
            ("mcap %s  " % money_full(a.get("mcap_now"))) if a.get("mcap_now") else "", _c("r")))
        for lv in a["supports"][:4]:
            out.append("  S  %s%12s%s  %s%s %s%s" % (
                _c("g"), money(lv["price"]), _c("r"),
                _c("dim"), _mc(lv), lv.get("kind", "pivote"), _c("r")))

    # ---- flujo ----------------------------------------------------------
    out.append(_hdr("FLUJO DE ORDENES  (ultimos %d trades)" % flow["n"]))
    if flow["n"]:
        # Barrera: tres lecturas del mismo pulso. Cuando divergen, la
        # discrepancia dice mas que cualquiera de ellas por separado.
        def barra(share, w=34):
            n = max(0, min(w, int(round(share * w))))
            # Glifos distintos, no solo colores: asi la barra se lee igual
            # en una terminal sin color o en un copiar-pegar.
            return "%s%s%s%s%s" % (_c("g"), "█" * n, _c("red"), "▒" * (w - n), _c("r"))

        tx_share = flow["buys"] / max(1, flow["buys"] + flow["sells"])
        out.append("  %sCOMPRADORES%s %s %sVENDEDORES%s" % (
            _c("g") + _c("b"), _c("r"), " " * 14, _c("red") + _c("b"), _c("r")))
        out.append("  operaciones  %s  %3.0f%% / %3.0f%%   (%d / %d)" % (
            barra(tx_share), tx_share * 100, (1 - tx_share) * 100,
            flow["buys"], flow["sells"]))
        out.append("  wallets      %s  %3.0f%% / %3.0f%%   (%d / %d)" % (
            barra(flow["buyer_share"]), flow["buyer_share"] * 100,
            (1 - flow["buyer_share"]) * 100, flow["buyers"], flow["sellers"]))
        out.append("  dinero USD   %s  %3.0f%% / %3.0f%%   (%s / %s)" % (
            barra(flow["buy_usd_share"]), flow["buy_usd_share"] * 100,
            (1 - flow["buy_usd_share"]) * 100,
            money(flow["buy_usd"]), money(flow["sell_usd"])))

        out.append("  ticket medio   compra %s   venta %s" % (
            money(flow["avg_buy"]), money(flow["avg_sell"])))

        # Divergencia entre operaciones y wallets: pocas manos repitiendo.
        div = tx_share - flow["buyer_share"]
        if abs(div) > 0.12:
            if div > 0:
                out.append("  %s⚠ %.0f%% de las compras se concentran en pocas wallets "
                           "(compra repetida, no demanda nueva)%s"
                           % (_c("y"), abs(div) * 100, _c("r")))
            else:
                out.append("  %s⚠ pocas wallets acumulan con tickets grandes mientras "
                           "muchas venden pequeño%s" % (_c("y"), _c("r")))
        if flow["both"]:
            out.append("  %s%d wallets compran y venden en la misma ventana (rotacion)%s"
                       % (_c("dim"), flow["both"], _c("r")))
        out.append("  wallets unicas %d   HHI %.3f   mayor wallet %.0f%% del volumen" % (
            flow["wallets"], flow["hhi"], flow["top_wallet_share"] * 100))
        if flow["dev_sold_usd"] > 0:
            out.append("  %s⚠ EL DEV ESTA VENDIENDO: %s en la ventana%s" % (
                _c("red") + _c("b"), money(flow["dev_sold_usd"]), _c("r")))
        elif flow["dev_active"]:
            out.append("  %sDev activo comprando (%s)%s" % (
                _c("g"), money(flow["dev_bought_usd"]), _c("r")))
        dec = flow["decay"]
        if abs(dec) > 0.15:
            dcol = _c("g") if dec > 0 else _c("red")
            out.append("  Ritmo de trades %s%+.0f%%%s vs la mitad anterior de la ventana" % (
                dcol, dec * 100, _c("r")))
    else:
        out.append("  %ssin trades recuperados%s" % (_c("dim"), _c("r")))

    # ---- condiciones de entrada ------------------------------------------
    st = a.get("setup")
    if st:
        out.append(_hdr("CONDICIONES DE ENTRADA"))
        out.append("  %s%s%s" % (_c(st["color"]) + _c("b"), st["condiciones"], _c("r")))
        out.append("")
        ve, cost, neto = st["ve_pct"], st["coste_pct"], st["ve_neto_pct"]
        if ve is not None:
            ncol = _c("g") if (neto or 0) > 0 else _c("red")
            out.append("  Valor esperado del movimiento   %s%+.2f%%%s" % (
                _c("b"), ve, _c("r")))
            out.append("  Coste de entrar y salir         %s-%.2f%%%s   "
                       "%s(deslizamiento x2 + comisiones, con %s)%s" % (
                           _c("red"), cost, _c("r"), _c("dim"),
                           money(st["tamano_ref"]), _c("r")))
            out.append("  %sValor esperado NETO             %s%+.2f%%%s" % (
                _c("b"), ncol + _c("b"), neto, _c("r")))
            if neto is not None and neto < 0:
                out.append("  %sCon estos numeros la operacion pierde de media,"
                           " aunque acierte a veces.%s" % (_c("red"), _c("r")))
        if st["rr"] is not None:
            out.append("  Riesgo / recompensa             %.2f a 1" % st["rr"])

        if st["descartes"]:
            out.append("")
            out.append("  %sDESCARTES (hechos, no opiniones):%s" % (
                _c("red") + _c("b"), _c("r")))
            for d in st["descartes"]:
                out.append("    %s✕ %s%s" % (_c("red"), d, _c("r")))
        if st["rojas"]:
            out.append("")
            for r in st["rojas"]:
                out.append("    %s▼ %s%s" % (_c("red"), r, _c("r")))
        if st["verdes"]:
            out.append("")
            for v in st["verdes"]:
                out.append("    %s▲ %s%s" % (_c("g"), v, _c("r")))
        out.append("")
        out.append("  %sEsto describe lo medido, no es una recomendacion de compra."
                   "\n  La decision de operar es tuya.%s" % (_c("dim"), _c("r")))

    # ---- ballenas -------------------------------------------------------
    w = a.get("whale") or {}
    if w.get("niveles"):
        out.append(_hdr("ENTRADA DE BALLENA  (horizonte %s)" % a["horizon_label"]))
        out.append("  %sUmbral por impacto en precio, no por dolares: lo que es una"
                   "\n  ballena en un pool de $20k es ruido en uno de $2M.%s"
                   % (_c("dim"), _c("r")))
        out.append("")
        out.append("  %s%-16s %-12s %8s %8s   %s%s" % (
            _c("dim"), "nivel", "umbral", "P entra", "P sale", "fiabilidad", _c("r")))
        for n in w["niveles"]:
            pc_ = n["p_compra"] * 100
            col = _c("g") if pc_ >= 60 else (_c("y") if pc_ >= 25 else _c("dim"))
            if n.get("muy_extrapolado"):
                fia = "%sextrapolado %.0fx sobre lo visto%s" % (
                    _c("red"), n["sobre_max"] or 0, _c("r"))
            elif n.get("extrapolado"):
                fia = "%sajuste de cola (%.1fx)%s" % (_c("y"), n["sobre_max"] or 0, _c("r"))
            else:
                fia = "%s%d observadas%s" % (_c("g"), n["observadas"], _c("r"))
            out.append("  %-16s %-12s %s%7.1f%%%s %7.1f%%   %s" % (
                n["nombre"], money(n["umbral"]), col, pc_, _c("r"),
                (n["p_venta"] or 0) * 100, fia))

        t = w.get("titular")
        if t:
            out.append("")
            out.append("  %sLo mas informativo: %s%s de %s%s%s -> %s%.0f%%%s en %s"
                       "   (una cada %.0f min)" % (
                           _c("dim"), _c("r"), t["nombre"], _c("b"),
                           money(t["umbral"]), _c("r"),
                           _c("b"), t["p_compra"] * 100, _c("r"),
                           a["horizon_label"], t["espera_min"] or 0))
        if w.get("max_observado"):
            out.append("  %smayor compra observada: %s · cola alfa=%.2f · %d compras"
                       " en la ventana%s" % (
                           _c("dim"), money(w["max_observado"]), w.get("alfa") or 0,
                           a["flow"]["buys"], _c("r")))
        for m in w.get("motivos", []):
            out.append("  %s· %s%s" % (_c("cy"), m, _c("r")))
        if w.get("ajuste") and abs(w["ajuste"] - 1.0) > 0.05:
            out.append("  %stasa base ajustada x%.2f por el contexto%s"
                       % (_c("dim"), w["ajuste"], _c("r")))
        if not w.get("fiable"):
            out.append("  %s⚠ ventana corta: la tasa esta poco determinada%s"
                       % (_c("y"), _c("r")))

    # ---- auditoria ------------------------------------------------------
    if show_why:
        out.append(_hdr("POR QUE ESAS CIFRAS"))
        pr = pred["prior"]
        out.append("  Regimen: %s%s%s   base rate  SUBIDA %.0f%% / RANGO %.0f%% / BAJADA %.0f%%" % (
            _c("mag"), pred["regime"], _c("r"),
            pr["SUBIDA"] * 100, pr["RANGO"] * 100, pr["BAJADA"] * 100))
        out.append("  Confianza en los datos: %.0f%%  %s(atenua toda la evidencia)%s" % (
            pred["confidence"] * 100, _c("dim"), _c("r")))
        out.append("")
        out.append("  %s%-16s %-11s %6s   %8s %8s %8s%s" % (
            _c("dim"), "señal", "grupo", "z", "→SUBIDA", "→RANGO", "→BAJADA", _c("r")))
        for c in pred["contrib"][:12]:
            d = c["deltas"]
            out.append("  %-16s %-11s %+6.2f   %+8.3f %+8.3f %+8.3f" % (
                c["signal"], c.get("group", "-"), c["z"],
                d["SUBIDA"], d["RANGO"], d["BAJADA"]))
        if not pred["contrib"]:
            out.append("  %sninguna señal activa: se devuelve el base rate puro%s" % (
                _c("dim"), _c("r")))

        if pred.get("capped_groups"):
            out.append("\n  %sGrupos limitados por correlacion: %s%s" % (
                _c("y"), ", ".join(pred["capped_groups"]), _c("r")))
        if pred.get("global_capped"):
            out.append("  %sEvidencia total limitada por el tope global.%s" % (
                _c("y"), _c("r")))

        out.append("\n  %sCada fila desplaza el log-odds. La probabilidad final es el"
                   " softmax de\n  log(base rate) + Σ(señal × peso × confianza), con las"
                   " señales\n  correlacionadas agrupadas y topadas para no contar dos"
                   " veces lo mismo.%s" % (_c("dim"), _c("r")))

    if explicar:
        from . import explain
        etiqueta = {"SUBIDA": "LA SUBIDA", "RANGO": "EL RANGO",
                    "BAJADA": "LA BAJADA"}.get(explicar, explicar)
        out.append(_hdr("POR QUE %s, EN DETALLE" % etiqueta))
        col = {"SUBIDA": _c("g"), "RANGO": _c("y"),
               "BAJADA": _c("red")}.get(explicar, "")
        for i, par in enumerate(explain.narrate(a, explicar)):
            out.append("")
            out.append((col if i == 0 else "") + _wrap(par) + _c("r"))

    if a["warnings"]:
        out.append(_hdr("AVISOS DE CALIDAD DE DATOS"))
        for w in a["warnings"]:
            out.append("  %s· %s%s" % (_c("y"), w, _c("r")))

    out.append("\n%s%s%s" % (_c("dim"), "─" * 66, _c("r")))
    out.append("%sEsto es un modelo estadistico sobre un mercado donde el 68,67%% de los"
               "\ntokens muere el mismo dia y el 0,26%% gradua. Las probabilidades son"
               "\ncondicionales y estan mal calibradas por definicion en las colas."
               "\nNo es consejo financiero.%s" % (_c("dim"), _c("r")))
    out.append("%sgenerado %s%s" % (
        _c("dim"), time.strftime("%Y-%m-%d %H:%M:%S"), _c("r")))
    return "\n".join(out)
