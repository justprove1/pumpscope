"""Explicacion en prosa de un escenario.

Norma de la casa: cada frase lleva un numero que sale del analisis. Nada de
relleno generico -- si una frase no puede apoyarse en una cifra medida, no se
escribe. El texto se compone de las mismas contribuciones que alimentan la
tabla de auditoria, asi que no puede contradecirlas.
"""

from . import model

# Como se lee cada señal cuando empuja a favor y cuando empuja en contra.
_DETALLE = {
    "momentum": (
        "el precio ya venia subiendo antes de mirarlo (momentum de {z:+.1f} sigmas)",
        "el precio venia cayendo ({z:+.1f} sigmas de momentum)"),
    "sobreextension": (
        "esta estirado sobre su media movil, y eso historicamente revierte",
        None),
    "presion_compra": (
        "entra mas dinero del que sale",
        "sale mas dinero del que entra"),
    "caida_desde_ath": (
        "arrastra un desplome desde su maximo del que rara vez se vuelve rapido",
        None),
    "concentracion": (
        "el volumen esta en muy pocas manos capaces de salir de golpe",
        "el volumen esta repartido entre muchas wallets, sin una sola que domine"),
    "dev_vendiendo": (
        "el propio creador del token esta vendiendo",
        None),
    "liquidez_fina": (
        "la liquidez es tan fina que una sola venta mueve el precio",
        "la liquidez es lo bastante profunda para absorber ordenes grandes"),
    "rotacion_rara": (
        "rota mucho mas volumen del que su liquidez justifica, señal tipica de "
        "volumen inflado",
        None),
    "actividad_cae": (
        "el ritmo de operaciones se esta apagando",
        "el ritmo de operaciones se esta acelerando"),
    "participacion": (
        "hay muchas wallets distintas operando en lugar de cuatro repitiendo",
        "hay muy pocas wallets distintas operando"),
    "cerca_graduar": (
        "la curva esta en su tramo final y la graduacion actua como iman",
        None),
    "estancado": (
        "lleva horas sin avanzar en la curva",
        None),
    "tendencia_align": (
        "las ventanas de 5m, 1h, 6h y 24h apuntan todas en la misma direccion",
        "las ventanas de tiempo apuntan todas hacia abajo"),
    "agotamiento": (
        "sube en las ventanas largas pero ya se gira en las cortas, que es "
        "como se forma un techo",
        None),
}


def _lista(frases):
    """Enumera con punto y coma: las frases largas pueden llevar comas dentro."""
    if not frases:
        return ""
    if len(frases) == 1:
        return frases[0]
    return "; ".join(frases[:-1]) + "; y " + frases[-1]


def _lista_corta(items):
    """Enumera con coma. Para terminos sueltos, donde el punto y coma chirria."""
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " y " + items[-1]


def _frase(c, clase):
    par = _DETALLE.get(c["signal"])
    if not par:
        return None
    txt = par[0] if c["z"] >= 0 else par[1]
    if not txt:
        return None
    return txt.format(z=c["z"])


def _pct(x):
    return "%.1f%%" % (x * 100)


def narrate(a, clase=model.UP):
    """Devuelve una explicacion en parrafos del escenario pedido."""
    pred = a["pred"]
    prob = pred["probs"][clase]
    prior = pred["prior"][clase]
    conf = pred["confidence"]
    sc = next((s for s in a["scenarios"] if s["name"] == clase), None)
    parr = []

    nombre = {model.UP: "subida", model.RANGE: "rango",
              model.DOWN: "bajada"}[clase]
    art = "el" if clase == model.RANGE else "la"       # el rango / la subida
    un = "un" if clase == model.RANGE else "una"

    # --- 1. de donde parte -------------------------------------------------
    reg = pred["regime"].replace("_", " ")
    if pred["regime"] == "graduado":
        ctx = ("El token ya graduo la curva y cotiza en el AMM, asi que parte del "
               "regimen 'graduado'")
    else:
        pr = a["curve"].get("progress")
        ctx = ("El token sigue en la bonding curve, al %s de recorrido, lo que lo "
               "situa en el regimen '%s'" % (
                   _pct(pr) if pr is not None else "inicio", reg))
    p1 = ("%s. Antes de mirar un solo dato de este token concreto, la estadistica "
          "de su regimen ya asigna un %s a %s %s: ese es el punto de partida, y "
          "sale de que la inmensa mayoria de los tokens de pump.fun mueren pronto "
          "(el 68,67%% no vuelve a operar despues del dia de lanzamiento). "
          "Todo lo que sigue son ajustes sobre esa cifra."
          % (ctx, _pct(prior), art, nombre))
    parr.append(p1)

    # --- 2. que movio la probabilidad --------------------------------------
    favor = [c for c in pred["contrib"] if c["deltas"][clase] > 0.012]
    contra = [c for c in pred["contrib"] if c["deltas"][clase] < -0.012]
    favor.sort(key=lambda c: -c["deltas"][clase])
    contra.sort(key=lambda c: c["deltas"][clase])

    delta = prob - prior
    if abs(delta) < 0.015:
        parr.append("La evidencia medida no mueve practicamente esa cifra: se "
                    "queda en %s. Cuando las señales se cancelan entre si, el "
                    "modelo devuelve el base rate en vez de inventarse una "
                    "opinion." % _pct(prob))
    else:
        verbo = "sube" if delta > 0 else "baja"
        frases = [f for f in (_frase(c, clase) for c in favor[:3]) if f]
        if frases:
            cuerpo = _lista(frases)
            n = len(frases)
            cuantas = {1: "una cosa", 2: "dos cosas", 3: "tres cosas"}.get(n, "varias cosas")
            parr.append("A favor de %s %s pesa%s %s: %s. En conjunto, eso %s la "
                        "probabilidad del %s de partida al %s."
                        % (art, nombre, "n" if n > 1 else "", cuantas, cuerpo,
                           verbo, _pct(prior), _pct(prob)))
        else:
            parr.append("La evidencia %s la probabilidad del %s al %s."
                        % (verbo, _pct(prior), _pct(prob)))

    # --- 3. lo que juega en contra -----------------------------------------
    fr_c = [f for f in (_frase(c, clase) for c in contra[:3]) if f]
    if fr_c:
        cuerpo = _lista(fr_c)
        parr.append("En contra juega que %s. Esto es lo que impide que la cifra "
                    "sea mas alta, y es tambien lo primero que habria que ver "
                    "corregirse para que el escenario gane fuerza." % cuerpo)

    if pred.get("capped_groups"):
        parr.append("Hay que matizar una cosa: varias de esas señales miden lo "
                    "mismo por vias distintas (un token grande y sano tiene a la "
                    "vez muchas wallets, liquidez profunda y poca concentracion). "
                    "Sumarlas como pruebas independientes inflaria la confianza, "
                    "asi que %s se ha%s recortado para no contar dos veces el "
                    "mismo hecho."
                    % (("el grupo '%s'" % pred["capped_groups"][0])
                       if len(pred["capped_groups"]) == 1
                       else ("los grupos " + _lista_corta(
                           ["'%s'" % g for g in pred["capped_groups"]])),
                       "" if len(pred["capped_groups"]) == 1 else "n"))

    # --- 4. el objetivo ----------------------------------------------------
    if sc and sc.get("target"):
        origen = sc.get("source", "volatilidad")
        if origen in ("pivote", "ATH", "GRADUACION", "nivel"):
            de_donde = {
                "pivote": "coincide con un pivote donde el precio ya reacciono antes",
                "ATH": "es el maximo historico del token",
                "GRADUACION": "es el precio exacto al que la curva se cierra y el "
                              "token migra al AMM, un umbral mecanico del protocolo",
                "nivel": "es un nivel estructural del grafico",
            }[origen]
            obj = ("El objetivo de %s (%+.1f%%) no es una cifra redonda: %s. "
                   "El precio tiende a reaccionar donde hay ordenes acumuladas, "
                   "no en un multiplo estadistico."
                   % (_money(sc["target"]), sc.get("pct") or 0, de_donde))
        else:
            obj = ("El objetivo de %s (%+.1f%%) sale de la distribucion de "
                   "retornos del propio token: no habia ningun nivel estructural "
                   "cerca al que anclarlo."
                   % (_money(sc["target"]), sc.get("pct") or 0))
        if sc.get("mcap"):
            obj += " En capitalizacion eso son %s." % _money_full(sc["mcap"])
        if sc.get("mcap_vs_grad"):
            obj += " Puesto en contexto: %s." % sc["mcap_vs_grad"]
        parr.append(obj)

        if a.get("boot_n"):
            parr.append("Ese numero es la mediana de la cola: remuestreando por "
                        "bloques los retornos reales del token se simulan %d "
                        "recorridos hasta el horizonte, y de los que acaban en "
                        "este escenario, la mitad llega mas lejos y la mitad se "
                        "queda mas aca. No es el techo posible, es el resultado "
                        "tipico si el escenario ocurre."
                        % a["boot_n"])

    # --- 5. limites --------------------------------------------------------
    lim = []
    if conf < 0.55:
        lim.append("la confianza en los datos es baja (%s), asi que toda la "
                   "evidencia va atenuada y la cifra se apoya sobre todo en el "
                   "base rate" % _pct(conf))
    if a.get("horizon_recortado"):
        lim.append("el horizonte tuvo que recortarse a %s porque el token no "
                   "tiene mas historico" % a["horizon_label"])
    if a["liq"]["liq_usd"] < 15000:
        lim.append("con %s de liquidez, el deslizamiento de una orden mediana "
                   "puede comerse buena parte del recorrido"
                   % _money(a["liq"]["liq_usd"]))
    if a["flow"]["n"] < 40:
        lim.append("solo hay %d operaciones en la ventana, poca muestra para "
                   "leer el flujo" % a["flow"]["n"])
    if lim:
        cuerpo = _lista(lim)
        parr.append("Con todo, conviene leerlo con reservas: %s." % cuerpo)

    parr.append("Y el recordatorio de fondo: esto es una probabilidad "
                "condicional sobre un mercado donde el 0,26%% de los tokens "
                "gradua. %s %s del %s tambien significa un %s de que no pase."
                % (un.capitalize(), nombre, _pct(prob), _pct(1 - prob)))
    return parr


def _money_full(x):
    """Capitalizacion con todos los digitos, separador español."""
    if x is None:
        return "n/d"
    if abs(x) >= 1000:
        return "$" + "{:,.0f}".format(x).replace(",", ".")
    return _money(x)


def _money(x):
    if x is None:
        return "n/d"
    if x == 0:
        return "$0"
    ax = abs(x)
    if ax >= 1_000_000:
        return "$%.2fM" % (x / 1e6)
    if ax >= 1_000:
        return "$%.1fk" % (x / 1e3)
    if ax >= 1:
        return "$%.2f" % x
    if ax >= 1e-6:
        return "$%.8f" % x
    return "$%.10f" % x
