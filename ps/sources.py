"""Capa de datos: todas las fuentes son gratuitas y sin API key.

Verificado en vivo (agosto 2026):
  - frontend-api-v3.pump.fun/coins/{mint}  -> estado de la curva, creator, ATH
  - api.geckoterminal.com                  -> velas OHLCV reales + trades con wallet
  - api.dexscreener.com                    -> buckets de volumen/txns m5/h1/h6/h24

Solo stdlib: sin dependencias que instalar.
"""

import calendar
import json
import time
import urllib.error
import urllib.parse
import urllib.request

PUMP_API = "https://frontend-api-v3.pump.fun"
GT_API = "https://api.geckoterminal.com/api/v2"
DS_API = "https://api.dexscreener.com"

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# GeckoTerminal permite ~30 req/min en el plan gratuito. Espaciamos las llamadas
# para no comernos un 429 a mitad de un analisis.
_MIN_GAP = {"api.geckoterminal.com": 2.6}
_last_call = {}

# Fallos blandos (una fuente que no respondio). Se acumulan aqui para que el
# informe pueda avisar de que falta un dato, en lugar de tratar el hueco como
# si fuese una lectura de cero.
_soft_errors = []


def take_errors():
    """Devuelve y limpia los fallos acumulados."""
    global _soft_errors
    errs, _soft_errors = _soft_errors, []
    return errs


class SourceError(Exception):
    pass


def _throttle(host):
    gap = _MIN_GAP.get(host)
    if not gap:
        return
    prev = _last_call.get(host)
    if prev is not None:
        wait = gap - (time.time() - prev)
        if wait > 0:
            time.sleep(wait)
    _last_call[host] = time.time()


def get_json(url, headers=None, timeout=25, retries=3):
    """GET + parse JSON, con reintento exponencial en 429/5xx."""
    host = urllib.parse.urlsplit(url).hostname or ""
    hdrs = {"User-Agent": _UA, "Accept": "application/json"}
    if headers:
        hdrs.update(headers)

    last_err = None
    for attempt in range(retries):
        _throttle(host)
        req = urllib.request.Request(url, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                # Un 429 de GeckoTerminal necesita una pausa de verdad: reintentar
                # a los 2s solo gasta el siguiente intento.
                back = 6.0 * (attempt + 1) if e.code == 429 else 2.0 * (attempt + 1)
                _last_call[host] = time.time() + back
                time.sleep(back)
                continue
            raise SourceError("HTTP %s en %s" % (e.code, url))
        except Exception as e:  # timeouts, DNS, JSON malformado
            last_err = e
            if attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise SourceError("%s en %s" % (type(e).__name__, url))
    raise SourceError(str(last_err))


# --------------------------------------------------------------------------
# pump.fun
# --------------------------------------------------------------------------

def pump_coin(mint):
    """Estado completo del token en pump.fun.

    Trae reservas virtuales/reales (para la matematica de la curva), el creator
    (para detectar si el dev vende), ATH y el flag `complete` (graduado o no).
    """
    hdrs = {"Origin": "https://pump.fun", "Referer": "https://pump.fun/"}
    return get_json("%s/coins/%s" % (PUMP_API, mint), headers=hdrs)


def sol_price_usd():
    try:
        return float(get_json("%s/sol-price" % PUMP_API)["solPrice"])
    except Exception:
        return None


# --------------------------------------------------------------------------
# GeckoTerminal
# --------------------------------------------------------------------------

def gt_pools_for_token(mint):
    """Pools donde cotiza el token, ordenadas por liquidez descendente."""
    try:
        data = get_json("%s/networks/solana/tokens/%s/pools" % (GT_API, mint)).get("data", [])
    except SourceError as e:
        _soft_errors.append("lista de pools no disponible (%s)" % e)
        return []

    def liq(p):
        try:
            return float(p["attributes"].get("reserve_in_usd") or 0)
        except (TypeError, ValueError):
            return 0.0

    return sorted(data, key=liq, reverse=True)


def gt_ohlcv(pool, timeframe="minute", aggregate=1, limit=300):
    """Velas reales. Devuelve lista de dicts orden cronologico ascendente.

    GeckoTerminal las entrega de mas nueva a mas vieja; aqui se invierten.
    """
    url = "%s/networks/solana/pools/%s/ohlcv/%s?aggregate=%s&limit=%s" % (
        GT_API, pool, timeframe, aggregate, limit,
    )
    try:
        raw = get_json(url)["data"]["attributes"]["ohlcv_list"]
    except (SourceError, KeyError, TypeError) as e:
        _soft_errors.append("velas OHLCV no disponibles (%s)" % e)
        return []

    out = []
    for row in raw:
        if not row or len(row) < 6:
            continue
        try:
            out.append({
                "t": int(row[0]),
                "o": float(row[1]),
                "h": float(row[2]),
                "l": float(row[3]),
                "c": float(row[4]),
                "v": float(row[5]),
            })
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda c: c["t"])
    return out


def gt_trades(pool):
    """Ultimos ~300 trades con wallet, lado y volumen USD."""
    url = "%s/networks/solana/pools/%s/trades" % (GT_API, pool)
    try:
        raw = get_json(url).get("data", [])
    except SourceError as e:
        _soft_errors.append("historial de trades no disponible (%s)" % e)
        return []

    out = []
    for item in raw:
        a = item.get("attributes", {})
        try:
            ts = a.get("block_timestamp", "")
            epoch = int(calendar.timegm(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ"))) if ts else 0
            kind = (a.get("kind") or "").lower()   # buy | sell
            # En una compra el token es el lado 'to'; en una venta, el 'from'.
            px = a.get("price_to_in_usd") if kind == "buy" else a.get("price_from_in_usd")
            out.append({
                "ts": epoch,
                "wallet": a.get("tx_from_address", ""),
                "kind": kind,
                "usd": float(a.get("volume_in_usd") or 0),
                "price": float(px) if px else 0.0,
            })
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda t: t["ts"])
    return out


# --------------------------------------------------------------------------
# DexScreener
# --------------------------------------------------------------------------

def ds_token(mint):
    """Par principal en DexScreener (mayor liquidez)."""
    try:
        pairs = get_json("%s/latest/dex/tokens/%s" % (DS_API, mint)).get("pairs") or []
    except SourceError as e:
        _soft_errors.append("DexScreener no respondio (%s)" % e)
        return None
    if not pairs:
        return None

    def liq(p):
        try:
            return float((p.get("liquidity") or {}).get("usd") or 0)
        except (TypeError, ValueError):
            return 0.0

    return sorted(pairs, key=liq, reverse=True)[0]
