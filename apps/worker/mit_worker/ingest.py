"""Servicio de ingesta: stream -> detector -> base de datos -> Redis (SPEC.md 6).

Une las piezas y no hace nada mas. Toda la logica interesante vive en modulos que se pueden
probar sin red ni base de datos; aqui solo se cablea.

Publica cada token nuevo en Redis para que la API lo reenvie por WebSocket al dashboard. Se
usa pub/sub y no que la API consulte la base en bucle porque el objetivo es tiempo real: un
sondeo cada segundo anadiria hasta un segundo de retraso al presupuesto de SPEC.md 6, que ya
es de un segundo en total.
"""

from __future__ import annotations

import asyncio
import bisect
import collections
import contextlib
import itertools
import json
import logging
import os
import signal
import statistics
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

import redis.asyncio as redis
import websockets
from mit_observability.metrics import IngestMetrics
from mit_pumpfun.constants import PUMPFUN_PROGRAM_ID
from mit_pumpfun.curve import CurveError, CurveState, market_cap_lamports, spot_price_lamports
from mit_pumpfun.detector import DetectedToken, NewTokenDetector
from mit_pumpfun.events import CreateEvent, find_trade_events
from mit_pumpfun.pumpswap import find_pumpswap_trades
from mit_shared.types import LAMPORTS_PER_SOL
from mit_solana.logs_stream import ResilientLogStream
from mit_solana.multi_log_stream import MultiLogStream
from mit_solana.racing_stream import RacingLogStream
from mit_solana.rpc import RpcLimits, SolanaRpc
from sqlalchemy.ext.asyncio import create_async_engine

from mit_worker.analysis import AnalysisPipeline
from mit_worker.repository import TokenRepository

LOGGER = logging.getLogger("mit.ingest")
CHANNEL_NEW_TOKENS = "mit:tokens.new"
# Actualizaciones de capitalizacion en vivo, una por operacion (con throttle).
CHANNEL_CAP = "mit:tokens.cap"

DEFAULT_WSS = "wss://api.mainnet-beta.solana.com"
# Segundo endpoint publico que SI responde a logsSubscribe. Verificado: 993 eventos en 8 s,
# los mismos que el oficial.
PUBLICNODE_WSS = "wss://solana-rpc.publicnode.com"

# Supply constante de todo token de pump.fun (10^9 tokens x 10^6 decimales). El TradeEvent no
# lo trae, pero la curva lo fija: sirve para calcular la cap desde las reservas de cada trade.
PUMPFUN_TOTAL_SUPPLY = 1_000_000_000_000_000
# Como maximo una actualizacion de cap por token cada tanto. Bajado a 0,1 s: el objetivo es que
# la parte del retraso que controlamos quede por debajo de 200 ms. Un token muy activo genera
# mas mensajes en Redis, que los absorbe sin problema; el limite real esta en el WebSocket
# publico (1.325 ms medidos), no aqui.
CAP_THROTTLE_SECONDS = 0.1
# Cuantos mints recientes se siguen para cap en vivo. Acota memoria y trabajo: solo los tokens
# que estan (o estuvieron hace poco) en el radar reciben actualizaciones.
TRACKED_MINTS_CAP = 500

# --- Probabilidad empirica de llegar a la zona de ~$50k ------------------------------------
# Cap con la que nace todo token de pump.fun (misma que en el radar).
BIRTH_CAP_SOL = 27.96
# Objetivo "+$50k": ~300 SOL de capitalizacion, ya en la recta de graduacion. Es aproximado:
# el valor en dolares depende del precio de SOL, que sin proveedor de precios no medimos aqui.
TARGET_MCAP_SOL = 300.0
TARGET_GROWTH = TARGET_MCAP_SOL / BIRTH_CAP_SOL  # ~x10.7 desde el nacimiento
# Niveles de crecimiento sobre los que se condiciona la probabilidad.
GROWTH_BUCKETS = (1.0, 1.5, 2.0, 3.0, 5.0, 8.0)
# Por debajo de esta muestra NO se da un porcentaje: seria ruido, no una tasa.
MIN_OUTCOME_SAMPLE = 25
# Tope de mints distintos recordados por sesion. Cota de memoria dura.
OUTCOME_MEMORY_CAP = 40_000

# Clave Redis con la foto de "los que mas han explotado" (la API la sirve tal cual).
KEY_TOP_MOVERS = "mit:top_movers"
# Foto de los tokens que estan AHORA en la banda media (~$30-60k).
KEY_HOT_ZONE = "mit:hot_zone"
# Tokens con lanzamiento en estampida (el patron de V713/VanillaFunk).
KEY_STAMPEDE = "mit:stampede"
# Tokens en camino de graduarse: la señal mas rara y valiosa (~1-3% lo consigue).
KEY_GRADUATING = "mit:graduating"
# Series: nombres que se repiten y donde algun miembro anterior YA bombeo.
KEY_SERIES = "mit:series"

# --- Progreso hacia la graduacion -----------------------------------------------------------
# Verificado contra mainnet: todo token nace con 793.100.000.000.000 tokens disponibles para
# vender y 30 SOL de reserva virtual. Gradua cuando esos tokens se agotan.
INITIAL_REAL_TOKENS = 793_100_000_000_000
INITIAL_VIRTUAL_SOL_LAMPORTS = 30_000_000_000
# SOL que hay que recaudar para graduar, en lamports. La curva pasa de 30 SOL virtuales al
# nacer a ~115 al completarse.
GRADUATION_RAISE_LAMPORTS = 85_000_000_000
# Progreso a partir del cual un token entra en la lista de vigilancia.
GRADUATING_MIN_PROGRESS = 0.35
# Cuantos tokens se publican en la foto.
GRADUATING_MAX = 25


def _graduation_progress(virtual_sol_reserves: int) -> float:
    """Fraccion del camino a la graduacion, de 0 a 1.

    Se deriva de las reservas VIRTUALES de SOL y no de `real_token_reserves` porque el
    TradeEvent solo trae las virtuales: usar las reales obligaria a una llamada RPC por token
    y por operacion, que es justo lo que no se puede permitir a 30 tokens por minuto.

    La relacion es exacta: la curva conserva x*y=k, asi que los tokens vendidos se deducen del
    SOL acumulado. Nace en 30 SOL virtuales; el reparto de la curva se agota al alcanzar el SOL
    de graduacion (~115 virtuales = 30 iniciales + ~85 recaudados).
    """
    raised = virtual_sol_reserves - INITIAL_VIRTUAL_SOL_LAMPORTS
    if raised <= 0:
        return 0.0
    return min(1.0, raised / GRADUATION_RAISE_LAMPORTS)


# --- Deteccion de "estampida" en el lanzamiento ---------------------------------------------
# Ventana desde el nacimiento en la que se cuentan las operaciones.
LAUNCH_WINDOW_SECONDS = 15.0
# Umbral MEDIDO, no inventado. Sobre 26 lanzamientos observados en mainnet: mediana 8 tx en esa
# ventana, p90 37, y solo 1 de 26 supero 100. V713/VanillaFunk hizo 281 en sus primeros 10 s.
# 100 deja fuera el 96% de los lanzamientos: es una señal rara de verdad, no ruido.
STAMPEDE_TRADES = 100

# --- Salud posterior: una estampida que ya se desinfla NO es una oportunidad -----------------
# Cuantas lecturas de capitalizacion se conservan por token. Se usa la MEDIANA de esta ventana
# y no el ultimo valor a proposito: un pico de ventas aislado hunde el ultimo dato un instante
# y volveria a subir. Lo que importa es la caida SOSTENIDA, y la mediana ignora el pico suelto.
CAP_HISTORY = 9
# Caida desde el maximo a partir de la cual el token se considera enfriandose / muerto.
COOLING_DRAWDOWN = 0.25
DEAD_DRAWDOWN = 0.50

# --- Seguimiento PROFUNDO de estampidas -----------------------------------------------------
# Solo el ~4% de los lanzamientos entra aqui, asi que se puede registrar CADA operacion sin
# throttle: es lo que permite contar manos unicas, concentracion y trayectoria del ritmo. Con
# 500 mints a la vez seria inviable; con 20 sobra.
DEEP_TRACKED_MAX = 20
# Operaciones conservadas por token seguido. Cota de memoria dura.
DEEP_TRADES_MAX = 3_000
# Ventanas para comparar el ritmo reciente contra el anterior (acelera o se apaga).
RATE_WINDOW_SECONDS = 10.0

# --- Techo empirico -------------------------------------------------------------------------
# ~$100k. Aproximado: sin proveedor de precios de SOL la equivalencia en dolares es orientativa
# y la cifra que manda es la de SOL.
BIG_CAP_SOL = 600.0
# Muestra minima para publicar un techo. Por debajo se devuelve None en vez de inventar.
MIN_CEILING_SAMPLE = 8
# Tokens graduados que se siguen a la vez en PumpSwap. Cada uno es una suscripcion sobre la
# conexion multiplexada; el limite evita que el conjunto crezca sin freno con las horas.
SWAP_WATCH_CAP = 60
# --- Deteccion de SERIES ---------------------------------------------------------------------
# Patron observado en vivo con $TNOS: el mismo simbolo relanzado una y otra vez, cada iteracion
# valiendo aproximadamente la mitad que la anterior (253M -> 118M -> 72,9M -> 43,4M, una cada
# ~45 min). Lo que convierte una serie en interesante NO es que el nombre se repita —eso pasa a
# todas horas y casi siempre son tokens muertos de 3.000 $— sino que algun miembro ANTERIOR haya
# bombeado de verdad. Eso es lo que distingue una serie viva de un nombre popular cualquiera.
SERIES_MIN_MEMBERS = 2
# Un miembro cuenta como "bombeo" si llego a la GRADUACION. El umbral se subio desde x3 tras
# medir la pestana en vivo: con x3 entraban Doge, TIMMY y UP —nombres populares que colisionan
# solos porque se crean ~1.400 tokens por hora—. Los miembros de la serie $TNOS llegaron a 43M,
# 72,9M, 118M y 253M $, muy por encima de graduar, asi que este listón sigue siendo generoso
# para lo que se quiere detectar y elimina el ruido de golpe.
# Debe coincidir con EXPLODE_TARGET_SOL, que se define mas abajo. Un test lo verifica.
SERIES_PUMP_CAP_SOL = 380.0
# Separacion MINIMA entre iteraciones. Es el discriminador que de verdad separa una serie
# orquestada de una casualidad: $TNOS salia cada 43-50 minutos; las colisiones de nombres
# comunes se apilan en segundos (Doge cada 13 s, TIMMY cada 63 s). Nadie relanza a proposito
# el mismo simbolo cada trece segundos.
SERIES_MIN_CADENCE_SECONDS = 300.0
# Ventana: la serie $TNOS observada abarcaba 7 horas de punta a punta.
SERIES_WINDOW_SECONDS = 12 * 3600.0
# Cuantos simbolos distintos se recuerdan. Acotado: se ven ~1.400 tokens por hora.
SERIES_MEMORY = 4000
SERIES_MAX = 25
# Capitalizacion minima para admitir una graduacion como REAL.
#
# Por que hace falta. `_graduation_progress` satura con `min(1.0, ...)`: una sola lectura de
# reservas disparatada da progreso 1,0 y marcaba el token como graduado PARA SIEMPRE. Se observo
# en vivo: el token `quasi` figuraba graduado con 0,045 ◎ y progreso 0,38, algo imposible —un
# token que gradua de verdad no puede volver a operar en la curva, porque su reparto se agota—.
# Un graduado real ronda los 410 ◎ (valor terminal de la curva, visto una y otra vez en los
# datos). Se exige un suelo generoso: descarta la basura sin arriesgar falsos negativos.
GRADUATION_MIN_CAP_SOL = 300.0
# Puntos de la serie de precios que se guardan por estampida en el corpus. Se remuestrea para
# no engordar el fichero, pero CONSERVANDO maximos y minimos: son justo lo que dispara un stop.
CAP_SERIES_MAX = 400


def _downsample(series: list[tuple[float, float]], limit: int) -> list[tuple[float, float]]:
    """Reduce la serie a `limit` puntos sin perder los extremos de cada tramo.

    Un remuestreo ingenuo (uno de cada N) se comeria justo los picos y valles, que son los que
    disparan un trailing stop. Aqui se conserva el maximo y el minimo de cada tramo.
    """
    if len(series) <= limit:
        return list(series)
    bucket = len(series) / (limit / 2)
    kept: list[tuple[float, float]] = []
    index = 0.0
    while int(index) < len(series):
        chunk = series[int(index) : int(index + bucket) or int(index) + 1]
        if chunk:
            lowest = min(chunk, key=lambda p: p[1])
            highest = max(chunk, key=lambda p: p[1])
            kept.extend(sorted({lowest, highest}, key=lambda p: p[0]))
        index += bucket
    return kept


@dataclass
class DeepTrack:
    """Registro operacion a operacion de un token en estampida.

    Guarda lo minimo por operacion —instante, sentido, importe y cartera— porque de esos cuatro
    campos salen todas las metricas que distinguen una estampida real de un montaje de bots.
    """

    mint: str
    birth: float
    trades: list[tuple[float, bool, int, str]] = field(default_factory=list)
    # Serie de capitalizacion: (segundos desde el nacimiento, cap). De aqui sale la FORMA de la
    # subida, que es lo que distingue "subio de golpe" de "subio a trompicones".
    caps: list[tuple[float, float]] = field(default_factory=list)

    def add(self, at: float, is_buy: bool, sol: int, user: str) -> None:
        self.trades.append((at - self.birth, is_buy, sol, user))
        if len(self.trades) > DEEP_TRADES_MAX:
            del self.trades[: len(self.trades) - DEEP_TRADES_MAX]

    def add_cap(self, at: float, cap: float) -> None:
        if cap > 0:
            self.caps.append((at - self.birth, cap))
            if len(self.caps) > DEEP_TRADES_MAX:
                del self.caps[: len(self.caps) - DEEP_TRADES_MAX]

    def climb(self) -> dict[str, Any]:
        """La FORMA de la subida hasta su maximo.

        Tres cosas que el usuario identifico mirando graficas y que aqui se miden:

        - `climb_speed`: cuanta capitalizacion gano por segundo hasta tocar techo. "Subir de
          golpe" es exactamente esto.
        - `monotonic`: que fraccion de los movimientos fueron hacia arriba. Una subida limpia
          tiene pocos retrocesos.
        - `max_dip`: el retroceso mas PROFUNDO sufrido durante la subida. Es la clave del matiz
          "como mucho picos": un token que baja un 10% y sigue es sano; uno que se hunde un 40%
          a mitad de camino esta siendo vendido, aunque luego recupere.
        """
        if len(self.caps) < 3:
            return {}
        peak_value = max(c for _, c in self.caps)
        peak_index = next(i for i, (_, c) in enumerate(self.caps) if c == peak_value)
        if peak_index == 0:
            # El maximo fue la primera lectura: no hubo subida que medir.
            return {"climb_speed": 0.0, "monotonic": 0.0, "max_dip": 0.0}

        rise = self.caps[: peak_index + 1]
        elapsed = rise[-1][0] - rise[0][0]
        speed = (peak_value - rise[0][1]) / elapsed if elapsed > 0 else 0.0

        ups = sum(1 for a, b in itertools.pairwise(rise) if b[1] > a[1])
        monotonic = ups / (len(rise) - 1) if len(rise) > 1 else 0.0

        # Retroceso mas profundo DURANTE la subida.
        running_peak = rise[0][1]
        max_dip = 0.0
        for _, value in rise:
            running_peak = max(running_peak, value)
            if running_peak > 0:
                max_dip = max(max_dip, (running_peak - value) / running_peak)

        return {
            "climb_speed": round(speed, 3),
            "monotonic": round(monotonic, 3),
            "max_dip": round(max_dip, 3),
            "seconds_to_peak": round(rise[-1][0], 1),
        }

    def metrics(self, now: float) -> dict[str, Any]:
        """Las metricas que separan 'mucha gente entrando' de 'cuatro carteras girando'."""
        if not self.trades:
            return {}
        buyers = {u for _, is_buy, _, u in self.trades if is_buy and u}
        sellers = {u for _, is_buy, _, u in self.trades if not is_buy and u}
        buys = sum(1 for _, is_buy, _, _ in self.trades if is_buy)

        volume_by_user: collections.Counter[str] = collections.Counter()
        for _, _, sol, user in self.trades:
            if user:
                volume_by_user[user] += sol
        total_volume = sum(volume_by_user.values())
        top_share = (
            max(volume_by_user.values()) / total_volume if total_volume and volume_by_user else 0.0
        )

        # Ritmo: operaciones de la ultima ventana frente a la anterior. >1 acelera, <1 se apaga.
        elapsed = now - self.birth
        recent = sum(1 for t, _, _, _ in self.trades if t > elapsed - RATE_WINDOW_SECONDS)
        previous = sum(
            1
            for t, _, _, _ in self.trades
            if elapsed - 2 * RATE_WINDOW_SECONDS < t <= elapsed - RATE_WINDOW_SECONDS
        )
        momentum = recent / previous if previous else (1.0 if recent else 0.0)

        unique = len(buyers | sellers)
        return {
            "trades_total": len(self.trades),
            "unique_wallets": unique,
            "unique_buyers": len(buyers),
            # La metrica clave: muchas operaciones por cartera = pocas manos girando volumen.
            "trades_per_wallet": round(len(self.trades) / unique, 2) if unique else 0.0,
            "top_wallet_share": round(top_share, 4),
            "buy_ratio": round(buys / len(self.trades), 4),
            "volume_sol": round(total_volume / LAMPORTS_PER_SOL, 6),
            "recent_rate": recent,
            "momentum": round(momentum, 2),
            "age_seconds": round(elapsed, 1),
        }
TOP_MOVERS_COUNT = 12
# La foto se reescribe como mucho cada tanto. 0,15 s mantiene nuestro tramo por debajo de los
# 200 ms; el coste es CPU del worker reconstruyendo las listas mas a menudo.
TOP_MOVERS_THROTTLE_SECONDS = 0.15
# A partir de este crecimiento un token cuenta como "explosion" y se graba para entrenamiento.
WINNER_GROWTH = 3.0

# --- Zona media "a punto de explotar" (~$30-60k) -------------------------------------------
# Bordes de banda por capitalizacion absoluta en SOL. Aproximados (el $ depende del precio de
# SOL, que sin proveedor de precios no medimos): ~$30k, $40k, $50k, $60k.
CAP_BAND_EDGES_SOL = (170.0, 230.0, 300.0, 360.0)
# "Subir un huevo" = alcanzar la zona de graduacion (~$69k). Es el techo observable en la curva:
# tras graduar el token pasa a otro AMM y deja de emitir TradeEvents aqui.
EXPLODE_TARGET_SOL = 380.0
# Rango que puebla la pestana "a punto de explotar".
HOT_ZONE_LOW_SOL = 170.0
HOT_ZONE_HIGH_SOL = 360.0
# Estas bandas son raras: se exige menos muestra que en la tasa general para dar un numero.
MIN_EXPLODE_SAMPLE = 10
# Carpeta del corpus de entrenamiento (persistente via volumen). Cada ganador es una linea JSON.
TRAINING_CORPUS_DIR = os.environ.get("TRAINING_CORPUS_DIR", "/data/training")
WINNERS_FILE = "winners.jsonl"
# Estampidas ya resueltas, con su firma completa y su desenlace. Es el conjunto que responde
# "que distingue en los primeros segundos a la que aguanta de la que se desploma".
STAMPEDES_FILE = "stampedes.jsonl"


class CorpusSeed(NamedTuple):
    """Conteos reconstruidos desde las estampidas ya resueltas guardadas en disco."""

    peak_multiples: list[float]
    band_reached: list[int]
    band_success: list[int]
    band_big: list[int]


def _load_corpus_seed() -> CorpusSeed:
    """Lee `stampedes.jsonl` y reconstruye los conteos historicos.

    Sin esto todas las estadisticas (techo empirico y probabilidades por banda) arrancan vacias
    en cada reinicio y la interfaz muestra "—" durante horas pese a tener cientos de casos ya
    medidos en disco. No se genera ningun dato: solo se vuelven a contar picos reales.

    Un corpus ilegible o a medio escribir no puede tumbar el arranque: se ignora la linea mala.
    """
    multiples: list[float] = []
    reached = [0] * len(CAP_BAND_EDGES_SOL)
    success = [0] * len(CAP_BAND_EDGES_SOL)
    big = [0] * len(CAP_BAND_EDGES_SOL)
    path = Path(TRAINING_CORPUS_DIR) / STAMPEDES_FILE
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return CorpusSeed(multiples, reached, success, big)
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            peak = float(record["peak_market_cap_sol"])
            multiple = float(record["peak_multiple"])
        except (ValueError, KeyError, TypeError):
            continue
        if multiple > 0:
            multiples.append(multiple)
        for index, edge in enumerate(CAP_BAND_EDGES_SOL):
            if peak >= edge:
                reached[index] += 1
                if peak >= EXPLODE_TARGET_SOL:
                    success[index] += 1
                if peak >= BIG_CAP_SOL:
                    big[index] += 1
    LOGGER.info(
        json.dumps(
            {
                "event": "corpus_seeded",
                "resolved_stampedes": len(multiples),
                "band_reached": reached,
                "band_graduated": success,
                "band_big_cap": big,
            }
        )
    )
    return CorpusSeed(multiples[-500:], reached, success, big)


class GrowthOutlook:
    """Techo y probabilidades CONDICIONADOS al crecimiento actual, medidos sobre el corpus.

    La pregunta que responde es: "de los tokens que en su dia llegaron hasta donde esta este,
    hasta donde llegaron despues". Un token que ahora vale xG necesariamente alcanzo xG, asi que
    el subconjunto `peak_growth >= G` del corpus es la referencia correcta para el.

    Tres limitaciones que hay que respetar al leerlo:
      1. `winners.jsonl` solo guarda tokens que alcanzaron WINNER_GROWTH. Por debajo de ese
         crecimiento el corpus no tiene poblacion comparable y se devuelve None, no un numero.
      2. La curva gradua sobre EXPLODE_TARGET_SOL: por encima el token se va a PumpSwap y su
         capitalizacion deja de observarse aqui. Los techos medidos estan truncados ahi.
      3. Es una distribucion de lo que hicieron OTROS. No es una prediccion sobre este token.
    """

    def __init__(self, peaks: list[float]) -> None:
        self._peaks = sorted(p for p in peaks if p > 0)

    def outlook(self, growth: float) -> dict[str, Any] | None:
        if growth <= 0 or not self._peaks:
            return None
        start = bisect.bisect_left(self._peaks, growth)
        sample = self._peaks[start:]
        if len(sample) < MIN_CEILING_SAMPLE:
            return None
        median = sample[len(sample) // 2]
        high = sample[min(len(sample) - 1, int(len(sample) * 0.75))]
        graduated = sum(1 for p in sample if p * BIRTH_CAP_SOL >= EXPLODE_TARGET_SOL)
        big = sum(1 for p in sample if p * BIRTH_CAP_SOL >= BIG_CAP_SOL)
        return {
            "ceiling_sol": round(median * BIRTH_CAP_SOL, 2),
            "ceiling_high_sol": round(high * BIRTH_CAP_SOL, 2),
            "ceiling_sample": len(sample),
            "prob_grad": round(graduated / len(sample), 4),
            "prob_100k": round(big / len(sample), 4),
        }


def _load_growth_peaks() -> list[float]:
    """Picos de crecimiento de `winners.jsonl`. Corpus ausente o corrupto => lista vacia."""
    peaks: list[float] = []
    path = Path(TRAINING_CORPUS_DIR) / WINNERS_FILE
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return peaks
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            peaks.append(float(json.loads(line)["peak_growth"]))
        except (ValueError, KeyError, TypeError):
            continue
    LOGGER.info(json.dumps({"event": "growth_corpus_loaded", "records": len(peaks)}))
    return peaks


class OutcomeTracker:
    """Tasa base REAL, medida en vivo: de los tokens que llegaron a un crecimiento xN, que
    fraccion alcanzo luego la zona de ~$50k.

    No es una prediccion ni un modelo: es un conteo honesto de lo que ha pasado esta sesion.
    Empieza sin datos y se afina segun se acumulan tokens. Se resetea al reiniciar el worker.
    """

    def __init__(self, seed: CorpusSeed | None = None) -> None:
        self._peak: dict[str, float] = {}
        self._reached_target: set[str] = set()
        self._bucket_reached = [0] * len(GROWTH_BUCKETS)
        self._bucket_success = [0] * len(GROWTH_BUCKETS)
        # Bandas por capitalizacion: cuantos entraron en cada banda y cuantos luego explotaron.
        # Arrancan con los casos ya resueltos en disco, no en cero: son observaciones reales.
        self._band_reached = list(seed.band_reached) if seed else [0] * len(CAP_BAND_EDGES_SOL)
        self._band_success = list(seed.band_success) if seed else [0] * len(CAP_BAND_EDGES_SOL)
        self._exploded: set[str] = set()
        # Zona grande (~$100k). Se cuenta aparte porque esta POR ENCIMA de la graduacion: en la
        # curva es practicamente inalcanzable y el conteo debe poder decir "0 de N" sin mentir.
        self._band_big = list(seed.band_big) if seed else [0] * len(CAP_BAND_EDGES_SOL)
        self._reached_big: set[str] = set()

    def observe(self, mint: str, growth: float) -> None:
        """Registra el crecimiento de un token. Solo cuentan los avances de su pico."""
        previous = self._peak.get(mint)
        if previous is None and len(self._peak) >= OUTCOME_MEMORY_CAP:
            return  # memoria llena: no se admiten mints nuevos, los ya seguidos siguen contando
        if previous is not None and growth <= previous:
            return
        prior = previous or 0.0
        for index, level in enumerate(GROWTH_BUCKETS):
            if prior < level <= growth:
                self._bucket_reached[index] += 1
        # Bandas por capitalizacion absoluta: se cuenta la entrada en cada banda una sola vez.
        prior_cap = prior * BIRTH_CAP_SOL
        cap = growth * BIRTH_CAP_SOL
        for index, edge in enumerate(CAP_BAND_EDGES_SOL):
            if prior_cap < edge <= cap:
                self._band_reached[index] += 1

        self._peak[mint] = growth
        if growth >= TARGET_GROWTH and mint not in self._reached_target:
            self._reached_target.add(mint)
            for index, level in enumerate(GROWTH_BUCKETS):
                if level <= growth:
                    self._bucket_success[index] += 1
        if cap >= EXPLODE_TARGET_SOL and mint not in self._exploded:
            self._exploded.add(mint)
            for index, edge in enumerate(CAP_BAND_EDGES_SOL):
                if edge <= cap:
                    self._band_success[index] += 1
        if cap >= BIG_CAP_SOL and mint not in self._reached_big:
            self._reached_big.add(mint)
            for index, edge in enumerate(CAP_BAND_EDGES_SOL):
                if edge <= cap:
                    self._band_big[index] += 1

    def probability(self, growth: float) -> tuple[float | None, int]:
        """(probabilidad, muestra) para el nivel de crecimiento actual. None si falta muestra."""
        index = -1
        for i, level in enumerate(GROWTH_BUCKETS):
            if growth >= level:
                index = i
        if index < 0:
            return (None, 0)
        reached = self._bucket_reached[index]
        if reached < MIN_OUTCOME_SAMPLE:
            return (None, reached)
        return (self._bucket_success[index] / reached, reached)

    def top(self, count: int) -> list[tuple[str, float]]:
        """Los `count` mints con mayor pico de crecimiento visto. (mint, crecimiento)."""
        return sorted(self._peak.items(), key=lambda item: item[1], reverse=True)[:count]

    def explode_probability(self, cap_sol: float) -> tuple[float | None, int]:
        """(probabilidad, muestra) de que un token EN esta banda de cap llegue a explotar.

        Mide lo que ya paso: de los tokens que pasaron por esta banda, que fraccion siguio
        hasta la zona de graduacion. No es una prediccion sobre este token concreto.
        """
        index = -1
        for i, edge in enumerate(CAP_BAND_EDGES_SOL):
            if cap_sol >= edge:
                index = i
        if index < 0:
            return (None, 0)
        reached = self._band_reached[index]
        if reached < MIN_EXPLODE_SAMPLE:
            return (None, reached)
        return (self._band_success[index] / reached, reached)

    def big_probability(self, cap_sol: float) -> tuple[float | None, int]:
        """(probabilidad, muestra) de que un token EN esta banda llegue a la zona grande (~$100k).

        AVISO SOBRE ESTE NUMERO: la curva de pump.fun gradua alrededor de EXPLODE_TARGET_SOL. Al
        graduar, el token pasa a PumpSwap y deja de emitir TradeEvents de curva, asi que su
        capitalizacion posterior NO se observa aqui. Por eso este conteo tiende a 0 y hay que
        leerlo como "de los que seguimos EN LA CURVA, ninguno llego", no como "es imposible".
        La medicion real por encima de la graduacion exige seguir al token en PumpSwap.
        """
        index = -1
        for i, edge in enumerate(CAP_BAND_EDGES_SOL):
            if cap_sol >= edge:
                index = i
        if index < 0:
            return (None, 0)
        reached = self._band_reached[index]
        if reached < MIN_EXPLODE_SAMPLE:
            return (None, reached)
        return (self._band_big[index] / reached, reached)


def _market_cap_sol(
    virtual_sol_reserves: int, virtual_token_reserves: int, total_supply: int
) -> float:
    """Capitalizacion en SOL desde las reservas de la curva. 0.0 si las reservas son invalidas."""
    try:
        curve = CurveState(
            virtual_sol_reserves=max(1, virtual_sol_reserves),
            virtual_token_reserves=max(1, virtual_token_reserves),
            real_token_reserves=0,
            token_total_supply=total_supply,
        )
    except CurveError:
        return 0.0
    return round(market_cap_lamports(curve) / LAMPORTS_PER_SOL, 9)


def _price_sol(virtual_sol_reserves: int, virtual_token_reserves: int) -> float:
    try:
        curve = CurveState(
            virtual_sol_reserves=max(1, virtual_sol_reserves),
            virtual_token_reserves=max(1, virtual_token_reserves),
            real_token_reserves=0,
            token_total_supply=PUMPFUN_TOTAL_SUPPLY,
        )
    except CurveError:
        return 0.0
    return float(spot_price_lamports(curve)) / LAMPORTS_PER_SOL


@dataclass(frozen=True, slots=True)
class IngestConfig:
    database_url: str
    redis_url: str
    wss_url: str
    provider: str
    # Motores que compiten: (url, commitment). El primero que trae cada evento, gana.
    engines: tuple[tuple[str, str], ...]

    @classmethod
    def from_env(cls) -> IngestConfig:
        # HELIUS_WSS_URL solo se usa si hay clave: sin ella la URL trae un `api-key=`
        # vacio y el proveedor rechaza la conexion. Mejor caer al endpoint publico, que
        # funciona, que fallar por una credencial a medio poner.
        helius_key = os.environ.get("HELIUS_API_KEY", "").strip()
        helius_wss = os.environ.get("HELIUS_WSS_URL", "").strip()
        if helius_key and helius_wss:
            wss_url, provider = helius_wss, "helius"
        else:
            wss_url = os.environ.get("SOLANA_FALLBACK_WSS_URL", DEFAULT_WSS)
            provider = "solana-public-rpc"

        # Motores en carrera. Medido sobre 4.409 eventos: el oficial con `processed` gana el
        # 75,2%, publicnode el 23,2% y `confirmed` el 1,6%. Los tres suman: cada evento llega
        # por el que ese instante sea mas rapido. Cuando publicnode pierde, llega solo 26 ms
        # despues, asi que su aportacion es real aunque modesta.
        engines: list[tuple[str, str]] = [
            (wss_url, "processed"),
            (PUBLICNODE_WSS, "processed"),
            (wss_url, "confirmed"),
        ]
        if helius_key and helius_wss and helius_wss != wss_url:
            engines.insert(0, (helius_wss, "processed"))

        return cls(
            database_url=os.environ["DATABASE_URL"],
            redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
            wss_url=wss_url,
            provider=provider,
            engines=tuple(engines),
        )


def token_payload(token: DetectedToken) -> dict[str, Any]:
    """Representacion del token para el dashboard. Solo lectura, sin nada accionable."""
    event: CreateEvent = token.event
    return {
        "mint": token.mint,
        "name": event.name,
        "symbol": event.symbol,
        "uri": event.uri,
        "creator": event.creator,
        "user": event.user,
        "bonding_curve": event.bonding_curve,
        "slot": token.slot,
        "signature": token.signature,
        "provider": token.provider,
        "received_timestamp": token.received_timestamp.isoformat(),
        "pipeline_latency_ms": round(token.pipeline_latency_ms, 3),
        "onchain_lag_seconds": token.onchain_lag_seconds,
        # Capitalizacion en el instante EXACTO de creacion, desde las reservas del propio
        # CreateEvent. Sin una sola llamada extra al RPC.
        "market_cap_sol": _market_cap_sol(
            event.virtual_sol_reserves, event.virtual_token_reserves, event.token_total_supply
        ),
    }


class IngestService:
    """Bucle de ingesta. Se detiene limpiamente al recibir SIGINT/SIGTERM."""

    def __init__(self, config: IngestConfig) -> None:
        self._config = config
        self._engine = create_async_engine(config.database_url, pool_size=5, max_overflow=5)
        self._repository = TokenRepository(self._engine)
        self._redis = redis.from_url(config.redis_url)  # type: ignore[no-untyped-call]  # redis no anota from_url
        self._detector = NewTokenDetector(provider=config.provider)
        # Ritmo del analisis MUY por debajo del limite del endpoint publico: la ingesta
        # tiene prioridad, y perder el WebSocket por agotar cuota analizando seria un
        # mal negocio.
        self._rpc = SolanaRpc(limits=RpcLimits(requests_per_second=1.0))
        self._analysis = AnalysisPipeline(self._rpc, self._redis)
        self.metrics = IngestMetrics()
        self._stop = asyncio.Event()
        # Mints seguidos para cap en vivo (LRU acotado) y ultimo instante publicado por mint.
        self._tracked_mints: collections.OrderedDict[str, None] = collections.OrderedDict()
        self._cap_throttle: dict[str, float] = {}
        # Un unico paso por el corpus alimenta tanto el techo empirico como las probabilidades.
        self._seed = _load_corpus_seed()
        self._outcomes = OutcomeTracker(self._seed)
        self._outlook = GrowthOutlook(_load_growth_peaks())
        # Nombre/simbolo/creador por mint, para poder mostrar los top movers con su nombre.
        self._token_meta: collections.OrderedDict[str, dict[str, str]] = collections.OrderedDict()
        self._top_pub = 0.0
        self._recorded_winners: set[str] = set()
        # Cap ACTUAL por mint (la del ultimo trade), para poblar la banda media en vivo.
        self._current_cap: collections.OrderedDict[str, float] = collections.OrderedDict()
        # Instante de nacimiento y operaciones dentro de la ventana de lanzamiento, para
        # detectar estampidas. Solo cuentan los tokens que hemos visto NACER: sin el instante
        # de creacion no se puede afirmar que la rafaga fuera en el lanzamiento.
        self._birth: collections.OrderedDict[str, float] = collections.OrderedDict()
        self._launch_trades: collections.Counter[str] = collections.Counter()
        self._stampede: dict[str, int] = {}
        # Maximo alcanzado y ultimas lecturas de capitalizacion, para medir si se desinfla.
        self._peak_cap: dict[str, float] = {}
        self._cap_history: dict[str, collections.deque[float]] = {}
        # Seguimiento profundo: SOLO estampidas, y de esas se registra todo.
        self._deep: collections.OrderedDict[str, DeepTrack] = collections.OrderedDict()
        # Capitalizacion en el instante de detectar la estampida, y multiplos de techo ya
        # observados: de ahi sale el techo empirico, que no es una prediccion sino la
        # distribucion de lo que alcanzaron los casos anteriores.
        self._cap_at_detection: dict[str, float] = {}
        # Progreso hacia la graduacion por mint, y momento en que se cruzo cada hito. De ahi
        # sale la velocidad: no es lo mismo llegar al 80% en un minuto que en media hora.
        self._grad_progress: dict[str, float] = {}
        self._grad_first_seen: dict[str, float] = {}
        self._graduated: set[str] = set()
        # Se siembra con las estampidas YA resueltas que hay en disco. Sin esto el techo empirico
        # arranca en blanco tras cada reinicio y la columna muestra "—" durante horas, aunque el
        # corpus lleve cientos de casos medidos. No se inventa nada: son picos reales observados.
        self._peak_multiples: list[float] = list(self._seed.peak_multiples)
        self._resolved: set[str] = set()
        # --- Seguimiento POSTERIOR a la graduacion, en PumpSwap ---------------------------
        # Una sola conexion multiplexada: el RPC publico devuelve 429 al abrir una por mint.
        self._swap = MultiLogStream(
            self._connector(self._config.wss_url), commitment="confirmed", silence_timeout=120.0
        )
        self._curve_cap_at_graduation: dict[str, float] = {}
        self._swap_cap: dict[str, float] = {}
        self._swap_peak: dict[str, float] = {}
        self._swap_trades: collections.Counter[str] = collections.Counter()
        self._swap_order: collections.deque[str] = collections.deque()
        # Ganadores POST-graduacion: los que superaron la zona grande ya fuera de la curva.
        self._swap_reached_big: set[str] = set()
        # Series por simbolo normalizado -> [(mint, nacimiento monotonic)]
        self._series: collections.OrderedDict[str, list[tuple[str, float]]] = (
            collections.OrderedDict()
        )

    def request_stop(self) -> None:
        self._stop.set()

    async def close(self) -> None:
        await self._rpc.close()
        await self._redis.aclose()
        await self._engine.dispose()

    def _connector(self, url: str) -> Callable[[], Awaitable[Any]]:
        async def connect() -> Any:
            return await websockets.connect(url, ping_interval=20, max_size=20_000_000)

        return connect

    async def _connect(self) -> Any:
        return await websockets.connect(self._config.wss_url, ping_interval=20, max_size=20_000_000)

    def _build_engines(self) -> RacingLogStream:
        """Monta los motores que van a competir por traer cada evento antes que los demas.

        Medido contra mainnet (7.454 eventos vistos por ambos): `processed` llego antes que
        `confirmed` en el 100% de los casos, mediana 105 ms. Se mantiene `confirmed` como red de
        seguridad porque gano el 0,9% de las carreras: son eventos que `processed` no trajo.
        """
        engines = [
            ResilientLogStream(
                PUMPFUN_PROGRAM_ID, self._connector(url), commitment=commitment
            )
            for url, commitment in self._config.engines
        ]
        LOGGER.info(
            json.dumps(
                {
                    "event": "engines_ready",
                    "count": len(engines),
                    "engines": [f"{c}@{u.split('//')[-1][:32]}" for u, c in self._config.engines],
                }
            )
        )
        return RacingLogStream(engines)

    def _track_mint(self, mint: str) -> None:
        """Registra un mint para cap en vivo, con desalojo LRU del mas antiguo."""
        self._tracked_mints[mint] = None
        self._tracked_mints.move_to_end(mint)
        while len(self._tracked_mints) > TRACKED_MINTS_CAP:
            evicted, _ = self._tracked_mints.popitem(last=False)
            self._cap_throttle.pop(evicted, None)

    async def _publish_cap_updates(self, notification: dict[str, Any]) -> None:
        """Publica la cap actualizada de cada operacion de un mint ya seguido.

        Se alimenta del MISMO stream de logs que la deteccion: cada TradeEvent trae las reservas
        posteriores a la operacion, y de ahi sale la cap sin tocar el RPC. Se limita a mints que
        estan (o estuvieron hace poco) en el radar, y con throttle por mint.
        """
        if not self._tracked_mints:
            return
        value = notification.get("params", {}).get("result", {}).get("value", {})
        if not isinstance(value, dict) or value.get("err") is not None:
            return
        logs = value.get("logs") or []
        # Filtro barato antes de decodificar base64: la inmensa mayoria del trafico no es un trade.
        if not any("Instruction: Buy" in line or "Instruction: Sell" in line for line in logs):
            return

        now = time.monotonic()
        for event in find_trade_events(logs):
            if event.mint not in self._tracked_mints:
                continue
            # El conteo de la rafaga va ANTES del throttle: si contara despues, el propio
            # throttle descartaria la mayoria de operaciones y una estampida real pareceria
            # un lanzamiento tranquilo. Es justo el caso que hay que medir bien.
            self._count_launch_trade(event.mint, now)
            # Seguimiento profundo: cada operacion, sin throttle. Solo para estampidas, que es
            # lo que hace el coste asumible y lo que permite contar manos y concentracion.
            deep = self._deep.get(event.mint)
            if deep is not None:
                deep.add(now, event.is_buy, event.sol_amount, event.user)
            if now - self._cap_throttle.get(event.mint, 0.0) < CAP_THROTTLE_SECONDS:
                continue
            self._cap_throttle[event.mint] = now
            self._tracked_mints.move_to_end(event.mint)
            cap_sol = _market_cap_sol(
                event.virtual_sol_reserves, event.virtual_token_reserves, PUMPFUN_TOTAL_SUPPLY
            )
            growth = cap_sol / BIRTH_CAP_SOL if cap_sol > 0 else 0.0
            # Progreso a graduacion: sale de las reservas virtuales, sin coste de RPC.
            progress = _graduation_progress(event.virtual_sol_reserves)
            if progress >= GRADUATING_MIN_PROGRESS:
                if event.mint not in self._grad_first_seen:
                    self._grad_first_seen[event.mint] = now
                self._grad_progress[event.mint] = progress
                if progress >= 1.0 and event.mint not in self._graduated:
                    meta = self._token_meta.get(event.mint, {})
                    if cap_sol < GRADUATION_MIN_CAP_SOL:
                        # Progreso saturado a 1,0 con una capitalizacion imposible: es una
                        # lectura mala, no una graduacion. No se marca, y se dej registro para
                        # poder medir cuantas veces pasa.
                        LOGGER.warning(
                            json.dumps(
                                {
                                    "event": "graduation_rejected",
                                    "mint": event.mint,
                                    "symbol": meta.get("symbol", ""),
                                    "market_cap_sol": round(cap_sol, 4),
                                    "virtual_sol_reserves": event.virtual_sol_reserves,
                                    "virtual_token_reserves": event.virtual_token_reserves,
                                    "reason": f"cap < {GRADUATION_MIN_CAP_SOL} SOL",
                                }
                            )
                        )
                    else:
                        self._graduated.add(event.mint)
                        LOGGER.info(
                            json.dumps(
                                {
                                    "event": "graduated",
                                    "mint": event.mint,
                                    "symbol": meta.get("symbol", ""),
                                    "market_cap_sol": round(cap_sol, 4),
                                }
                            )
                        )
                        # La curva enmudece aqui. El seguimiento continua en PumpSwap.
                        self._curve_cap_at_graduation[event.mint] = cap_sol
                        await self._watch_on_swap(event.mint)
            self._outcomes.observe(event.mint, growth)
            self._record_winner(event.mint, growth)
            self._current_cap[event.mint] = cap_sol
            self._record_cap(event.mint, cap_sol)
            if (tracked := self._deep.get(event.mint)) is not None:
                tracked.add_cap(now, cap_sol)
            self._current_cap.move_to_end(event.mint)
            while len(self._current_cap) > TRACKED_MINTS_CAP:
                self._current_cap.popitem(last=False)
            probability, sample = self._outcomes.probability(growth)
            payload = {
                "mint": event.mint,
                "market_cap_sol": cap_sol,
                "price_sol": _price_sol(event.virtual_sol_reserves, event.virtual_token_reserves),
                "is_buy": event.is_buy,
                "prob_50k": probability,
                "prob_sample": sample,
                # Techo empirico y probabilidades condicionados al crecimiento ACTUAL. Van aqui
                # y no solo en la estampida para que tambien los tokens del radar los tengan.
                **(self._outlook.outlook(growth) or {}),
            }
            with contextlib.suppress(Exception):
                await self._redis.publish(CHANNEL_CAP, json.dumps(payload))

        await self._maybe_publish_top()

    def _record_cap(self, mint: str, cap: float) -> None:
        """Guarda la capitalizacion para poder medir despues si el token se desinfla."""
        if cap <= 0:
            return
        self._peak_cap[mint] = max(self._peak_cap.get(mint, 0.0), cap)
        history = self._cap_history.setdefault(mint, collections.deque(maxlen=CAP_HISTORY))
        history.append(cap)
        # Cota de memoria: se limpia lo que ya no se sigue para la cap en vivo.
        if len(self._peak_cap) > TRACKED_MINTS_CAP * 2:
            # Los tokens en estampida se conservan aunque dejen de operar: su historial es la
            # unica fuente de su ultima capitalizacion conocida.
            protegidos = self._stampede.keys() | self._deep.keys()
            stale_mints = [
                m for m in self._peak_cap if m not in self._current_cap and m not in protegidos
            ]
            for stale in stale_mints[:200]:
                self._peak_cap.pop(stale, None)
                self._cap_history.pop(stale, None)

    def _ceiling(self, mint: str) -> tuple[float | None, float | None, int]:
        """(techo mediano, techo optimista p75, muestra) para un token en estampida.

        NO es una prediccion. Es la distribucion de lo que alcanzaron las estampidas anteriores:
        se mide `pico / capitalizacion al detectarla` en los casos ya resueltos y se aplica ese
        multiplo al token actual. Con muestra insuficiente se devuelve None en vez de inventar
        una cifra, porque un techo inventado invita a entrar donde no hay nada.
        """
        base = self._cap_at_detection.get(mint)
        if base is None or len(self._peak_multiples) < MIN_CEILING_SAMPLE:
            return (None, None, len(self._peak_multiples))
        ordered = sorted(self._peak_multiples)
        median = statistics.median(ordered)
        optimistic = ordered[min(len(ordered) - 1, int(len(ordered) * 0.75))]
        return (base * median, base * optimistic, len(ordered))

    def _resolve_outcome(self, mint: str, state: str) -> None:
        """Cierra una estampida ya muerta: alimenta el techo empirico y el corpus.

        Solo se cierra cuando el token esta claramente acabado. Cerrarlo antes contaminaria la
        estadistica con techos que aun no habian llegado.
        """
        if state != "cayendo" or mint in self._resolved:
            return
        base = self._cap_at_detection.get(mint)
        peak = self._peak_cap.get(mint)
        if not base or not peak or base <= 0:
            return
        self._resolved.add(mint)
        self._peak_multiples.append(peak / base)
        if len(self._peak_multiples) > 500:
            self._peak_multiples = self._peak_multiples[-500:]

        meta = self._token_meta.get(mint, {})
        deep = self._deep.get(mint)
        record: dict[str, Any] = {
            "mint": mint,
            "name": meta.get("name", ""),
            "symbol": meta.get("symbol", ""),
            "creator": meta.get("creator", ""),
            "launch_trades": self._launch_trades.get(mint, 0),
            "cap_at_detection_sol": round(base, 4),
            "peak_market_cap_sol": round(peak, 4),
            "peak_multiple": round(peak / base, 3),
            "reached_big_cap": peak >= BIG_CAP_SOL,
            "label": "stampede_dumped",
            "recorded_at": datetime.now(UTC).isoformat(),
        }
        if deep is not None:
            record.update(deep.metrics(time.monotonic()))
            # La forma de la subida es la parte mas prometedora del corpus: es lo que deberia
            # separar "subio de golpe y aguanto" de "subio a trompicones y se hundio".
            record.update(deep.climb())
            # Serie COMPLETA de capitalizacion. Sin ella no se puede simular un trailing stop
            # de verdad: un stop salta en el primer retroceso, no espera al maximo, y con solo
            # el pico y la caida final el resultado sale optimista.
            record["cap_series"] = [
                [round(t, 2), round(c, 4)] for t, c in _downsample(deep.caps, CAP_SERIES_MAX)
            ]
        try:
            directory = Path(TRAINING_CORPUS_DIR)
            directory.mkdir(parents=True, exist_ok=True)
            with (directory / STAMPEDES_FILE).open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")
        except OSError:
            LOGGER.warning("no se pudo grabar la estampida %s en el corpus", mint)
            return
        LOGGER.info(json.dumps({"event": "stampede_resolved", **record}))

    def _last_known_cap(self, mint: str) -> float | None:
        """Ultima capitalizacion conocida del token, o None si nunca se midio.

        Devolver None y omitir el token es preferible a inventar un valor: una cifra falsa se
        lee como un desplome real y llevaria a decisiones equivocadas.
        """
        current = self._current_cap.get(mint)
        if current is not None:
            return current
        history = self._cap_history.get(mint)
        return history[-1] if history else None

    def _health(self, mint: str) -> tuple[str, float]:
        """(estado, caida_desde_maximo) de un token ya detectado.

        Se compara el maximo contra la MEDIANA de las ultimas lecturas, no contra la ultima.
        Asi un pico de ventas aislado —que se recupera al instante— no marca el token como
        muerto, pero una caida sostenida si: es justo la distincion que hace falta.
        """
        peak = self._peak_cap.get(mint, 0.0)
        history = self._cap_history.get(mint)
        if peak <= 0 or not history:
            return ("nueva", 0.0)
        sustained = statistics.median(history)
        drawdown = max(0.0, (peak - sustained) / peak)
        if drawdown >= DEAD_DRAWDOWN:
            return ("cayendo", drawdown)
        if drawdown >= COOLING_DRAWDOWN:
            return ("enfriando", drawdown)
        return ("viva", drawdown)

    def _count_launch_trade(self, mint: str, now: float) -> None:
        """Cuenta una operacion si cae dentro de la ventana de lanzamiento del token.

        Solo se cuentan tokens cuyo nacimiento hemos visto: sin instante de creacion no se puede
        afirmar que la rafaga fuera en el lanzamiento, y afirmarlo sin base seria inventar.
        """
        birth = self._birth.get(mint)
        if birth is None or now - birth > LAUNCH_WINDOW_SECONDS:
            return
        self._launch_trades[mint] += 1
        count = self._launch_trades[mint]
        if count == STAMPEDE_TRADES:
            # Se cruza el umbral: se registra una sola vez, al cruzarlo.
            self._stampede[mint] = count
            # A partir de aqui, TODA la potencia del motor sobre este token: cada operacion se
            # registra, sin throttle. Es asequible porque solo entra el ~4% de lanzamientos.
            self._deep[mint] = DeepTrack(mint=mint, birth=birth)
            # Capitalizacion de partida: el techo empirico se mide como multiplo de ESTE valor.
            self._cap_at_detection[mint] = self._current_cap.get(mint, BIRTH_CAP_SOL)
            self._deep.move_to_end(mint)
            while len(self._deep) > DEEP_TRACKED_MAX:
                self._deep.popitem(last=False)
            meta = self._token_meta.get(mint, {})
            LOGGER.info(
                json.dumps(
                    {
                        "event": "stampede_detected",
                        "mint": mint,
                        "symbol": meta.get("symbol", ""),
                        "trades_in_window": count,
                        "window_seconds": LAUNCH_WINDOW_SECONDS,
                    }
                )
            )
        elif mint in self._stampede:
            self._stampede[mint] = count

    def _remember_series(self, token: DetectedToken) -> None:
        """Apunta el token en la serie de su simbolo.

        La clave es el simbolo normalizado. Se guarda el momento de nacimiento para poder medir
        cada cuanto sale una iteracion nueva —que es el dato que interesa— y el mint para poder
        consultar despues su pico real.
        """
        clave = (token.event.symbol or "").strip().lower()
        if not clave:
            return
        serie = self._series.setdefault(clave, [])
        serie.append((token.mint, time.monotonic()))
        self._series.move_to_end(clave)
        while len(self._series) > SERIES_MEMORY:
            self._series.popitem(last=False)

    def _series_snapshot(self) -> list[dict[str, Any]]:
        """Series VIVAS: un simbolo repetido donde algun miembro anterior ya bombeo.

        El filtro de "potencial" es exactamente ese: que la serie haya demostrado que mueve
        dinero. Un nombre repetido cuyos miembros murieron todos en 3.000 $ no entra, y son la
        inmensa mayoria —ocho $TNOS en ocho dias antes de que empezara el bombeo, todos basura—.
        """
        ahora = time.monotonic()
        salida: list[dict[str, Any]] = []
        for clave, miembros in self._series.items():
            recientes = [(m, t) for m, t in miembros if ahora - t <= SERIES_WINDOW_SECONDS]
            if len(recientes) < SERIES_MIN_MEMBERS:
                continue
            recientes.sort(key=lambda x: x[1])
            picos = [(m, self._peak_cap.get(m, 0.0), t) for m, t in recientes]
            bombearon = [p for _, p, _ in picos if p >= SERIES_PUMP_CAP_SOL]
            if not bombearon:
                continue  # nombre repetido pero sin potencial demostrado: fuera
            # Cadencia: cada cuanto aparece una iteracion nueva.
            tiempos = [t for _, t in recientes]
            huecos = [tiempos[i + 1] - tiempos[i] for i in range(len(tiempos) - 1)]
            # Colisión de nombres populares, no serie orquestada.
            if huecos and statistics.median(huecos) < SERIES_MIN_CADENCE_SECONDS:
                continue
            ultimo_mint, ultimo_pico, ultimo_t = picos[-1]
            meta = self._token_meta.get(ultimo_mint, {})
            salida.append(
                {
                    "symbol": meta.get("symbol") or clave,
                    "key": clave,
                    "members": len(recientes),
                    "pumped": len(bombearon),
                    "best_peak_sol": round(max(bombearon), 2),
                    # Mediana de los huecos: "sale uno cada X". Con un solo hueco es ese hueco.
                    "cadence_seconds": (
                        round(statistics.median(huecos), 1) if huecos else None
                    ),
                    "latest_mint": ultimo_mint,
                    "latest_age_seconds": round(ahora - ultimo_t, 1),
                    "latest_peak_sol": round(ultimo_pico, 2),
                    "latest_cap_sol": (
                        round(c, 4) if (c := self._last_known_cap(ultimo_mint)) else None
                    ),
                    # Picos de todos los miembros, del mas viejo al mas nuevo. Es donde se ve
                    # si la serie se esta agotando (cada iteracion vale menos que la anterior).
                    "peaks_sol": [round(p, 2) for _, p, _ in picos],
                    "decaying": len(bombearon) >= 2 and picos[-1][1] < max(bombearon) / 2,
                }
            )
        salida.sort(key=lambda s: (-float(s["best_peak_sol"]), float(s["latest_age_seconds"])))
        return salida[:SERIES_MAX]

    def _remember_meta(self, token: DetectedToken) -> None:
        self._remember_series(token)
        self._token_meta[token.mint] = {
            "name": token.event.name,
            "symbol": token.event.symbol,
            "creator": token.event.creator,
        }
        self._token_meta.move_to_end(token.mint)
        while len(self._token_meta) > OUTCOME_MEMORY_CAP:
            self._token_meta.popitem(last=False)

    async def _maybe_publish_top(self) -> None:
        """Reescribe la foto de los que mas han explotado, con throttle global."""
        now = time.monotonic()
        if now - self._top_pub < TOP_MOVERS_THROTTLE_SECONDS:
            return
        self._top_pub = now
        movers = []
        for mint, g in self._outcomes.top(TOP_MOVERS_COUNT):
            if g < 1.05:  # nada que celebrar: no ha crecido
                continue
            meta = self._token_meta.get(mint, {})
            movers.append(
                {
                    "mint": mint,
                    "name": meta.get("name", ""),
                    "symbol": meta.get("symbol", ""),
                    "creator": meta.get("creator", ""),
                    "growth": round(g, 2),
                    "peak_market_cap_sol": round(g * BIRTH_CAP_SOL, 4),
                }
            )
        with contextlib.suppress(Exception):
            await self._redis.set(KEY_TOP_MOVERS, json.dumps(movers))

        # Zona media: los que estan AHORA entre ~$30k y ~$60k, con su tasa base de explosion.
        hot = []
        for mint, cap in self._current_cap.items():
            if not (HOT_ZONE_LOW_SOL <= cap <= HOT_ZONE_HIGH_SOL):
                continue
            probability, sample = self._outcomes.explode_probability(cap)
            meta = self._token_meta.get(mint, {})
            hot.append(
                {
                    "mint": mint,
                    "name": meta.get("name", ""),
                    "symbol": meta.get("symbol", ""),
                    "market_cap_sol": round(cap, 4),
                    "growth": round(cap / BIRTH_CAP_SOL, 2),
                    "explode_prob": probability,
                    "explode_sample": sample,
                }
            )
        hot.sort(key=lambda item: float(item["market_cap_sol"]), reverse=True)  # type: ignore[arg-type]
        with contextlib.suppress(Exception):
            await self._redis.set(KEY_HOT_ZONE, json.dumps(hot[:20]))

        # Camino a la graduacion: los que llevan mas de un tercio del recorrido hecho.
        ahora_mono = time.monotonic()
        graduando: list[dict[str, Any]] = []
        for mint, progress in sorted(self._grad_progress.items(), key=lambda i: -i[1]):
            meta = self._token_meta.get(mint, {})
            cap_grad = self._last_known_cap(mint)
            desde = ahora_mono - self._grad_first_seen.get(mint, ahora_mono)
            # Velocidad: puntos de progreso por minuto desde que entro en vigilancia. Lo que
            # separa "va a graduar en un minuto" de "lleva media hora atascado al 40%".
            avance = max(0.0, progress - GRADUATING_MIN_PROGRESS)
            por_min = (avance / (desde / 60)) if desde > 20 else 0.0
            restante = max(0.0, 1.0 - progress)
            graduando.append(
                {
                    "mint": mint,
                    "name": meta.get("name", ""),
                    "symbol": meta.get("symbol", ""),
                    "creator": meta.get("creator", ""),
                    "progress": round(progress, 4),
                    "progress_per_min": round(por_min, 4),
                    # Minutos que tardaria en graduar si mantuviera este ritmo. None si esta
                    # parado: extrapolar desde cero daria un infinito disfrazado de cifra.
                    "eta_minutes": round(restante / por_min, 1) if por_min > 0.001 else None,
                    "sol_to_graduate": round(restante * GRADUATION_RAISE_LAMPORTS / 1e9, 3),
                    "market_cap_sol": round(cap_grad, 4) if cap_grad is not None else None,
                    "graduated": mint in self._graduated,
                    "watched_seconds": round(desde, 1),
                    # Vida DESPUES de graduar, leida en PumpSwap. La curva enmudece al graduar,
                    # asi que sin esto un token que sigue vivo parece congelado para siempre.
                    # `swap_ratio` es la cifra a vigilar: si en todos los tokens se agrupa en el
                    # mismo valor, el salto es de referencia entre sitios y no de mercado.
                    "swap_market_cap_sol": (
                        round(self._swap_cap[mint], 4) if mint in self._swap_cap else None
                    ),
                    "swap_peak_sol": (
                        round(self._swap_peak[mint], 4) if mint in self._swap_peak else None
                    ),
                    "swap_trades": self._swap_trades.get(mint, 0),
                    "swap_watched": mint in self._swap.watched,
                    "swap_ratio": (
                        round(self._swap_cap[mint] / self._curve_cap_at_graduation[mint], 4)
                        if mint in self._swap_cap
                        and self._curve_cap_at_graduation.get(mint, 0) > 0
                        else None
                    ),
                    **(
                        {"launch_trades": self._launch_trades[mint]}
                        if mint in self._stampede
                        else {}
                    ),
                }
            )
            if len(graduando) >= GRADUATING_MAX:
                break
        with contextlib.suppress(Exception):
            await self._redis.set(KEY_GRADUATING, json.dumps(graduando))

        # Estampidas: lanzamientos con una rafaga de operaciones como la de V713.
        # Se ordena por tamano de rafaga ANTES de construir los diccionarios: asi la clave de
        # ordenacion sigue siendo un int y no hay que reconvertir nada.
        ranked = sorted(self._stampede.items(), key=lambda item: item[1], reverse=True)[:30]
        stampede: list[dict[str, Any]] = []
        for mint, burst in ranked:
            meta = self._token_meta.get(mint, {})
            # NUNCA se cae a la cap de nacimiento: `_current_cap` es un LRU acotado y un token
            # que deja de operar acaba desalojado. Usar el valor por defecto haria que un token
            # inactivo pareciera desplomado a 27,96 —una perdida catastrofica inventada—.
            # Se recurre a la ultima lectura conocida, y si no hay ninguna se omite el token.
            last_cap = self._last_known_cap(mint)
            if last_cap is None:
                continue
            cap = last_cap
            state, drawdown = self._health(mint)
            # Si ya esta acabado, se cierra: alimenta el techo empirico y el corpus.
            self._resolve_outcome(mint, state)
            ceiling, ceiling_high, ceiling_sample = self._ceiling(mint)
            peak_cap = self._peak_cap.get(mint, cap)
            stampede.append(
                {
                    "mint": mint,
                    "name": meta.get("name", ""),
                    "symbol": meta.get("symbol", ""),
                    "creator": meta.get("creator", ""),
                    "launch_trades": burst,
                    "window_seconds": LAUNCH_WINDOW_SECONDS,
                    "market_cap_sol": round(cap, 4),
                    "growth": round(cap / BIRTH_CAP_SOL, 2),
                    # Salud posterior: una estampida que ya se desinfla no es una oportunidad.
                    "state": state,
                    "drawdown_pct": round(drawdown * 100, 1),
                    "peak_market_cap_sol": round(peak_cap, 4),
                    # Techo anclado en la capitalizacion AL DETECTAR la estampida: mediana y p75
                    # de los multiplos de pico que hicieron las estampidas ya resueltas.
                    "ceiling_detect_sol": round(ceiling, 2) if ceiling is not None else None,
                    "ceiling_detect_high_sol": (
                        round(ceiling_high, 2) if ceiling_high is not None else None
                    ),
                    "ceiling_detect_sample": ceiling_sample,
                    # Techo PRINCIPAL, condicionado a donde esta el token AHORA. Se prefiere al
                    # anterior porque no depende del momento en que lo pillamos y tiene mas
                    # muestra. Trae ademas prob_grad y prob_100k para esta misma poblacion.
                    **(self._outlook.outlook(cap / BIRTH_CAP_SOL) or {}),
                    # Tramo por encima de la graduacion, medido en PumpSwap.
                    **self._post_graduation_stats(),
                    # ¿Ha llegado alguna vez a la zona grande (~$100k)?
                    "reached_big_cap": peak_cap >= BIG_CAP_SOL,
                    # Metricas del seguimiento profundo (manos, concentracion, ritmo) y la
                    # FORMA de la subida (de golpe o a trompicones).
                    **(deep.metrics(time.monotonic()) if (deep := self._deep.get(mint)) else {}),
                    **(deep.climb() if deep else {}),
                }
            )
        # Las vivas primero y las que caen al fondo: la lista tiene que reflejar el AHORA, no
        # solo lo que paso en el lanzamiento.
        order = {"viva": 0, "nueva": 1, "enfriando": 2, "cayendo": 3}

        def _rank(item: dict[str, Any]) -> tuple[int, int, int]:
            # Primero los que han tocado la zona grande, luego por salud, y a igualdad por
            # tamano de rafaga. Los que no llegaron quedan abajo, como en las otras pestañas.
            return (
                0 if item.get("reached_big_cap") else 1,
                order.get(str(item["state"]), 9),
                -int(item["launch_trades"]),
            )

        stampede.sort(key=_rank)
        with contextlib.suppress(Exception):
            await self._redis.set(KEY_STAMPEDE, json.dumps(stampede))
        with contextlib.suppress(Exception):
            await self._redis.set(KEY_SERIES, json.dumps(self._series_snapshot()))

    def _record_winner(self, mint: str, growth: float) -> None:
        """Graba un token que ha explotado como ejemplo etiquetado del corpus de entrenamiento.

        Es la referencia con la que el detector de la Fase 5 aprendera: cada linea es un caso
        real 'esto llego a xN'. Append-only y persistente: nunca se reentrena en caliente aqui,
        pero el dato queda para siempre.
        """
        if growth < WINNER_GROWTH or mint in self._recorded_winners:
            return
        self._recorded_winners.add(mint)
        meta = self._token_meta.get(mint, {})
        record = {
            "mint": mint,
            "name": meta.get("name", ""),
            "symbol": meta.get("symbol", ""),
            "creator": meta.get("creator", ""),
            "peak_growth": round(growth, 2),
            "peak_market_cap_sol": round(growth * BIRTH_CAP_SOL, 4),
            "label": "win",
            "recorded_at": datetime.now(UTC).isoformat(),
        }
        try:
            directory = Path(TRAINING_CORPUS_DIR)
            directory.mkdir(parents=True, exist_ok=True)
            with (directory / WINNERS_FILE).open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")
        except OSError:
            # No poder escribir el corpus no puede tumbar la ingesta: se cuenta como fallo suave.
            LOGGER.warning("no se pudo grabar ganador %s en el corpus", mint)
            return
        LOGGER.info(json.dumps({"event": "winner_recorded", **record}))

    async def _handle(self, notification: dict[str, Any]) -> None:
        self.metrics.events_received += 1
        token = self._detector.observe(notification)
        if token is None:
            self.metrics.events_discarded += 1
            # No es una creacion: puede ser una compra/venta de un token que ya seguimos.
            await self._publish_cap_updates(notification)
            return

        self.metrics.tokens_detected += 1
        self.metrics.pipeline_latency.record(token.pipeline_latency_ms)
        self._track_mint(token.mint)
        self._remember_meta(token)
        # Se marca el nacimiento AQUI, al verlo crear: es lo que permite afirmar que una
        # rafaga posterior ocurrio en el lanzamiento y no a mitad de vida.
        self._birth[token.mint] = time.monotonic()
        self._birth.move_to_end(token.mint)
        while len(self._birth) > TRACKED_MINTS_CAP:
            evicted, _ = self._birth.popitem(last=False)
            self._launch_trades.pop(evicted, None)

        try:
            result = await self._repository.save_detection(token)
            self.metrics.persistence_latency.record(result.latency_ms)
        except Exception:
            # Un fallo de escritura no puede tumbar la ingesta: se cuenta, se registra y se
            # sigue. Perder un token es malo; dejar de escuchar es peor.
            self.metrics.persistence_errors += 1
            LOGGER.exception("fallo al persistir %s", token.mint)
            return

        if not result.inserted:
            self.metrics.duplicates += 1
            return

        payload = token_payload(token)
        # Token recien nacido: crecimiento x1. Se registra y se adjunta la tasa base a ese nivel.
        self._outcomes.observe(token.mint, 1.0)
        probability, sample = self._outcomes.probability(1.0)
        payload["prob_50k"] = probability
        payload["prob_sample"] = sample
        LOGGER.info(json.dumps({"event": "token_detected", **payload}))
        # Se encola para analisis. Si la cola esta llena se descarta y se cuenta: el token
        # queda registrado igual, solo sin veredicto.
        self._analysis.submit(token)
        with contextlib.suppress(Exception):
            await self._redis.publish(CHANNEL_NEW_TOKENS, json.dumps(payload))

    async def run(self) -> None:
        stream = self._build_engines()
        LOGGER.info(
            json.dumps(
                {
                    "event": "ingest_started",
                    "provider": self._config.provider,
                    "program": PUMPFUN_PROGRAM_ID,
                    "engines": len(self._config.engines),
                }
            )
        )
        consumer = asyncio.create_task(self._consume(stream))
        analyst = asyncio.create_task(self._analysis.run())
        swapper = asyncio.create_task(self._consume_swap())
        stopper = asyncio.create_task(self._stop.wait())
        done, pending = await asyncio.wait(
            {consumer, stopper, analyst, swapper}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        for task in done:
            if task is consumer and not task.cancelled():
                task.result()

    def _post_graduation_stats(self) -> dict[str, Any]:
        """P(llegar a 100k | ya gradúo), medida en PumpSwap y no en la curva.

        Es la mitad que faltaba de la cadena. La curva puede medir P(graduar); por encima de la
        graduacion no ve nada, y por eso la probabilidad de 100k salia 0 estructural. Este
        conteo cubre el tramo de arriba con observaciones propias, y devuelve None mientras no
        haya muestra en vez de dar un porcentaje sacado de cuatro casos.
        """
        seguidos = len(self._swap_peak)
        llegaron = len(self._swap_reached_big)
        return {
            "grad_tracked": seguidos,
            "grad_reached_big": llegaron,
            "prob_100k_given_grad": (
                round(llegaron / seguidos, 4) if seguidos >= MIN_EXPLODE_SAMPLE else None
            ),
        }

    async def _watch_on_swap(self, mint: str) -> None:
        """Da de alta un token recien graduado en el seguimiento de PumpSwap.

        El conjunto esta acotado: cada suscripcion consume recursos del proveedor y los tokens
        graduados se acumulan sin parar. Se desaloja el mas antiguo, que es el que menos
        probabilidad tiene de seguir operando.
        """
        if mint in self._swap.watched:
            return
        self._swap_order.append(mint)
        while len(self._swap_order) > SWAP_WATCH_CAP:
            evicted = self._swap_order.popleft()
            await self._swap.unwatch(evicted)
            self._swap_trades.pop(evicted, None)
        with contextlib.suppress(Exception):
            await self._swap.watch(mint)

    async def _consume_swap(self) -> None:
        """Capitalizacion de los tokens YA graduados, leida de PumpSwap.

        Estas cifras se guardan en su PROPIO espacio (`_swap_*`) y no se mezclan con las de la
        curva. El motivo es concreto: al graduar, la capitalizacion medida cae de golpe (se
        observaron ratios de 0,31 y 0,34 contra la ultima lectura de curva). Mientras no este
        establecido si ese salto es mercado real o un cambio de referencia entre los dos sitios,
        volcarlo en `_peak_cap` marcaria como "desplomados" a tokens que acaban de triunfar y
        contaminaria el techo empirico con caidas que nunca ocurrieron.
        """
        async for notification in self._swap:
            mint = notification.key
            for trade in find_pumpswap_trades(notification.logs, mint):
                cap = _market_cap_sol(
                    trade.virtual_sol_reserves,
                    trade.virtual_token_reserves,
                    PUMPFUN_TOTAL_SUPPLY,
                )
                if cap <= 0:
                    continue
                first = mint not in self._swap_cap
                self._swap_cap[mint] = cap
                self._swap_peak[mint] = max(self._swap_peak.get(mint, 0.0), cap)
                self._swap_trades[mint] += 1
                if first:
                    # El traspaso curva -> AMM, con las dos lecturas juntas. Es el registro que
                    # permitira decidir si el salto es real o un problema de referencia: si el
                    # ratio se agrupa siempre en el mismo valor, es lo segundo.
                    curve = self._curve_cap_at_graduation.get(mint)
                    LOGGER.info(
                        json.dumps(
                            {
                                "event": "graduation_handoff",
                                "mint": mint,
                                "symbol": self._token_meta.get(mint, {}).get("symbol", ""),
                                "curve_cap_sol": round(curve, 4) if curve else None,
                                "swap_cap_sol": round(cap, 4),
                                "ratio": round(cap / curve, 4) if curve else None,
                            }
                        )
                    )
                if cap >= BIG_CAP_SOL and mint not in self._swap_reached_big:
                    self._swap_reached_big.add(mint)
                    LOGGER.info(
                        json.dumps(
                            {
                                "event": "reached_big_cap_after_graduation",
                                "mint": mint,
                                "symbol": self._token_meta.get(mint, {}).get("symbol", ""),
                                "swap_cap_sol": round(cap, 4),
                            }
                        )
                    )
                with contextlib.suppress(Exception):
                    await self._redis.publish(
                        CHANNEL_CAP,
                        json.dumps(
                            {
                                "mint": mint,
                                "market_cap_sol": cap,
                                "price_sol": (
                                    trade.virtual_sol_reserves / trade.virtual_token_reserves
                                    if trade.virtual_token_reserves > 0
                                    else 0.0
                                ),
                                "is_buy": trade.is_buy,
                                # Marca de sitio: quien lo consuma tiene que saber que esta
                                # cifra NO es comparable con la de la curva.
                                "venue": "pumpswap",
                            }
                        ),
                    )

    async def _consume(self, stream: RacingLogStream) -> None:
        async for notification in stream:
            # Con varios motores, reconexiones y silencios se suman: lo que interesa es la
            # salud del conjunto, no la de uno concreto.
            self.metrics.reconnections = sum(s.stats.reconnects for s in stream.streams)
            self.metrics.connection_failures = sum(s.stats.silence_timeouts for s in stream.streams)
            await self._handle(notification)


async def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(message)s",
    )
    service = IngestService(IngestConfig.from_env())

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, service.request_stop)

    try:
        await service.run()
    finally:
        await service.close()
        LOGGER.info(json.dumps({"event": "ingest_stopped", **service.metrics.snapshot()}))


if __name__ == "__main__":
    asyncio.run(main())
