"""Radar de memecoins recien nacidas.

Lo que este modulo NO hace
--------------------------
No sabe cual va a subir. Nadie lo sabe. El dato duro: de 832.941 lanzamientos
analizados entre mayo y junio de 2026, gradua el 0,198% -- uno de cada 505. Un
buscador que prometiera acertar estaria mintiendo, y lo unico honesto es
ordenar candidatos por señales medibles y enseñar cuanto pesa cada una.

Lo que si hace, y por que
-------------------------
Un token de dos minutos no tiene grafico, ni volumen, ni historia: casi todas
las señales del analisis normal estan vacias. Pero hay una que si existe desde
el segundo cero, y la literatura la señala como el predictor temprano mas
fuerte: EL HISTORIAL DE QUIEN LO LANZA.

pump.fun expone `/coins?creator=<wallet>` (verificado en vivo), asi que de
cualquier token recien nacido se puede sacar:
  - cuantos tokens ha lanzado antes esa wallet
  - cuantos graduaron  ->  su tasa personal frente al 0,198% general
  - que capitalizacion maxima alcanzaron

Un creador con 4 graduados de 70 tiene una tasa del 6%: veintitres veces la
media. Eso no garantiza nada sobre el siguiente, pero es la diferencia entre
elegir a ciegas y elegir con una base.

La segunda señal es la narrativa, y se deriva de los propios datos en vez de
depender de noticias: se miran los tokens que estan funcionando AHORA, se
extraen las palabras de sus nombres, y se mide cuanto encaja el recien nacido
con lo que el mercado esta premiando en este momento.
"""

import math
import re
import time

from . import sources

# Tasa de graduacion de referencia. Fuente: analisis de supervivencia sobre
# 832.941 lanzamientos (may-jun 2026), 0,198% con IC95% [0,189%, 0,208%].
# Era 0,63% en sep-oct 2025: el mercado ha empeorado 3,18 veces.
BASE_GRADUACION = 0.00198

_cache_creador = {}
_cache_narrativa = {"t": 0, "palabras": {}}

# Palabras que aparecen en demasiados nombres para significar nada.
_VACIAS = {
    "the", "coin", "token", "official", "inu", "sol", "solana", "pump", "meme",
    "de", "la", "el", "and", "for", "you", "my", "on", "to", "in", "is", "it",
    "a", "i", "of", "by", "cat", "dog",
}


def _f(d, k, default=0.0):
    try:
        v = d.get(k)
        return default if v is None else float(v)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------
# Recien nacidos
# --------------------------------------------------------------------------

def recien_nacidos(n=30, max_edad_min=90):
    """Los tokens mas nuevos de pump.fun, ordenados por fecha de creacion."""
    try:
        d = sources.get_json(
            "%s/coins?sort=created_timestamp&order=DESC&limit=%d&offset=0"
            % (sources.PUMP_API, min(100, n * 2)),
            headers={"Origin": "https://pump.fun", "Referer": "https://pump.fun/"})
    except sources.SourceError as e:
        sources._soft_errors.append("listado de recien nacidos (%s)" % e)
        return []
    if not isinstance(d, list):
        return []
    ahora = time.time() * 1000.0
    out = []
    for c in d:
        ts = _f(c, "created_timestamp")
        if not ts:
            continue
        edad = (ahora - ts) / 60000.0
        if edad <= max_edad_min:
            c["_edad_min"] = edad
            out.append(c)
    return out[:n]


# --------------------------------------------------------------------------
# Historial del creador -- la señal fuerte
# --------------------------------------------------------------------------

def historial_creador(wallet):
    """Track record de la wallet que lanzo el token.

    Devuelve None si no se puede consultar. Se cachea porque un mismo creador
    aparece varias veces en una tanda de recien nacidos.
    """
    if not wallet:
        return None
    if wallet in _cache_creador:
        return _cache_creador[wallet]

    # La API tope cada pagina en 70 e ignora limit mayores, asi que hay que
    # paginar: sin esto un creador con 102 tokens se leia como 102 -> 70 y su
    # tasa de graduacion salia inflada un 46%.
    d, offset, truncado = [], 0, False
    for _ in range(6):                    # hasta 420 tokens
        try:
            pag = sources.get_json(
                "%s/coins?creator=%s&limit=70&offset=%d"
                % (sources.PUMP_API, wallet, offset),
                headers={"Origin": "https://pump.fun", "Referer": "https://pump.fun/"})
        except sources.SourceError:
            break
        if not isinstance(pag, list) or not pag:
            break
        d.extend(pag)
        if len(pag) < 70:
            break
        offset += 70
    else:
        truncado = True

    if not d:
        _cache_creador[wallet] = None
        return None

    total = len(d)
    graduados = sum(1 for c in d if c.get("complete"))
    aths = sorted((_f(c, "ath_market_cap") for c in d), reverse=True)
    mcaps = sorted((_f(c, "usd_market_cap") for c in d), reverse=True)

    # Ritmo de lanzamiento: muchos tokens en poco tiempo es fabrica de spam.
    tss = sorted(_f(c, "created_timestamp") for c in d if _f(c, "created_timestamp"))
    por_dia = None
    if len(tss) >= 3:
        dias = (tss[-1] - tss[0]) / 86400000.0
        if dias > 0.04:
            por_dia = len(tss) / dias

    h = {
        "wallet": wallet,
        "lanzados": total,
        "graduados": graduados,
        "tasa": graduados / total if total else 0.0,
        "veces_base": (graduados / total) / BASE_GRADUACION if total else 0.0,
        "mejor_mcap": mcaps[0] if mcaps else 0.0,
        "mediana_ath": aths[len(aths) // 2] if aths else 0.0,
        "por_dia": por_dia,
        "primerizo": total <= 1,
        "truncado": truncado,
    }
    _cache_creador[wallet] = h
    return h


# --------------------------------------------------------------------------
# Narrativa: que palabras estan funcionando ahora
# --------------------------------------------------------------------------

def _palabras(txt):
    return [w for w in re.findall(r"[a-z0-9]+", (txt or "").lower())
            if len(w) >= 3 and w not in _VACIAS]


def narrativas_calientes(ttl=900):
    """Palabras frecuentes entre los tokens que estan funcionando ahora.

    Se deriva de los datos en lugar de depender de una fuente de noticias: los
    tokens con mayor capitalizacion entre los lanzados recientemente indican
    que tema esta premiando el mercado en este momento. Si 'perro' aparece en
    seis de los que funcionan, un recien nacido con esa palabra hereda algo de
    esa corriente.
    """
    ahora = time.time()
    if _cache_narrativa["palabras"] and ahora - _cache_narrativa["t"] < ttl:
        return _cache_narrativa["palabras"]

    try:
        d = sources.get_json(
            "%s/coins?sort=market_cap&order=DESC&limit=100&offset=0" % sources.PUMP_API,
            headers={"Origin": "https://pump.fun", "Referer": "https://pump.fun/"})
    except sources.SourceError:
        return _cache_narrativa["palabras"]
    if not isinstance(d, list):
        return _cache_narrativa["palabras"]

    limite = ahora * 1000 - 14 * 86400000      # solo lo lanzado en 14 dias
    frec = {}
    for c in d:
        if _f(c, "created_timestamp") < limite:
            continue
        for w in set(_palabras(c.get("name")) + _palabras(c.get("symbol"))):
            frec[w] = frec.get(w, 0) + 1
    frec = {w: n for w, n in frec.items() if n >= 2}
    _cache_narrativa.update({"t": ahora, "palabras": frec})
    return frec


# --------------------------------------------------------------------------
# Puntuacion
# --------------------------------------------------------------------------

def puntua(coin, hist, narrativa):
    """Puntua un recien nacido. Devuelve dict con score y motivos."""
    a_favor, en_contra = [], []
    pts = 0.0

    edad = coin.get("_edad_min", 0.0)
    mcap = _f(coin, "usd_market_cap")
    ath = _f(coin, "ath_market_cap")
    replies = int(_f(coin, "reply_count"))

    # --- 1. historial del creador (lo que mas pesa) ----------------------
    if hist is None:
        en_contra.append("no se pudo consultar el historial del creador")
    elif hist["primerizo"]:
        pts -= 0.3
        a_favor.append("primer token de esta wallet (sin historial, ni bueno ni malo)")
    else:
        t = hist["tasa"]
        if t > 0:
            g = min(3.2, math.log10(hist["veces_base"] + 1) * 2.4)
            pts += g
            # Con historial truncado por el tope de paginacion, la cifra es
            # un minimo: se marca con '+' para no darla por exacta.
            mas = "+" if hist.get("truncado") else ""
            a_favor.append("el creador gradúa el %.1f%% (%d de %d%s): %.0fx la media"
                           % (t * 100, hist["graduados"], hist["lanzados"], mas,
                              hist["veces_base"]))
        else:
            pts -= 1.4
            en_contra.append("el creador lleva %d%s tokens y ninguno graduó"
                             % (hist["lanzados"], "+" if hist.get("truncado") else ""))
        pd = hist.get("por_dia")
        if pd and pd > 12:
            pts -= 1.6
            en_contra.append("lanza %.0f tokens al día: fábrica de spam" % pd)
        elif pd and pd < 1.5 and hist["lanzados"] >= 4:
            pts += 0.6
            a_favor.append("lanza con cuentagotas (%.1f/día), no en masa" % pd)
        if hist["mejor_mcap"] > 500000:
            pts += 0.8
            a_favor.append("su mejor token llegó a %s" % _money(hist["mejor_mcap"]))

    # --- 2. traccion temprana --------------------------------------------
    # Un token arranca en ~$2.000 de capitalizacion. Lo que suba sobre eso en
    # los primeros minutos es demanda real entrando.
    if mcap > 0 and edad > 0.5:
        x = mcap / 2000.0
        if x > 1.6:
            g = min(2.4, math.log(x) * 1.5)
            pts += g
            a_favor.append("x%.1f sobre el precio de salida en %.0f min" % (x, edad))
        elif x < 0.9:
            pts -= 0.8
            en_contra.append("por debajo del precio de salida")
    if replies >= 8:
        pts += 0.7
        a_favor.append("%d comentarios ya" % replies)

    # Ya se desplomo desde su maximo: llegaste tarde.
    if ath > 0 and mcap > 0 and mcap / ath < 0.45 and ath > 3000:
        pts -= 1.5
        en_contra.append("ya cayó un %.0f%% desde su máximo" % ((1 - mcap / ath) * 100))

    # --- 3. narrativa -----------------------------------------------------
    ws = set(_palabras(coin.get("name")) + _palabras(coin.get("symbol")))
    hits = [(w, narrativa[w]) for w in ws if w in narrativa]
    if hits:
        hits.sort(key=lambda h: -h[1])
        g = min(1.5, 0.45 * len(hits) + 0.12 * hits[0][1])
        pts += g
        a_favor.append("encaja con lo que funciona ahora: %s"
                       % ", ".join("'%s' (%d activos)" % (w, n) for w, n in hits[:2]))

    # --- 4. señales sociales ---------------------------------------------
    if coin.get("twitter") or coin.get("telegram") or coin.get("website"):
        pts += 0.5
        a_favor.append("tiene presencia social enlazada")
    else:
        pts -= 0.4
        en_contra.append("sin redes ni web")

    if coin.get("is_currently_live"):
        pts += 0.4
        a_favor.append("el creador está en directo")

    return {
        "mint": coin.get("mint"), "nombre": coin.get("name"),
        "simbolo": coin.get("symbol"), "edad_min": edad,
        "mcap": mcap, "ath": ath, "replies": replies,
        "creador": coin.get("creator"),
        "hist": hist, "score": pts,
        "a_favor": a_favor, "en_contra": en_contra,
    }


def _money(x):
    if x is None:
        return "n/d"
    if abs(x) >= 1_000_000:
        return "$%.2fM" % (x / 1e6)
    if abs(x) >= 1_000:
        return "$%.1fk" % (x / 1e3)
    return "$%.0f" % x


def busca(limite=10, candidatos=26, max_edad_min=90):
    """Rastrea recien nacidos y los devuelve ordenados por puntuacion."""
    sources.take_errors()
    nuevos = recien_nacidos(n=candidatos, max_edad_min=max_edad_min)
    narrativa = narrativas_calientes()

    filas = []
    for c in nuevos:
        try:
            h = historial_creador(c.get("creator"))
            filas.append(puntua(c, h, narrativa))
        except Exception:
            continue
    filas.sort(key=lambda r: -r["score"])

    # Contexto de base rate sobre la propia tanda.
    con_hist = [r for r in filas if r["hist"] and not r["hist"]["primerizo"]]
    return {
        "filas": filas[:limite],
        "analizados": len(filas),
        "base_graduacion": BASE_GRADUACION,
        "uno_de_cada": int(round(1 / BASE_GRADUACION)),
        "con_historial": len(con_hist),
        "narrativa_top": sorted(narrativa.items(), key=lambda kv: -kv[1])[:8],
        "avisos": sources.take_errors(),
    }
