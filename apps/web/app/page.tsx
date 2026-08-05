'use client';

import { Fragment, useEffect, useRef, useState } from 'react';

import {
  buy as buyOnChain,
  connectWallet,
  explorerUrl,
  reconnectWallet,
  sell as sellOnChain,
  waitForConfirmation,
  warmToken,
} from './trading';

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';
const WS = API.replace(/^http/, 'ws') + '/v1/stream';
const MAX_ROWS = 100;

type Token = {
  mint: string;
  name: string;
  symbol: string;
  creator: string;
  user?: string;
  slot: number;
  signature: string;
  provider: string;
  received_timestamp: string;
  pipeline_latency_ms: number;
  onchain_lag_seconds: number | null;
  // Segundos transcurridos entre la creación del token en cadena y nuestra detección.
  detection_lag_ms?: number | null;
  market_cap_sol?: number | null;
  creator_launches?: number | null;
  prob_50k?: number | null;
  prob_sample?: number | null;
  // Techo empírico y probabilidades, condicionados al crecimiento actual del token.
  ceiling_sol?: number | null;
  ceiling_high_sol?: number | null;
  ceiling_sample?: number | null;
  prob_grad?: number | null;
  prob_100k?: number | null;
};

// Cap con la que nace todo token de pump.fun. Sirve para medir cuánto ha crecido desde cero.
const BIRTH_CAP_SOL = 27.96;
// A partir de este crecimiento un token se considera "caliente" y se resalta en blanco.
const HOT_GROWTH = 1.5;

type CapUpdate = {
  mint: string;
  market_cap_sol: number;
  price_sol: number;
  is_buy: boolean;
  prob_50k?: number | null;
  prob_sample?: number | null;
  ceiling_sol?: number | null;
  ceiling_high_sol?: number | null;
  ceiling_sample?: number | null;
  prob_grad?: number | null;
  prob_100k?: number | null;
};

type TopMover = {
  mint: string;
  name: string;
  symbol: string;
  creator: string;
  growth: number;
  peak_market_cap_sol: number;
};

type StampedeToken = {
  mint: string;
  name: string;
  symbol: string;
  creator: string;
  launch_trades: number;
  window_seconds: number;
  market_cap_sol: number;
  growth: number;
  state: 'viva' | 'nueva' | 'enfriando' | 'cayendo';
  drawdown_pct: number;
  peak_market_cap_sol: number;
  // Métricas del seguimiento profundo (solo tokens en estampida).
  unique_wallets?: number;
  unique_buyers?: number;
  trades_per_wallet?: number;
  top_wallet_share?: number;
  buy_ratio?: number;
  momentum?: number;
  trades_total?: number;
  // Techo principal: condicionado al crecimiento ACTUAL, medido sobre el corpus de ganadores.
  ceiling_sol?: number | null;
  ceiling_high_sol?: number | null;
  ceiling_sample?: number;
  /** Fracción de esa misma población que llegó a graduar (~380 ◎). */
  prob_grad?: number | null;
  /** Fracción que llegó a la zona de 100k. Ver aviso en `BIG_CAP_SOL`: la curva no la mide. */
  prob_100k?: number | null;
  reached_big_cap?: boolean;
  climb_speed?: number;
  monotonic?: number;
  max_dip?: number;
  seconds_to_peak?: number;
  age_seconds?: number;
};

/** Calidad de la SUBIDA, con el criterio que se observó mirando gráficas: subir de golpe es
 * buena señal; hundirse a mitad de camino es mala, aunque luego recupere. Un retroceso breve
 * (un pico de ventas) se tolera; uno profundo no.
 *
 * Es una heurística derivada de MUY pocos casos. Está pendiente de validar contra el corpus. */
function climbVerdict(t: StampedeToken): { label: string; cls: string; detail: string } | null {
  if (t.max_dip === undefined || t.monotonic === undefined) return null;
  const dip = t.max_dip;
  const detail =
    `Retroceso más profundo durante la subida: ${(dip * 100).toFixed(0)}%. ` +
    `Movimientos al alza: ${((t.monotonic ?? 0) * 100).toFixed(0)}%. ` +
    `Tardó ${t.seconds_to_peak ?? '?'}s en tocar techo. ` +
    `Heurística sin validar: pocos casos observados.`;
  if (dip >= 0.35) return { label: 'a trompicones', cls: 'bad', detail };
  if (dip >= 0.18) return { label: 'irregular', cls: 'warn', detail };
  return { label: 'limpia', cls: 'good', detail };
}

// Zona grande: 600 ◎ (~100k $). El umbral se fija en SOL y no en euros porque es lo que la
// curva determina; el euro solo se muestra.
//
// AVISO IMPORTANTE sobre esta cifra. La curva de pump.fun GRADÚA sobre 380 ◎ (~69k $): a partir
// de ahí el token se marcha a PumpSwap y deja de emitir operaciones de curva, así que su
// capitalización posterior no la vemos. En 1.969 registros del corpus el máximo observado son
// 411 ◎ y NINGUNO llegó a 600 ◎. Por eso `prob_100k` sale ~0: no es que sea imposible, es que
// nuestra medición se acaba en la graduación. La referencia alcanzable y medible es GRAD_CAP_SOL.
const BIG_CAP_SOL = 600;
// Espejo de las constantes del worker para las series (ver ingest.py).
const SERIES_PUMP_CAP_SOL = 380;
const SERIES_MIN_MEMBERS = 2;
const SERIES_WINDOW_HOURS = 12;
const SERIES_MIN_CADENCE_MIN = 5;
/** Graduación: techo real de la curva y último punto donde nuestra medida es fiable. */
const GRAD_CAP_SOL = 380;

type SolPrice = { eur: number; usd: number; age_seconds: number; stale: boolean };

/** Veredicto sobre la CALIDAD de la ráfaga: ¿mucha gente o pocas carteras girando volumen?
 * Medido en mainnet: ratios ≥3 tx/cartera aparecían en ráfagas claramente automatizadas. */
function crowdVerdict(t: StampedeToken): { label: string; cls: string } | null {
  const ratio = t.trades_per_wallet;
  if (ratio === undefined || !t.unique_wallets) return null;
  if (ratio >= 3) return { label: 'bots', cls: 'bad' };
  if (ratio >= 1.8) return { label: 'mixto', cls: 'warn' };
  return { label: 'gente', cls: 'good' };
}

/** Posición SIMULADA sobre un token en estampida.
 *
 * No existe fuera de este navegador: no se envía ninguna orden, no se firma nada y el trading
 * real está deshabilitado en el sistema. Es papel — sirve para ver qué habría pasado. */
type PaperPosition = {
  mint: string;
  symbol: string;
  sizeSol: number;
  // Euros invertidos y precio de SOL en el momento de entrar. Se guarda el precio de entrada
  // para que el resultado en euros no cambie retroactivamente si SOL se mueve.
  sizeEur?: number;
  entryEurPerSol?: number;
  entryCap: number;
  entryAt: number;
  trailing: number;
  peakCap: number;
  closedCap?: number;
  closedAt?: number;
  closedReason?: string;
  // Posición con dinero real: se abrió con una compra firmada en la cartera. Cambia lo que
  // pueden hacer los botones —cerrarla exige otra firma— y por eso se distingue del papel.
  real?: boolean;
  // Firma de la compra, para poder ir a verla en el explorador.
  signature?: string;
  // El trailing saltó pero la posición sigue abierta porque vender exige una firma. Se marca
  // en vez de cerrarse sola: dar por vendido algo que no se vendió es la peor mentira posible.
  stopHit?: boolean;
  // El token graduó DESPUÉS de abrir la posición. Al graduar, el precio pasa a medirse en
  // PumpSwap con otra escala, así que comparar con la entrada de la curva no tiene sentido:
  // daría un desplome inventado del 90% y pico. La posición se marca y deja de valorarse.
  graduatedAfter?: boolean;
};

// Costes por lado, iguales a los del backtest para que las cifras sean comparables.
const FEE_PER_SIDE = 0.01;
const SLIPPAGE_PER_SIDE = 0.0125;
const FIXED_COST_SOL = 0.00041;

/** Resultado en SOL de una posición simulada al precio indicado. Cobra ambas patas. */
function paperProfit(position: PaperPosition, currentCap: number): number {
  if (position.entryCap <= 0) return 0;
  const multiple = currentCap / position.entryCap;
  const paid = position.sizeSol * (1 + FEE_PER_SIDE + SLIPPAGE_PER_SIDE);
  const received = position.sizeSol * multiple * (1 - FEE_PER_SIDE - SLIPPAGE_PER_SIDE);
  return received - paid - FIXED_COST_SOL;
}

/** Token que SOLO está subiendo: sigue pegado a su máximo y su última operación fue al alza.
 *
 * "Solo subiendo" se define como no haber retrocedido de forma apreciable desde el máximo. Un
 * token que subió mucho y ya cayó un 30% no está subiendo, aunque siga muy por encima de su
 * nacimiento — y confundir ambas cosas es exactamente lo que hace entrar tarde. */
const RISING_MAX_DRAWDOWN_PCT = 3;

type RisingToken = {
  mint: string;
  symbol: string;
  name: string;
  cap: number;
  growth: number;
  drawdownPct: number;
  /** Cuánto crece por minuto desde que lo detectamos. Es la "velocidad" de la subida. */
  climbPerMin: number;
  /** Cuántas veces tiene que multiplicarse todavía para tocar la zona grande (600 ◎). */
  toBigCapMultiple: number;
  ageSeconds: number;
  /** Solo lo traen los tokens en estampida: ritmo reciente frente al anterior. */
  momentum?: number;
  uniqueWallets?: number;
  launchTrades?: number;
  fromStampede: boolean;
  /** Línea de precio reciente, para dibujar la forma. */
  serie: number[];
  /** Segundos que lleva la subida EN CURSO (se reinicia si cae más de un 10%). */
  risingSeconds: number;
  /** Techo objetivo: lo que alcanzaron casos parecidos. NO es una predicción. */
  targetCap: number | null;
  targetCapHigh: number | null;
  targetSample: number | null;
  /** Fracción de esa población que llegó a graduar, y la que llegó a 100k. */
  probGrad: number | null;
  prob100k: number | null;
  buyRatio?: number;
  uniqueBuyers?: number;
  tradesTotal?: number;
  /** Criterios de "subida constante" que cumple, para poder explicar por qué está o no está. */
  rise: RiseCheck;
};

/** Veredicto de subida constante, con el detalle de qué eje falla. */
type RiseCheck = {
  ok: boolean;
  /** Ejes que NO cumple. Vacío si pasa todo. */
  fails: string[];
  /** Fracción de movimientos al alza en la línea de precio (0-1), o null si no hay historia. */
  upFraction: number | null;
  /** Retroceso puntual más profundo dentro de la serie (0-1). */
  worstDip: number | null;
};

// --- Criterios de "subida CONSTANTE" -------------------------------------------------------
// Los umbrales de compras y concentración salen de medir el corpus de estampidas resueltas
// (225 casos), comparando las que multiplicaron x2 o más contra las que se quedaron bajo x1,3:
//
//        métrica            x>=2      x<1,3
//        buy_ratio          0,545     0,422     <- separa; se exige >= 0,50
//        unique_buyers        235        53     <- separa mucho, pero crece con la edad
//        top_wallet_share   0,047     0,073     <- menos concentración en las buenas
//        momentum           1,000     1,000     <- NO separa: no se usa como criterio
//        recent_rate           44        48     <- NO separa (va al revés); no se usa
//
// Deliberadamente NO se filtra por momentum ni por recent_rate aunque parezcan indicadores
// obvios: en los datos que tenemos no distinguen a las que suben de las que se apagan.
const MIN_RISE_POINTS = 5;
const MIN_UP_FRACTION = 0.75;
const MAX_SINGLE_DIP = 0.05;
const MAX_OFF_PEAK = 0.02;
const MIN_BUY_RATIO = 0.5;
const MAX_TOP_WALLET_SHARE = 0.25;

/** ¿Está subiendo de forma CONSTANTE? Exige evidencia en los tres ejes que pidió el usuario:
 * capitalización (forma de la línea), operaciones y compras.
 *
 * Criterio de diseño: la falta de datos NO cuenta como aprobado. Un token del que solo sabemos
 * que dio un tick al alza no es un token en subida constante; es un token sin historia. Antes
 * bastaba con `capDir === 'up'` —un único tick— y por eso se colaban tokens ya desplomados que
 * rebotaban un instante. */
function checkConstantRise(t: {
  serie: number[];
  drawdownPct: number;
  buyRatio?: number;
  topWalletShare?: number;
}): RiseCheck {
  const fails: string[] = [];
  const serie = t.serie;
  if (serie.length < MIN_RISE_POINTS) {
    return { ok: false, fails: ['sin historia suficiente'], upFraction: null, worstDip: null };
  }
  let ups = 0;
  let worstDip = 0;
  for (let i = 1; i < serie.length; i += 1) {
    const previous = serie[i - 1] ?? 0;
    const current = serie[i] ?? 0;
    if (current >= previous) ups += 1;
    else if (previous > 0) worstDip = Math.max(worstDip, (previous - current) / previous);
  }
  const upFraction = ups / (serie.length - 1);
  const peak = Math.max(...serie);
  const last = serie[serie.length - 1] ?? 0;
  const offPeak = peak > 0 ? (peak - last) / peak : 1;

  if (upFraction < MIN_UP_FRACTION) fails.push('la línea sube a trompicones');
  if (worstDip > MAX_SINGLE_DIP) fails.push(`bajón puntual del ${(worstDip * 100).toFixed(0)}%`);
  if (offPeak > MAX_OFF_PEAK) fails.push('no está en su máximo');
  if (t.drawdownPct > RISING_MAX_DRAWDOWN_PCT) fails.push('ya retrocedió desde el pico');
  // Compras: solo se exige cuando hay dato. Sin seguimiento profundo no se puede afirmar, pero
  // tampoco se descarta al token por algo que no hemos podido medir.
  if (t.buyRatio !== undefined && t.buyRatio < MIN_BUY_RATIO) {
    fails.push(`más ventas que compras (${(t.buyRatio * 100).toFixed(0)}% compras)`);
  }
  if (t.topWalletShare !== undefined && t.topWalletShare > MAX_TOP_WALLET_SHARE) {
    fails.push(`una sola cartera mueve el ${(t.topWalletShare * 100).toFixed(0)}%`);
  }
  return { ok: fails.length === 0, fails, upFraction, worstDip };
}

// Crecimiento por minuto a partir del cual se considera que "sube como la espuma".
// x0,5/min sobre el nacimiento es ya muy por encima de lo normal: la inmensa mayoria de
// tokens no se mueve en absoluto.
const FAST_CLIMB_PER_MIN = 0.5;

// Puntos de precio que se guardan por token para dibujar la línea. 40 a ~400 ms de refresco
// son unos 16 segundos de historia: suficiente para ver la forma sin engordar memoria.
const SPARK_POINTS = 40;

// Segundos a partir de los cuales una subida se considera SOSTENIDA. La mayoría de tokens
// suben entre 5 y 30 segundos y se apagan; aguantar un minuto entero sin caer un 10% es
// bastante menos común y merece salir primero.
const SUSTAINED_RISE_SECONDS = 60;

/** Tendencia legible a partir de la línea de precio.
 *
 * Se compara el último tramo contra el anterior y no el primer punto contra el último: un token
 * que subió y ya se paró debe leerse "plano", no "subiendo", y comparar extremos lo diría mal. */
function trendOf(serie: number[]): { label: string; cls: string; arrow: string } {
  if (serie.length < 4) return { label: 'sin datos', cls: 'pending', arrow: '·' };
  const mitad = Math.floor(serie.length / 2);
  const antes = serie.slice(0, mitad);
  const ahora = serie.slice(mitad);
  const media = (a: number[]) => a.reduce((x, y) => x + y, 0) / a.length;
  const previo = media(antes);
  if (previo <= 0) return { label: 'sin datos', cls: 'pending', arrow: '·' };
  const cambio = (media(ahora) / previo - 1) * 100;
  if (cambio >= 15) return { label: 'disparado', cls: 'hot-name', arrow: '▲▲' };
  if (cambio >= 3) return { label: 'subiendo', cls: 'good', arrow: '▲' };
  if (cambio <= -15) return { label: 'desplomándose', cls: 'bad', arrow: '▼▼' };
  if (cambio <= -3) return { label: 'bajando', cls: 'bad', arrow: '▼' };
  return { label: 'plano', cls: 'muted', arrow: '—' };
}

/** Línea de precio en miniatura. Se normaliza a su propio rango: lo que importa es la FORMA,
 * no la magnitud, que ya está en la columna de capitalización. */
function Spark({ serie, cls }: { serie: number[]; cls: string }) {
  if (serie.length < 2) return <span className="pending">—</span>;
  const min = Math.min(...serie);
  const max = Math.max(...serie);
  const rango = max - min || 1;
  const w = 68;
  const h = 20;
  const paso = w / (serie.length - 1);
  const pts = serie
    .map((v, i) => `${(i * paso).toFixed(1)},${(h - ((v - min) / rango) * (h - 2) - 1).toFixed(1)}`)
    .join(' ');
  const color =
    cls === 'hot-name' ? '#fff' : cls === 'good' ? 'var(--ok)' : cls === 'bad' ? 'var(--down)' : 'var(--muted)';
  return (
    <svg width={w} height={h} className="spark" aria-hidden="true">
      <polyline points={pts} fill="none" stroke={color} strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  );
}

/** Token que estuvo en «solo subidas» y salió. `reason` dice por qué: es la información que
 * convierte la lista en algo auditable en vez de en una caja negra que quita cosas. */
type DroppedToken = {
  mint: string;
  symbol: string;
  name: string;
  peakCap: number;
  exitCap: number;
  maxGrowth: number;
  reason: string;
  at: number;
};

const STATE_LABEL: Record<StampedeToken['state'], string> = {
  viva: '● viva',
  nueva: '· nueva',
  enfriando: '▼ enfriando',
  cayendo: '✕ cayendo',
};

// Umbral de aviso: 120 ◎, unos 7.700 € con SOL cerca de los 64 €. El umbral se fija en SOL
// porque es lo que la curva determina; el euro solo se muestra, con el precio en vivo.
const NOTIFY_CAP_SOL = 120;

/** Token en camino de graduarse. `progress` va de 0 a 1; al llegar a 1 la curva se completa
 * y el token pasa a PumpSwap. Es el suceso más raro y valioso: lo logra ~1-3%. */
type GraduatingToken = {
  mint: string;
  name: string;
  symbol: string;
  creator: string;
  progress: number;
  progress_per_min: number;
  eta_minutes: number | null;
  sol_to_graduate: number;
  market_cap_sol: number | null;
  graduated: boolean;
  watched_seconds: number;
  launch_trades?: number;
  // Vida DESPUÉS de graduar, leída en PumpSwap. La curva enmudece al graduar: sin esto un
  // token que sigue vivo parece congelado para siempre en su última lectura de curva.
  swap_market_cap_sol?: number | null;
  swap_peak_sol?: number | null;
  swap_trades?: number;
  swap_watched?: boolean;
  /** PumpSwap ÷ última lectura de curva. Ver la nota en la tabla: aún no está resuelto. */
  swap_ratio?: number | null;
};

/** Una SERIE: el mismo símbolo relanzado, con al menos un miembro anterior que ya bombeó. */
type SeriesEntry = {
  symbol: string;
  key: string;
  members: number;
  pumped: number;
  best_peak_sol: number;
  /** Cada cuánto sale una iteración nueva (mediana de los huecos). */
  cadence_seconds: number | null;
  latest_mint: string;
  latest_age_seconds: number;
  latest_peak_sol: number;
  latest_cap_sol: number | null;
  /** Picos de cada miembro, del más viejo al más nuevo. Aquí se ve si la serie se agota. */
  peaks_sol: number[];
  /** El último no llegó ni a la mitad del mejor: la serie se está apagando. */
  decaying: boolean;
};

type HotZoneToken = {
  mint: string;
  name: string;
  symbol: string;
  market_cap_sol: number;
  growth: number;
  explode_prob: number | null;
  explode_sample: number;
};

type Analysis = {
  mint: string;
  opportunity: number;
  manipulation_risk: number;
  holders: number;
  top10_pct: number | null;
  signal: string;
  eligible: boolean;
  reasons: string[];
  partial: boolean;
};

type Row = Token & {
  fresh?: boolean;
  analysis?: Analysis;
  capDir?: 'up' | 'down';
};

function short(address: string): string {
  return address.length > 12 ? `${address.slice(0, 4)}…${address.slice(-4)}` : address;
}

/** Segundos entre la creación del token en cadena y el instante en que lo vimos.
 *
 * Es la latencia que de verdad importa: mide cuánto llegamos tarde a la fiesta. La resolución
 * del evento on-chain es de SEGUNDOS, así que no se finge precisión de milisegundos. */
function formatDetectionLag(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '—';
  const seconds = ms / 1000;
  if (seconds < 1) return '<1 s';
  return `${seconds.toLocaleString('es-ES', { maximumFractionDigits: 0 })} s`;
}

function formatCap(cap: number | null | undefined): string {
  if (cap === null || cap === undefined) return '—';
  // Cifra COMPLETA, sin abreviar: en formato español la coma es decimal y el punto es de
  // miles, así "27,96" y "1.234,56" no se confunden con 40 vs 40.000.
  const digits = cap >= 1 ? 2 : 4;
  const figure = cap.toLocaleString('es-ES', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
  return `${figure} ◎`;
}

/** Euros, con el precio de SOL en vivo. Sin precio devuelve null: no se inventa conversión. */
function toEur(sol: number | null | undefined, price: SolPrice | null): string | null {
  if (sol === null || sol === undefined || price === null) return null;
  const eur = sol * price.eur;
  const digits = Math.abs(eur) >= 100 ? 0 : Math.abs(eur) >= 1 ? 2 : 4;
  return `${eur.toLocaleString('es-ES', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })} €`;
}

/** Importe principal en EUROS, con el valor en SOL como referencia secundaria.
 *
 * El euro va delante porque es la unidad en la que se piensa; el SOL se conserva porque es la
 * cifra EXACTA: no depende de ningún proveedor y no envejece si el precio deja de responder. */
function formatMoney(sol: number | null | undefined, price: SolPrice | null): string {
  if (sol === null || sol === undefined) return '—';
  const eur = toEur(sol, price);
  return eur === null ? formatCap(sol) : eur;
}

/** Cuánto ha crecido la cap desde el nacimiento. >1 sube, <1 por debajo de cero. null si no hay cap. */
function growth(cap: number | null | undefined): number | null {
  if (cap === null || cap === undefined || cap <= 0) return null;
  return cap / BIRTH_CAP_SOL;
}

/** Potencial on-chain: creciendo con fuerza desde su nacimiento Y sin ser una fábrica de tokens.
 * NO es una predicción — clasifica lo que ESTÁ subiendo ahora con un creador no-spam. */
function isPotential(row: Row): boolean {
  const g = growth(row.market_cap_sol);
  const launches = row.creator_launches ?? 0;
  // Basta con que esté subiendo y no sea una fábrica extrema: la clasificación por niveles
  // ya se encarga de separar lo notable de lo mediocre.
  return g !== null && g > 1.0 && launches < 20;
}

// --- Barrendero: limpia de la vista lo que ya demostró no funcionar ------------------------
// Margen que se le da a un token para demostrar algo antes de barrerlo. Con ~30 nacimientos
// por minuto, esperar menos barreria tokens que aun no han tenido tiempo de moverse.
const SWEEP_GRACE_SECONDS = 90;
// Lanzamientos del mismo creador a partir de los cuales se considera fabrica de tokens.
const SWEEP_SPAM_LAUNCHES = 10;
// Por debajo de esta fracción de su nacimiento, el token ya se vendió: no va a volver.
const SWEEP_BELOW_BIRTH = 0.95;
// Crecimiento por debajo del cual, pasado el margen, se considera que nunca despegó.
const SWEEP_FLAT_GROWTH = 1.02;

/** Por qué este token es basura, o `null` si merece seguir en pantalla.
 *
 * Devuelve el MOTIVO y no un booleano a propósito: un filtro que esconde cosas sin decir por
 * qué es imposible de auditar, y acabarías dudando de si se te está ocultando algo bueno. */
function garbageReason(
  row: Row,
  stampede: Map<string, StampedeToken>,
  nowMs: number,
): string | null {
  const g = growth(row.market_cap_sol);

  // 1. Ya se desplomó de forma sostenida (lo mide el worker desde el máximo real).
  const burst = stampede.get(row.mint);
  if (burst?.state === 'cayendo') return `se desplomó (−${burst.drawdown_pct.toFixed(0)}% del pico)`;

  // 2. Cotiza por debajo de lo que nació: los compradores ya salieron.
  if (g !== null && g < SWEEP_BELOW_BIRTH) return 'por debajo de su nacimiento';

  // 3. Fábrica de tokens: el mismo creador lanzando en serie.
  const launches = row.creator_launches ?? 0;
  if (launches >= SWEEP_SPAM_LAUNCHES) return `creador en serie (${launches} lanz.)`;

  // 4. Nunca despegó: ha tenido tiempo de sobra y sigue plano.
  const seen = row.received_timestamp ? Date.parse(row.received_timestamp) : NaN;
  const ageSeconds = Number.isNaN(seen) ? 0 : (nowMs - seen) / 1000;
  if (ageSeconds > SWEEP_GRACE_SECONDS && g !== null && g <= SWEEP_FLAT_GROWTH) {
    return `nunca despegó (${Math.round(ageSeconds)}s plano)`;
  }
  return null;
}

type Tier = 'alto' | 'medio' | 'bajo';

/** Puntuación de potencial 0-100, compuesta SOLO de señales medidas:
 *
 * - Crecimiento real ya logrado desde el nacimiento (lo que está pasando, no lo que pasará).
 * - Tasa base empírica a ese nivel (de los que llegaron aquí, cuántos alcanzaron 300 ◎).
 * - Penalización por creador en serie: una fábrica de tokens resta, y mucho.
 *
 * NO es una probabilidad de que suba. Es la intensidad de las señales AHORA, para ordenar.
 */
function potentialScore(row: Row): number {
  const g = growth(row.market_cap_sol);
  if (g === null) return 0;

  // Crecimiento: 0 en x1, satura cerca de x8. Es el componente dominante.
  const growthPoints = Math.max(0, Math.min(55, (g - 1) * 11));

  // Tasa base medida a ese nivel: hasta 30 puntos. Sin muestra suficiente, no suma nada.
  const base = row.prob_50k ?? null;
  const basePoints = base === null ? 0 : Math.min(30, base * 200);

  // Momento: que la última operación empujara hacia arriba.
  const momentumPoints = row.capDir === 'up' ? 15 : row.capDir === 'down' ? 0 : 7;

  // Creador en serie: penalización dura. 20+ lanzamientos es una fábrica de spam.
  const launches = row.creator_launches ?? 1;
  const creatorPenalty = launches >= 20 ? 45 : launches >= 10 ? 25 : launches >= 5 ? 10 : 0;

  const score = growthPoints + basePoints + momentumPoints - creatorPenalty;
  return Math.max(0, Math.min(100, Math.round(score)));
}

function tierOf(score: number): Tier {
  if (score >= 60) return 'alto';
  if (score >= 30) return 'medio';
  return 'bajo';
}

const TIER_LABEL: Record<Tier, string> = {
  alto: '🔥 Potencial ALTO',
  medio: '◆ Potencial MEDIO',
  bajo: '· Potencial BAJO',
};

export default function Page() {
  const [rows, setRows] = useState<Row[]>([]);
  const [connected, setConnected] = useState(false);
  const [detected, setDetected] = useState(0);
  const [view, setView] = useState<
    'radar' | 'potenciales' | 'zona' | 'estampida' | 'subidas' | 'graduando' | 'series'
  >('radar');
  const [topMovers, setTopMovers] = useState<TopMover[]>([]);
  const [hotZone, setHotZone] = useState<HotZoneToken[]>([]);
  const [stampede, setStampede] = useState<StampedeToken[]>([]);
  const [graduating, setGraduating] = useState<GraduatingToken[]>([]);
  const [seriesList, setSeriesList] = useState<SeriesEntry[]>([]);
  // Avisar SOLO de series. Una serie tipo $TNOS es rarísima; las estampidas salen a todas horas
  // y ahogarían el aviso que de verdad se quiere oír.
  const [soloSeries, setSoloSeries] = useState(false);
  const seriesAvisadas = useRef<Set<string>>(new Set());
  const [positions, setPositions] = useState<PaperPosition[]>([]);
  const [price, setPrice] = useState<SolPrice | null>(null);
  // Precio propio de las posiciones abiertas. No depende de que el token siga en ninguna lista.
  const [trackedCaps, setTrackedCaps] = useState<Record<string, number>>({});
  // Barrendero encendido por defecto: la mayoría de lo que nace es basura y verlo todo
  // impide encontrar lo poco que importa.
  const [sweeper, setSweeper] = useState(true);
  const [dropped, setDropped] = useState<DroppedToken[]>([]);
  const [sizeInput, setSizeInput] = useState('0.5');
  const [trailingInput, setTrailingInput] = useState('20');
  // Operativa real: cartera conectada, modo, y el aviso de lo que está pasando ahora mismo.
  const [wallet, setWallet] = useState<string | null>(null);
  const [realMode, setRealMode] = useState(false);
  const [slippageInput, setSlippageInput] = useState('10');
  const [notice, setNotice] = useState<{ text: string; kind: 'ok' | 'bad' | 'wait' } | null>(null);
  // Impide que dos clics manden dos compras de la misma decisión. Es un `ref` y no un estado
  // porque tiene que bloquear en el mismo tick del clic, sin esperar a que React repinte.
  const trading = useRef(false);
  const socket = useRef<WebSocket | null>(null);
  // Mints ya avisados: el aviso se manda UNA vez por token, no en cada refresco.
  const notified = useRef<Set<string>>(new Set());
  // Máximo histórico de cada token visto en esta sesión. Sin esto no se puede distinguir
  // "sube" de "rebota tras desplomarse": un token un 66% por debajo de su pico entraba en
  // «solo subidas» solo porque su último tick fue al alza.
  const peakByMint = useRef<Map<string, number>>(new Map());
  // Historia de precio por token, para dibujar la línea. Y el instante en que empezó la subida
  // actual: se reinicia cuando el token cae por debajo del umbral, así el contador de segundos
  // mide la subida EN CURSO y no el tiempo total desde que lo vimos.
  const histByMint = useRef<Map<string, number[]>>(new Map());
  const riseStart = useRef<Map<string, number>>(new Map());
  // Tokens que ESTUVIERON en «solo subidas» y dejaron de estarlo. Verlos importa tanto como
  // ver los que siguen: es la prueba de cuántos de los que subían acaban no subiendo.
  const risingSeen = useRef<Map<string, RisingToken>>(new Map());
  const onlyPotentials = view === 'potenciales';
  // En «solo subidas» el motor concentra todo el esfuerzo en esa vista: deja de pedir los
  // rankings que no se usan y refresca los precios más a menudo.
  const focusRising = view === 'subidas';

  // Carga inicial: lo ya detectado antes de abrir la pagina.
  useEffect(() => {
    fetch(`${API}/v1/tokens?limit=50`)
      .then((response) => response.json())
      .then((data: { tokens: Array<Record<string, unknown>> }) => {
        setRows(
          data.tokens.map((token) => ({
            mint: String(token.mint),
            name: String(token.name ?? ''),
            symbol: String(token.symbol ?? ''),
            creator: String(token.creator_address ?? ''),
            slot: Number(token.created_at_slot ?? 0),
            signature: '',
            provider: '',
            received_timestamp: String(token.first_seen_at ?? ''),
            pipeline_latency_ms: Number(token.detection_latency_ms ?? 0),
            onchain_lag_seconds: null,
            // En la base, `detection_latency_ms` ES el retraso desde la creación on-chain.
            detection_lag_ms:
              token.detection_latency_ms === null || token.detection_latency_ms === undefined
                ? null
                : Number(token.detection_latency_ms),
            market_cap_sol:
              token.market_cap_sol === null || token.market_cap_sol === undefined
                ? null
                : Number(token.market_cap_sol),
            creator_launches:
              token.creator_launches === null || token.creator_launches === undefined
                ? null
                : Number(token.creator_launches),
          })),
        );
      })
      .catch(() => undefined);
  }, []);

  // Tiempo real. Reconecta sola: el backend puede reiniciarse y la pagina no debe morir.
  useEffect(() => {
    let stopped = false;
    let retry: ReturnType<typeof setTimeout>;

    const connect = () => {
      if (stopped) return;
      const ws = new WebSocket(WS);
      socket.current = ws;

      ws.onopen = () => setConnected(true);
      ws.onclose = () => {
        setConnected(false);
        retry = setTimeout(connect, 2000);
      };
      ws.onerror = () => ws.close();
      ws.onmessage = (message) => {
        const parsed = JSON.parse(message.data as string) as {
          channel: string;
          payload?: Token & Partial<Analysis> & Partial<CapUpdate>;
        };
        if (parsed.channel === 'tokens.analysis' && parsed.payload) {
          const verdict = parsed.payload as unknown as Analysis;
          setRows((current) =>
            current.map((row) => (row.mint === verdict.mint ? { ...row, analysis: verdict } : row)),
          );
          return;
        }
        if (parsed.channel === 'tokens.cap' && parsed.payload) {
          const cap = parsed.payload as unknown as CapUpdate;
          setRows((current) =>
            current.map((row) => {
              if (row.mint !== cap.mint) return row;
              const previous = row.market_cap_sol ?? null;
              let capDir = row.capDir;
              if (previous !== null && cap.market_cap_sol !== previous) {
                capDir = cap.market_cap_sol > previous ? 'up' : 'down';
              }
              const previousPeak = peakByMint.current.get(cap.mint) ?? 0;
              if (cap.market_cap_sol > previousPeak) {
                peakByMint.current.set(cap.mint, cap.market_cap_sol);
              }
              const hist = histByMint.current.get(cap.mint) ?? [];
              hist.push(cap.market_cap_sol);
              if (hist.length > SPARK_POINTS) hist.splice(0, hist.length - SPARK_POINTS);
              histByMint.current.set(cap.mint, hist);
              // La subida "en curso" arranca cuando el token deja de estar hundido y se
              // reinicia si vuelve a caer: es lo que hace que el contador signifique algo.
              const caidaAhora = previousPeak > 0 ? (previousPeak - cap.market_cap_sol) / previousPeak : 0;
              if (caidaAhora > 0.1) riseStart.current.delete(cap.mint);
              else if (!riseStart.current.has(cap.mint)) riseStart.current.set(cap.mint, Date.now());
              const patched = {
                ...row,
                market_cap_sol: cap.market_cap_sol,
                prob_50k: cap.prob_50k ?? null,
                prob_sample: cap.prob_sample ?? null,
                // El techo solo viaja cuando hay muestra comparable. Si en este tick no viene,
                // se conserva el último conocido en vez de borrarlo y hacer parpadear la celda.
                ceiling_sol: cap.ceiling_sol ?? row.ceiling_sol ?? null,
                ceiling_high_sol: cap.ceiling_high_sol ?? row.ceiling_high_sol ?? null,
                ceiling_sample: cap.ceiling_sample ?? row.ceiling_sample ?? null,
                prob_grad: cap.prob_grad ?? row.prob_grad ?? null,
                prob_100k: cap.prob_100k ?? row.prob_100k ?? null,
              };
              return capDir === undefined ? patched : { ...patched, capDir };
            }),
          );
          return;
        }
        if (parsed.channel !== 'tokens.new' || !parsed.payload) return;
        setDetected((n) => n + 1);
        const token = parsed.payload;
        // En vivo el retraso llega en segundos (resolución del evento on-chain).
        const lagMs =
          token.onchain_lag_seconds === null || token.onchain_lag_seconds === undefined
            ? null
            : token.onchain_lag_seconds * 1000;
        setRows((current) =>
          [{ ...token, fresh: true, detection_lag_ms: lagMs }, ...current].slice(0, MAX_ROWS),
        );
      };
    };

    connect();
    return () => {
      stopped = true;
      clearTimeout(retry);
      socket.current?.close();
    };
  }, []);

  // Los que más han explotado: se refresca en bucle, la foto la mantiene el worker.
  useEffect(() => {
    let stopped = false;

    // Aviso del navegador cuando una estampida cruza el umbral de capitalización. No hace
    // falta ninguna credencial: es la API de notificaciones del propio navegador.
    // Aviso cuando aparece una SERIE nueva. Se avisa una vez por símbolo, no por token: lo
    // relevante es que la serie exista, no cada iteración suya.
    const notifySeries = (lista: SeriesEntry[]) => {
      if (typeof Notification === 'undefined' || Notification.permission !== 'granted') return;
      for (const s of lista) {
        if (seriesAvisadas.current.has(s.key)) continue;
        seriesAvisadas.current.add(s.key);
        const cad =
          s.cadence_seconds === null
            ? 'cadencia desconocida'
            : s.cadence_seconds < 3600
              ? `una cada ${Math.round(s.cadence_seconds / 60)} min`
              : `una cada ${(s.cadence_seconds / 3600).toFixed(1)} h`;
        new Notification(`🔁 SERIE · ${s.symbol}`, {
          body:
            `${s.members} lanzamientos, ${s.pumped} graduaron (${cad}). ` +
            `Mejor pico ${s.best_peak_sol.toFixed(0)} ◎. ` +
            (s.decaying ? 'Ya se está AGOTANDO: cada iteración vale menos. ' : '') +
            'Es una señal para MIRAR, no una recomendación.',
          tag: `serie-${s.key}`,
        });
      }
    };

    const notifyCrossings = (tokens: StampedeToken[]) => {
      if (soloSeries) return;
      if (typeof Notification === 'undefined' || Notification.permission !== 'granted') return;
      for (const token of tokens) {
        if (token.market_cap_sol < NOTIFY_CAP_SOL || notified.current.has(token.mint)) continue;
        // No se avisa de lo que ya se está desinflando: un aviso sobre un token en caída
        // sostenida es peor que no avisar, porque invita a entrar tarde.
        if (token.state === 'enfriando' || token.state === 'cayendo') continue;
        // Tampoco se avisa de una ráfaga que son cuatro carteras girando volumen: avisar de
        // un montaje es peor que no avisar.
        if ((token.trades_per_wallet ?? 0) >= 3) continue;
        if ((token.top_wallet_share ?? 0) >= 0.5) continue;
        notified.current.add(token.mint);
        const label = token.symbol || token.mint.slice(0, 8);
        new Notification(`🌊 ${label} · ${token.market_cap_sol.toFixed(0)} ◎`, {
          body:
            `Lanzamiento en estampida (${token.launch_trades} operaciones en ` +
            `${token.window_seconds}s) y ya va ×${token.growth.toFixed(2)}. ` +
            `Es una señal para MIRAR, no una recomendación.`,
          tag: token.mint,
        });
      }
    };

    const load = async () => {
      try {
        // En «solo subidas» el motor concentra el esfuerzo: deja de pedir los rankings que esa
        // vista no usa y dedica el presupuesto a refrescar los precios más a menudo.
        const [stampedeRes, priceRes, gradRes, seriesRes] = await Promise.all([
          fetch(`${API}/v1/stampede`),
          fetch(`${API}/v1/price`),
          // La graduación se pide SIEMPRE, incluso en modo concentrado: es la señal más rara
          // del sistema y perdérsela por ahorrar una petición no compensa.
          fetch(`${API}/v1/graduating`),
          // Las series van en el MISMO ciclo que el resto: viajan en paralelo, así que no
          // añaden ni un milisegundo de espera a lo que ya se estaba pidiendo.
          fetch(`${API}/v1/series`),
        ]);
        const burst = (await stampedeRes.json()) as { tokens: StampedeToken[] };
        const quote = (await priceRes.json()) as { sol: SolPrice | null };
        const grad = (await gradRes.json()) as { tokens: GraduatingToken[] };
        const ser = (await seriesRes.json()) as { series: SeriesEntry[] };
        if (!stopped) {
          // El worker conoce el máximo REAL de los tokens que sigue, medido operación a
          // operación. Se incorpora al registro del navegador, que solo ve lo que le llega.
          for (const t of burst.tokens ?? []) {
            const known = peakByMint.current.get(t.mint) ?? 0;
            if (t.peak_market_cap_sol > known) {
              peakByMint.current.set(t.mint, t.peak_market_cap_sol);
            }
          }
          setStampede(burst.tokens ?? []);
          setGraduating(grad.tokens ?? []);
          setSeriesList(ser.series ?? []);
          setPrice(quote.sol ?? null);
          notifyCrossings(burst.tokens ?? []);
          notifySeries(ser.series ?? []);
        }
        if (!focusRising) {
          const [moversRes, zoneRes] = await Promise.all([
            fetch(`${API}/v1/top-movers`),
            fetch(`${API}/v1/hot-zone`),
          ]);
          const movers = (await moversRes.json()) as { movers: TopMover[] };
          const zone = (await zoneRes.json()) as { tokens: HotZoneToken[] };
          if (!stopped) {
            setTopMovers(movers.movers ?? []);
            setHotZone(zone.tokens ?? []);
          }
        }
      } catch {
        /* reintenta en el siguiente tick */
      }
    };
    void load();
    // Sondeo agresivo: 150 ms en modo concentrado, 400 ms en el resto. Son peticiones a la API
    // local (sin coste de red externa) y mantienen nuestro tramo del retraso bajo 200 ms.
    const id = setInterval(load, focusRising ? 150 : 400);
    return () => {
      stopped = true;
      clearInterval(id);
    };
  }, [focusRising, soloSeries]);

  // Las posiciones simuladas sobreviven a una recarga: si no, cerrar la pestaña por error
  // borraría el seguimiento y no se sabría cómo acabó.
  useEffect(() => {
    try {
      const stored = localStorage.getItem('mit.paper');
      if (stored) setPositions(JSON.parse(stored) as PaperPosition[]);
    } catch {
      /* almacenamiento no disponible: se sigue en memoria */
    }
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem('mit.paper', JSON.stringify(positions));
    } catch {
      /* sin persistencia, pero la sesión actual funciona igual */
    }
  }, [positions]);

  // Deja preparada la parte cara de cada posición real abierta. Cuando salte el stop, la
  // venta ya está caliente y el clic no espera a que se descubran cuentas.
  const openRealMints = positions
    .filter((p) => p.real && p.closedCap === undefined)
    .map((p) => p.mint)
    .join(',');
  useEffect(() => {
    if (!openRealMints) return;
    for (const mint of openRealMints.split(',')) void warmToken(mint);
  }, [openRealMints]);

  // Si el usuario ya autorizó este sitio, Phantom reconecta sin pedir nada.
  useEffect(() => {
    void reconnectWallet().then((key) => {
      if (key) setWallet(key);
    });
  }, []);

  // Seguimiento en vivo: actualiza el máximo y cierra al saltar el trailing stop.
  useEffect(() => {
    if (positions.length === 0) return;
    // Los precios vienen de las dos fuentes: la foto de estampidas y el radar en vivo. Una
    // posición abierta desde potenciales no aparece en la lista de estampidas, y sin esto se
    // quedaría congelada sin seguimiento.
    const capByMint = new Map<string, number>();
    for (const row of rows) {
      if (row.market_cap_sol) capByMint.set(row.mint, row.market_cap_sol);
    }
    for (const t of stampede) capByMint.set(t.mint, t.market_cap_sol);
    // El seguimiento propio va el ÚLTIMO porque es el más fiable: no caduca cuando el token
    // sale de las listas, que es justo cuando el trailing stop dejaría de funcionar.
    for (const [mint, cap] of Object.entries(trackedCaps)) capByMint.set(mint, cap);
    if (capByMint.size === 0) return;
    setPositions((current) => {
      let changed = false;
      const next = current.map((p) => {
        const cap = capByMint.get(p.mint);
        if (p.closedCap !== undefined || cap === undefined || cap <= 0) return p;
        const peak = Math.max(p.peakCap, cap);
        // El stop salta en el primer retroceso que supera el umbral desde el máximo.
        if (peak > 0 && (peak - cap) / peak >= p.trailing) {
          changed = true;
          // Con dinero real no se cierra sola: vender exige una firma en la cartera, y darla
          // por cerrada sin haber vendido dejaría al usuario creyendo que salió cuando sigue
          // dentro. Se marca, se avisa, y el botón de vender queda a un clic.
          if (p.real) {
            return p.stopHit ? p : { ...p, peakCap: peak, stopHit: true };
          }
          return {
            ...p,
            peakCap: peak,
            closedCap: cap,
            closedAt: Date.now(),
            closedReason: 'trailing stop',
          };
        }
        if (peak !== p.peakCap) {
          changed = true;
          return { ...p, peakCap: peak };
        }
        return p;
      });
      return changed ? next : current;
    });
  }, [stampede, rows, trackedCaps, positions.length]);

  /** Abre una posición simulada sobre cualquier token con capitalización conocida.
   *
   * Es genérico a propósito: sirve igual desde el radar, desde potenciales o desde estampida,
   * porque la mecánica de la simulación no depende de por qué pestaña llegó el token. */
  const activate = (mint: string, symbol: string, cap: number | null | undefined) => {
    const amountEur = Number(sizeInput.replace(',', '.'));
    const trailing = Number(trailingInput.replace(',', '.')) / 100;
    if (!Number.isFinite(amountEur) || amountEur <= 0) return;
    if (!Number.isFinite(trailing) || trailing <= 0 || trailing >= 1) return;
    // Sin precio no se puede convertir euros a SOL, y adivinarlo falsearía la posición entera.
    if (price === null || price.eur <= 0) return;
    if (cap === null || cap === undefined || cap <= 0) return;
    if (positions.some((p) => p.mint === mint && p.closedCap === undefined)) return;

    if (realMode) {
      void openReal(mint, symbol, cap, amountEur, trailing);
      return;
    }

    setPositions((current) => [
      {
        mint,
        symbol: symbol || mint.slice(0, 8),
        sizeSol: amountEur / price.eur,
        sizeEur: amountEur,
        entryEurPerSol: price.eur,
        entryCap: cap,
        entryAt: Date.now(),
        trailing,
        peakCap: cap,
      },
      ...current,
    ]);
  };

  const slippagePct = () => {
    const value = Number(slippageInput.replace(',', '.'));
    return Number.isFinite(value) && value > 0 && value < 50 ? value : 10;
  };

  /** Compra de verdad y, SOLO si la cadena la confirma, abre la posición.
   *
   * El orden importa: abrir la posición antes de la confirmación pinta un resultado sobre una
   * compra que puede no haber entrado, y a partir de ahí todo lo que se muestre es falso.
   */
  const openReal = async (
    mint: string,
    symbol: string,
    cap: number,
    amountEur: number,
    trailing: number,
  ) => {
    if (trading.current) return;
    if (!wallet) {
      setNotice({ text: 'Conecta Phantom antes de comprar.', kind: 'bad' });
      return;
    }
    if (price === null || price.eur <= 0) return;

    trading.current = true;
    setNotice({ text: `Preparando compra de ${symbol}… confírmala en Phantom.`, kind: 'wait' });
    try {
      const { signature, summary } = await buyOnChain(
        mint,
        wallet,
        amountEur / price.eur,
        slippagePct(),
      );
      setNotice({ text: `Enviada, esperando confirmación…`, kind: 'wait' });
      const result = await waitForConfirmation(signature);

      if (result.state === 'confirmada') {
        setPositions((current) => [
          {
            mint,
            symbol: symbol || mint.slice(0, 8),
            sizeSol: amountEur / price.eur,
            sizeEur: amountEur,
            entryEurPerSol: price.eur,
            entryCap: cap,
            entryAt: Date.now(),
            trailing,
            peakCap: cap,
            real: true,
            signature,
          },
          ...current,
        ]);
        const tokens = summary.tokens_expected ?? 0;
        setNotice({ text: `Comprado ${symbol}: ~${tokens.toLocaleString('es-ES')} tokens.`, kind: 'ok' });
      } else if (result.state === 'fallida') {
        setNotice({ text: `La red rechazó la compra: ${result.error ?? 'sin detalle'}`, kind: 'bad' });
      } else {
        setNotice({
          text: 'Compra sin confirmar todavía. No se abre la posición para no contar algo que quizá no entró.',
          kind: 'bad',
        });
      }
    } catch (error) {
      setNotice({ text: `No se compró: ${(error as Error).message}`, kind: 'bad' });
    } finally {
      trading.current = false;
    }
  };

  /** Vende de verdad el porcentaje indicado y cierra la posición si se vendió entera. */
  const closeReal = async (position: PaperPosition, percent = 100) => {
    if (trading.current) return;
    if (!wallet) {
      setNotice({ text: 'Conecta Phantom antes de vender.', kind: 'bad' });
      return;
    }
    trading.current = true;
    setNotice({ text: `Preparando venta de ${position.symbol}… confírmala en Phantom.`, kind: 'wait' });
    try {
      const { signature } = await sellOnChain(position.mint, wallet, percent, slippagePct());
      setNotice({ text: 'Venta enviada, esperando confirmación…', kind: 'wait' });
      const result = await waitForConfirmation(signature);

      if (result.state === 'confirmada') {
        if (percent >= 100) {
          const cap = liveCap(position.mint);
          setPositions((current) =>
            current.map((p) =>
              p.mint === position.mint && p.entryAt === position.entryAt
                ? {
                    ...p,
                    closedCap: cap ?? p.peakCap,
                    closedAt: Date.now(),
                    closedReason: p.stopHit ? 'stop · vendida' : 'vendida',
                    stopHit: false,
                  }
                : p,
            ),
          );
        } else {
          // Se encogen LOS DOS tamaños. El resultado se calcula sobre `sizeSol`, así que
          // tocar solo los euros dejaría la posición mostrando el beneficio de una cantidad
          // de tokens que ya no se tiene.
          const remaining = 1 - percent / 100;
          setPositions((current) =>
            current.map((p) => {
              if (p.mint !== position.mint || p.entryAt !== position.entryAt) return p;
              const next: PaperPosition = {
                ...p,
                sizeSol: p.sizeSol * remaining,
                stopHit: false,
              };
              if (p.sizeEur !== undefined) next.sizeEur = p.sizeEur * remaining;
              return next;
            }),
          );
        }
        setNotice({ text: `Vendido el ${percent}% de ${position.symbol}.`, kind: 'ok' });
      } else if (result.state === 'fallida') {
        setNotice({ text: `La red rechazó la venta: ${result.error ?? 'sin detalle'}`, kind: 'bad' });
      } else {
        setNotice({ text: 'Venta sin confirmar todavía.', kind: 'bad' });
      }
    } catch (error) {
      setNotice({ text: `No se vendió: ${(error as Error).message}`, kind: 'bad' });
    } finally {
      trading.current = false;
    }
  };

  /** Capitalización actual de un mint, mirando en TODAS las fuentes en vivo.
   *
   * El orden importa: primero el seguimiento propio de las posiciones abiertas, que es el único
   * que no caduca. El radar solo guarda los 100 tokens más recientes y entran ~30 por minuto,
   * así que un token sale de la lista en unos 3 minutos y la posición se quedaba sin precio. */
  const liveCap = (mint: string): number | undefined =>
    trackedCaps[mint] ??
    stampede.find((t) => t.mint === mint)?.market_cap_sol ??
    rows.find((r) => r.mint === mint)?.market_cap_sol ??
    undefined;

  const closeNow = (mint: string) => {
    const open = positions.find((p) => p.mint === mint && p.closedCap === undefined);
    // Una posición real no se cierra tachándola de una lista: hay tokens de verdad que hay
    // que vender, y eso exige una firma.
    if (open?.real) {
      void closeReal(open, 100);
      return;
    }
    const cap = liveCap(mint);
    setPositions((current) =>
      current.map((p) =>
        p.mint === mint && p.closedCap === undefined
          ? { ...p, closedCap: cap ?? p.peakCap, closedAt: Date.now(), closedReason: 'manual' }
          : p,
      ),
    );
  };

  /** Quita UNA posición del historial. `entryAt` distingue varias entradas del mismo token. */
  const removePosition = (mint: string, entryAt: number) => {
    setPositions((current) => current.filter((p) => !(p.mint === mint && p.entryAt === entryAt)));
  };

  /** Vuelve a abrir una posición sobre el mismo token, al precio de AHORA.
   *
   * Reutiliza el importe y el trailing de la posición anterior, no los del formulario: si
   * cogiera los del formulario, cambiar el importe alteraría en silencio lo que el usuario
   * cree que está repitiendo. */
  const rebuy = (previous: PaperPosition) => {
    const cap = liveCap(previous.mint);
    if (cap === undefined || cap <= 0) return;
    if (price === null || price.eur <= 0) return;
    if (positions.some((p) => p.mint === previous.mint && p.closedCap === undefined)) return;
    const amountEur = previous.sizeEur ?? previous.sizeSol * price.eur;

    if (realMode) {
      void openReal(previous.mint, previous.symbol, cap, amountEur, previous.trailing);
      return;
    }

    setPositions((current) => [
      {
        mint: previous.mint,
        symbol: previous.symbol,
        sizeSol: amountEur / price.eur,
        sizeEur: amountEur,
        entryEurPerSol: price.eur,
        entryCap: cap,
        entryAt: Date.now(),
        trailing: previous.trailing,
        peakCap: cap,
      },
      ...current,
    ]);
  };

  const openPositions = positions.filter((p) => p.closedCap === undefined);
  // Clave estable con los mints abiertos: evita reiniciar el sondeo en cada repintado.
  const openMintsKey = openPositions
    .map((p) => p.mint)
    .sort()
    .join(',');

  // Seguimiento PROPIO de cada posición abierta. Consulta el precio del token directamente,
  // sin depender de que siga apareciendo en el radar o en la lista de estampidas.
  useEffect(() => {
    if (!openMintsKey) return;
    const mints = openMintsKey.split(',');
    let stopped = false;

    const load = async () => {
      const found: Record<string, number> = {};
      const graduated: string[] = [];
      await Promise.all(
        mints.map(async (mint) => {
          try {
            const response = await fetch(`${API}/v1/tokens/${encodeURIComponent(mint)}/live`);
            const data = (await response.json()) as {
              graduated?: boolean;
              snapshot?: { market_cap_sol?: number } | null;
            };
            // Si graduó, su precio ya se mide en PumpSwap: NO es comparable con la entrada,
            // que se tomó en la bonding curve. Mezclarlos inventa un desplome del 90%.
            if (data.graduated) {
              graduated.push(mint);
              return;
            }
            const cap = data.snapshot?.market_cap_sol;
            if (typeof cap === 'number' && cap > 0) found[mint] = cap;
          } catch {
            /* se reintenta en el siguiente tick; se conserva el último valor conocido */
          }
        }),
      );
      if (stopped) return;
      if (Object.keys(found).length > 0) {
        setTrackedCaps((current) => ({ ...current, ...found }));
      }
      if (graduated.length > 0) {
        setPositions((current) =>
          current.map((p) =>
            graduated.includes(p.mint) && p.closedCap === undefined && !p.graduatedAfter
              ? { ...p, graduatedAfter: true }
              : p,
          ),
        );
      }
    };

    void load();
    const id = setInterval(load, 2500);
    return () => {
      stopped = true;
      clearInterval(id);
    };
  }, [openMintsKey]);

  // Tokens que SOLO están subiendo: pegados a su máximo, sin retroceso apreciable.
  // Se combinan las dos fuentes: las estampidas traen caída medida desde el pico; del radar se
  // aceptan los que acaban de moverse al alza, que es todo lo que se sabe de ellos.
  const rising: RisingToken[] = (() => {
    const found = new Map<string, RisingToken>();
    const nowMs = Date.now();
    // Velocidad de subida: cuánto ha crecido por minuto desde que lo vimos. Es lo que separa
    // "sube como la espuma" de "lleva media hora arrastrandose un 5% arriba".
    const rate = (g: number, ageSeconds: number) =>
      ageSeconds > 5 ? (g - 1) / (ageSeconds / 60) : 0;

    // REBÚSQUEDA COMPLETA: se recorre todo el radar, no solo lo que ya estuviera marcado. El
    // filtro de admisión es `checkConstantRise`, no un único tick al alza.
    for (const row of rows) {
      const g = growth(row.market_cap_sol);
      if (g === null || g <= 1) continue;
      const cap = row.market_cap_sol ?? 0;
      // Caída REAL desde el máximo visto. Antes se asumía 0 y por eso un token desplomado
      // entraba aquí en cuanto rebotaba un tick: es el caso que hay que excluir.
      const peak = Math.max(peakByMint.current.get(row.mint) ?? cap, cap);
      const drawdown = peak > 0 ? ((peak - cap) / peak) * 100 : 0;
      const seen = row.received_timestamp ? Date.parse(row.received_timestamp) : NaN;
      const age = Number.isNaN(seen) ? 0 : (nowMs - seen) / 1000;
      const serie = histByMint.current.get(row.mint) ?? [];
      found.set(row.mint, {
        mint: row.mint,
        symbol: row.symbol,
        name: row.name,
        cap,
        growth: g,
        drawdownPct: drawdown,
        climbPerMin: rate(g, age),
        toBigCapMultiple: cap > 0 ? BIG_CAP_SOL / cap : Infinity,
        ageSeconds: age,
        fromStampede: false,
        serie,
        risingSeconds: riseStart.current.has(row.mint)
          ? (nowMs - (riseStart.current.get(row.mint) ?? nowMs)) / 1000
          : 0,
        targetCap: row.ceiling_sol ?? null,
        targetCapHigh: row.ceiling_high_sol ?? null,
        targetSample: row.ceiling_sample ?? null,
        probGrad: row.prob_grad ?? null,
        prob100k: row.prob_100k ?? null,
        rise: checkConstantRise({ serie, drawdownPct: drawdown }),
      });
    }
    for (const t of stampede) {
      if (t.growth <= 1) continue;
      if (t.state === 'enfriando' || t.state === 'cayendo') continue;
      const age = t.age_seconds ?? 0;
      const serie = histByMint.current.get(t.mint) ?? [];
      found.set(t.mint, {
        mint: t.mint,
        symbol: t.symbol,
        name: t.name,
        cap: t.market_cap_sol,
        growth: t.growth,
        drawdownPct: t.drawdown_pct,
        climbPerMin: rate(t.growth, age),
        toBigCapMultiple: t.market_cap_sol > 0 ? BIG_CAP_SOL / t.market_cap_sol : Infinity,
        ageSeconds: age,
        ...(t.momentum !== undefined ? { momentum: t.momentum } : {}),
        ...(t.unique_wallets !== undefined ? { uniqueWallets: t.unique_wallets } : {}),
        ...(t.launch_trades !== undefined ? { launchTrades: t.launch_trades } : {}),
        ...(t.buy_ratio !== undefined ? { buyRatio: t.buy_ratio } : {}),
        ...(t.unique_buyers !== undefined ? { uniqueBuyers: t.unique_buyers } : {}),
        ...(t.trades_total !== undefined ? { tradesTotal: t.trades_total } : {}),
        fromStampede: true,
        serie,
        risingSeconds: riseStart.current.has(t.mint)
          ? (nowMs - (riseStart.current.get(t.mint) ?? nowMs)) / 1000
          : 0,
        // Techo EMPÍRICO condicionado a donde está AHORA: la mediana de lo que alcanzaron los
        // tokens del corpus que llegaron al menos hasta aquí.
        targetCap: t.ceiling_sol ?? null,
        targetCapHigh: t.ceiling_high_sol ?? null,
        targetSample: t.ceiling_sample ?? null,
        probGrad: t.prob_grad ?? null,
        prob100k: t.prob_100k ?? null,
        rise: checkConstantRise({
          serie,
          drawdownPct: t.drawdown_pct,
          ...(t.buy_ratio !== undefined ? { buyRatio: t.buy_ratio } : {}),
          ...(t.top_wallet_share !== undefined ? { topWalletShare: t.top_wallet_share } : {}),
        }),
      });
    }
    // Solo los que suben DE FORMA CONSTANTE. El barrendero también aplica aquí.
    // Los que llevan MÁS TIEMPO subiendo van primero: una subida sostenida es más rara que
    // un pico de velocidad, y era lo que quedaba enterrado al ordenar solo por velocidad.
    return [...found.values()]
      .filter((t) => t.rise.ok)
      .filter((t) => !sweeper || t.climbPerMin >= FAST_CLIMB_PER_MIN)
      .sort((a, b) => {
        const aSost = a.risingSeconds >= SUSTAINED_RISE_SECONDS ? 1 : 0;
        const bSost = b.risingSeconds >= SUSTAINED_RISE_SECONDS ? 1 : 0;
        if (aSost !== bSost) return bSost - aSost;
        if (aSost === 1) return b.risingSeconds - a.risingSeconds;
        return b.climbPerMin - a.climbPerMin;
      });
  })();

  // Barrendero: motivo por el que cada token es basura (o null si merece estar en pantalla).
  // Se calcula una sola vez y se reutiliza, para no recorrer la lista de estampidas por fila.
  const stampedeByMint = new Map(stampede.map((t) => [t.mint, t]));
  const sweepNow = Date.now();
  const garbage = new Map<string, string>();
  for (const row of rows) {
    const reason = garbageReason(row, stampedeByMint, sweepNow);
    if (reason) garbage.set(row.mint, reason);
  }

  const clean = (list: Row[]) => (sweeper ? list.filter((r) => !garbage.has(r.mint)) : list);

  // Detecta quién SALE de «solo subidas» y con qué motivo. Se compara contra la foto
  // anterior: lo que estaba y ya no está, se archiva con el porqué.
  const risingKey = rising.map((t) => t.mint).join(',');
  useEffect(() => {
    const ahora = new Set(rising.map((t) => t.mint));
    for (const t of rising) risingSeen.current.set(t.mint, t);

    const salidos: DroppedToken[] = [];
    for (const [mint, antes] of risingSeen.current) {
      if (ahora.has(mint)) continue;
      risingSeen.current.delete(mint);
      const capActual = liveCap(mint) ?? antes.cap;
      const pico = Math.max(peakByMint.current.get(mint) ?? antes.cap, antes.cap);
      const caida = pico > 0 ? ((pico - capActual) / pico) * 100 : 0;
      // El motivo se deduce de lo que falló: o se desinfló, o dejó de acelerar.
      const reason =
        caida > RISING_MAX_DRAWDOWN_PCT
          ? `se desinfló −${caida.toFixed(1)}% desde su máximo`
          : 'dejó de subir deprisa';
      salidos.push({
        mint,
        symbol: antes.symbol,
        name: antes.name,
        peakCap: pico,
        exitCap: capActual,
        maxGrowth: pico / BIRTH_CAP_SOL,
        reason,
        at: Date.now(),
      });
    }
    if (salidos.length > 0) {
      setDropped((current) => [...salidos, ...current].slice(0, 40));
    }
    // `risingKey` cambia solo cuando cambia el CONJUNTO de tokens, no en cada repintado.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [risingKey]);

  const potentials = clean(rows.filter(isPotential));
  // En la vista de potenciales se ordena por puntuación: los altos arriba del todo, los bajos
  // al fondo. En el radar se respeta el orden cronológico, que es lo que se espera de un radar.
  const visible = onlyPotentials
    ? [...potentials].sort((a, b) => potentialScore(b) - potentialScore(a))
    : clean(rows);

  return (
    <div className="wrap">
      <header>
        <h1>Memecoin Intelligence Terminal</h1>
        {/* La etiqueta dice lo que el terminal hace AHORA. Dejarla en «solo lectura» cuando
            los botones ya mueven dinero sería la señal más peligrosa de toda la pantalla. */}
        <span className={realMode ? 'badge' : 'badge read-only'}>
          {realMode ? '💸 operativa real' : 'solo lectura'}
        </span>
        <a className="badge" href="/prevision">previsión tokens →</a>
        <span className="status">
          <span className={connected ? 'dot live' : 'dot'} />
          {connected ? 'en vivo' : 'desconectado'}
        </span>
        {price && (
          <span
            className="status"
            title={
              price.stale
                ? 'No se pudo refrescar el precio: se muestra el último conocido.'
                : `Actualizado hace ${price.age_seconds}s · CoinGecko`
            }
          >
            1 ◎ ={' '}
            {price.eur.toLocaleString('es-ES', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €
            {price.stale ? ' (antiguo)' : ''}
          </span>
        )}
        <span className="tabs">
          <button
            className={view === 'radar' ? 'badge potential-tab on' : 'badge potential-tab'}
            onClick={() => setView('radar')}
            title="Todos los tokens detectados, según nacen."
          >
            radar ({rows.length})
          </button>
          <button
            className={view === 'potenciales' ? 'badge potential-tab on' : 'badge potential-tab'}
            onClick={() => setView('potenciales')}
            title="Tokens subiendo con fuerza y creador no-spam. No es una predicción."
          >
            ★ potenciales ({potentials.length})
          </button>
          <button
            className={view === 'graduando' ? 'badge potential-tab on' : 'badge potential-tab'}
            onClick={() => setView('graduando')}
            title="Tokens que están completando su curva. Graduar es el suceso más raro de pump.fun: lo consigue en torno al 1-3%. Al completarse, el token pasa a PumpSwap."
          >
            🎓 graduando ({graduating.filter((g) => !g.graduated).length})
          </button>
          <button
            className={sweeper ? 'badge potential-tab on' : 'badge potential-tab'}
            onClick={() => setSweeper((v) => !v)}
            title={
              sweeper
                ? `Barrendero ENCENDIDO: ${garbage.size} tokens ocultos. Se barre lo que se desplomó, lo que cotiza por debajo de su nacimiento, los creadores en serie (${SWEEP_SPAM_LAUNCHES}+ lanzamientos) y lo que sigue plano tras ${SWEEP_GRACE_SECONDS}s. Pulsa para verlo todo.`
                : 'Barrendero apagado: se muestra todo, incluida la basura. Pulsa para limpiar.'
            }
          >
            🧹 {sweeper ? `barridos (${garbage.size})` : 'sin filtrar'}
          </button>
          <button
            className={view === 'subidas' ? 'badge potential-tab on' : 'badge potential-tab'}
            onClick={() => setView('subidas')}
            title={`Tokens en subida CONSTANTE: al menos ${(MIN_UP_FRACTION * 100).toFixed(0)}% de movimientos al alza, sin ningún bajón mayor del ${(MAX_SINGLE_DIP * 100).toFixed(0)}%, en su máximo, y —cuando hay dato— más compras que ventas. En esta pestaña el motor deja de pedir el resto de rankings y refresca los precios más rápido.`}
          >
            ↑ solo subidas ({rising.length})
          </button>
          <button
            className={view === 'zona' ? 'badge potential-tab on' : 'badge potential-tab'}
            onClick={() => setView('zona')}
            title="Tokens en la banda 170-360 ◎ (unos 11.000-23.000 €), con la tasa base REAL de cuántos siguieron hasta explotar."
          >
            ⚡ zona 170-360 ◎ ({hotZone.length})
          </button>
          <button
            className={view === 'estampida' ? 'badge potential-tab on' : 'badge potential-tab'}
            onClick={() => setView('estampida')}
            title={`Lanzamientos con ≥100 operaciones en sus primeros 15s (el patrón de V713). Medido: la mediana de un lanzamiento normal es 8.`}
          >
            🌊 estampida ({stampede.length})
            {openPositions.length > 0 ? (
              <span className="good"> · 📄{openPositions.length}</span>
            ) : null}
          </button>
          <button
            className={view === 'series' ? 'badge potential-tab on' : 'badge potential-tab'}
            onClick={() => setView('series')}
            title="Símbolos relanzados una y otra vez donde algún miembro ANTERIOR ya bombeó. Es el patrón que vimos con $TNOS: una iteración cada ~45 min, cada una valiendo la mitad que la anterior."
          >
            🔁 series ({seriesList.length})
            {openPositions.length > 0 ? (
              <span className="good"> · 📄{openPositions.length}</span>
            ) : null}
          </button>
        </span>
      </header>

      <div className="stats">
        <div className="stat">
          <div className="label">En pantalla</div>
          <div className="value">{rows.length}</div>
        </div>
        <div className="stat">
          <div className="label">Detectados en esta sesión</div>
          <div className="value">{detected}</div>
        </div>
      </div>

      {/* Panel de simulación: visible en TODAS las pestañas. Los importes se fijan aquí y el
          botón «activar» de cualquier tabla los usa, así que tiene que estar siempre a mano. */}
      <section className="movers">
        <h2>
          {realMode ? '💸 Operativa real' : '📄 Simulación'}{' '}
          <span className={realMode ? 'badge' : 'badge read-only'}>
            {realMode ? 'dinero real · firmas tú' : 'papel · sin dinero real'}
          </span>
        </h2>
        <div className="notify-row">
          <label className="paper-field">
            € por operación
            <input value={sizeInput} onChange={(e) => setSizeInput(e.target.value)} />
          </label>
          <label className="paper-field">
            Trailing stop (%)
            <input value={trailingInput} onChange={(e) => setTrailingInput(e.target.value)} />
          </label>
          <label className="paper-field">
            Slippage (%)
            <input value={slippageInput} onChange={(e) => setSlippageInput(e.target.value)} />
          </label>
          {wallet === null ? (
            <button
              className="activate-btn"
              onClick={() =>
                void connectWallet()
                  .then((key) => {
                    setWallet(key);
                    setNotice({ text: 'Cartera conectada.', kind: 'ok' });
                  })
                  .catch((error: Error) => setNotice({ text: error.message, kind: 'bad' }))
              }
            >
              conectar Phantom
            </button>
          ) : (
            <span className="mono">
              🔑 {wallet.slice(0, 4)}…{wallet.slice(-4)}{' '}
              <button
                className="activate-btn"
                onClick={() => {
                  setRealMode((v) => !v);
                  setNotice(null);
                }}
                title={
                  realMode
                    ? 'Vuelve a papel: los botones dejan de mover dinero.'
                    : 'Los botones pasarán a comprar y vender de verdad, firmando en tu cartera.'
                }
              >
                {realMode ? 'pasar a papel' : 'pasar a REAL'}
              </button>
            </span>
          )}
          <span className="mono">
            {realMode ? (
              <>
                Pulsa <strong>activar</strong> para <strong>comprar de verdad</strong> ese token
                con este importe. Cada operación te la enseña Phantom antes de enviarla. El
                trailing stop <strong>avisa y deja la venta a un clic</strong>: no vende solo,
                porque firmar exige que estés delante.
              </>
            ) : (
              <>
                Pulsa <strong>activar</strong> en cualquier token para abrir una posición
                simulada con este importe. No se envía ninguna orden ni se firma nada. Costes
                aplicados: {FEE_PER_SIDE * 100}% comisión + {SLIPPAGE_PER_SIDE * 100}%
                deslizamiento por lado.
              </>
            )}
          </span>
        </div>
        {notice && (
          <p className={`mono ${notice.kind === 'ok' ? 'good' : notice.kind === 'bad' ? 'warn' : ''}`}>
            {notice.kind === 'wait' ? '⏳ ' : notice.kind === 'ok' ? '✅ ' : '⚠️ '}
            {notice.text}
          </p>
        )}
      </section>

      {positions.length > 0 && (
        <section className="movers">
          <h2>
            {positions.some((p) => p.real) ? '💸 Posiciones' : '📄 Posiciones simuladas'}{' '}
            {positions.some((p) => p.real) ? (
              <span className="badge">incluye dinero real</span>
            ) : (
              <span className="badge read-only">papel · sin dinero real</span>
            )}
          </h2>
          <table className="zone-table">
            <thead>
              <tr>
                <th>Token</th>
                <th>Tamaño</th>
                <th>Entrada</th>
                <th>Actual</th>
                <th>Máximo</th>
                <th title="Crecimiento del TOKEN desde su nacimiento (27,96 ◎). No es tu resultado.">Crece</th>
                <th title="Caída del token desde el máximo que alcanzó mientras tenías la posición.">Desde pico</th>
                <th>Resultado</th>
                <th>Estado</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {positions.map((p) => {
                const live = liveCap(p.mint);
                const priceNow = p.closedCap ?? live ?? p.peakCap;
                // Tras graduar, el precio se mide en PumpSwap con otra escala: valorar la
                // posición contra la entrada de la curva daría un desplome inventado.
                const comparable = !p.graduatedAfter;
                const profit = comparable ? paperProfit(p, priceNow) : 0;
                const pct = comparable ? (profit / p.sizeSol) * 100 : 0;
                return (
                  <tr key={`${p.mint}-${p.entryAt}`}>
                    <td className="symbol">{p.symbol}</td>
                    <td className="mono" title={`${p.sizeSol.toFixed(6)} ◎`}>
                      {/* Las posiciones abiertas antes de manejar euros no guardan el importe
                          en euros: se convierte al precio actual para no dejarlas en SOL. */}
                      {p.sizeEur !== undefined
                        ? `${p.sizeEur.toLocaleString('es-ES', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €`
                        : (toEur(p.sizeSol, price) ?? formatCap(p.sizeSol))}
                    </td>
                    <td className="mono" title={formatCap(p.entryCap)}>
                      {formatMoney(p.entryCap, price)}
                    </td>
                    <td className="mono" title={formatCap(priceNow)}>
                      {formatMoney(priceNow, price)}
                      {/* Si el token ya no aparece en ninguna lista en vivo, el precio deja de
                          actualizarse. Decirlo es obligatorio: una cifra vieja sin avisar se
                          lee como si fuera actual. */}
                      {p.closedCap === undefined && live === undefined ? (
                        <span className="warn" title="El token ya no está en las listas en vivo: esta cifra no se está actualizando.">
                          {' '}· sin señal
                        </span>
                      ) : null}
                    </td>
                    <td className="mono" title={formatCap(p.peakCap)}>
                      {formatMoney(p.peakCap, price)}
                    </td>
                    <td className="mono">
                      {(() => {
                        const g = growth(priceNow);
                        if (!comparable || g === null) return '—';
                        return (
                          <span className={g >= HOT_GROWTH ? 'hot-name' : g < 1 ? 'bad' : undefined}>
                            &times;{g.toFixed(2)}
                          </span>
                        );
                      })()}
                    </td>
                    <td className="mono">
                      {(() => {
                        if (!comparable || p.peakCap <= 0) return '—';
                        const dd = ((p.peakCap - priceNow) / p.peakCap) * 100;
                        return dd > 0.05 ? (
                          <span className={dd >= 20 ? 'bad' : 'warn'}>&minus;{dd.toFixed(1)}%</span>
                        ) : (
                          <span className="good">en máximos</span>
                        );
                      })()}
                    </td>
                    <td className="mono">
                      {comparable ? (
                        <span className={profit >= 0 ? 'good' : 'bad'} title={`${profit.toFixed(6)} ◎`}>
                          {profit >= 0 ? '+' : ''}
                          {(profit * (p.entryEurPerSol ?? price?.eur ?? 0)).toLocaleString('es-ES', {
                            minimumFractionDigits: 2,
                            maximumFractionDigits: 2,
                          })}{' '}
                          € ({pct >= 0 ? '+' : ''}
                          {pct.toFixed(1)}%)
                        </span>
                      ) : (
                        <span
                          className="pending"
                          title="El token graduó: su precio pasó a medirse en PumpSwap, con otra escala. Compararlo con la entrada de la bonding curve daría una cifra falsa."
                        >
                          no comparable
                        </span>
                      )}
                    </td>
                    <td className="mono">
                      {p.closedCap === undefined && p.graduatedAfter ? (
                        <span className="warn" title="Graduó a PumpSwap después de abrir la posición.">
                          🎓 graduó
                        </span>
                      ) : p.closedCap === undefined && p.stopHit ? (
                        <span
                          className="warn"
                          title="El trailing stop ha saltado. La venta no sale sola: pulsa «vender» y fírmala."
                        >
                          ⚠ STOP — vende
                        </span>
                      ) : p.closedCap === undefined ? (
                        <span className="good">
                          ● abierta
                          {p.real && p.signature && (
                            <>
                              {' · '}
                              <a href={explorerUrl(p.signature)} target="_blank" rel="noreferrer">
                                real
                              </a>
                            </>
                          )}
                        </span>
                      ) : (
                        <span className="pending">cerrada · {p.closedReason}</span>
                      )}
                    </td>
                    <td className="mono">
                      <span className="row-actions">
                        {p.closedCap === undefined ? (
                          <>
                            <button
                              className="activate-btn"
                              onClick={() => closeNow(p.mint)}
                              title={
                                p.real
                                  ? 'Vende TODO en Pump.fun. Lo firmas en tu cartera.'
                                  : 'Cierra la posición simulada.'
                              }
                            >
                              {p.real ? (p.stopHit ? '⚠ vender ya' : 'vender') : 'cerrar'}
                            </button>
                            {p.real && (
                              <button
                                className="activate-btn"
                                onClick={() => void closeReal(p, 50)}
                                title="Vende la mitad y deja correr el resto."
                              >
                                50%
                              </button>
                            )}
                          </>
                        ) : (
                          <button
                            className="activate-btn"
                            onClick={() => rebuy(p)}
                            disabled={
                              liveCap(p.mint) === undefined ||
                              positions.some(
                                (o) => o.mint === p.mint && o.closedCap === undefined,
                              )
                            }
                            title="Abre otra posición sobre este token al precio actual, con el mismo importe y trailing."
                          >
                            recomprar
                          </button>
                        )}
                        <button
                          className="remove-btn"
                          onClick={() => removePosition(p.mint, p.entryAt)}
                          title="Quitar solo esta operación del historial"
                          aria-label="Quitar esta operación"
                        >
                          ×
                        </button>
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="mono">
            Total simulado:{' '}
            <strong>
              {positions
                .reduce((sum, p) => {
                  // Las graduadas no se suman: su precio ya no es comparable con la entrada.
                  if (p.graduatedAfter && p.closedCap === undefined) return sum;
                  const live = liveCap(p.mint);
                  const sol = paperProfit(p, p.closedCap ?? live ?? p.peakCap);
                  return sum + sol * (p.entryEurPerSol ?? price?.eur ?? 0);
                }, 0)
                .toLocaleString('es-ES', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}{' '}
              €
            </strong>{' '}
            en {positions.length} operaciones ·{' '}
            <button className="activate-btn" onClick={() => setPositions([])}>
              borrar historial
            </button>
          </p>
        </section>
      )}

      {view !== 'zona' && view !== 'estampida' && view !== 'subidas' && view !== 'graduando' && view !== 'series' && (
      <table>
        <thead>
          <tr>
            <th>Token</th>
            <th>Mint</th>
            <th>Cap</th>
            <th>Crece</th>
            <th title="Puntuación 0-100 de intensidad de señales AHORA (crecimiento + tasa base + momento − creador en serie). NO es probabilidad de que suba.">Potencial</th>
            <th title="Tasa base REAL medida esta sesión: de los tokens que llegaron a este crecimiento, cuántos alcanzaron 300 ◎. No es una predicción.">→300 ◎</th>
            <th>Creador</th>
            <th>Score</th>
            <th>Riesgo</th>
            <th>Holders</th>
            <th>Señal</th>
            <th title="Abre una posición SIMULADA con el importe indicado en la pestaña Estampida. No envía ninguna orden.">Simular</th>
            <th>Visto</th>
            <th title="Segundos transcurridos desde que el token se creó en la cadena hasta que lo detectamos. Cuanto más alto, más tarde llegamos.">Detectado tras</th>
            <th>Analizar</th>
          </tr>
        </thead>
        <tbody>
          {visible.map((row, index) => {
            const g = growth(row.market_cap_sol);
            const hot = g !== null && g >= HOT_GROWTH;
            const launches = row.creator_launches ?? null;
            const score = potentialScore(row);
            const tier = tierOf(score);
            // Cabecera de nivel: solo en la vista clasificada, al cambiar de tramo.
            const previousTier = index > 0 ? tierOf(potentialScore(visible[index - 1]!)) : null;
            const showTierHeader = onlyPotentials && tier !== previousTier;
            const rowClass = [row.fresh ? 'fresh' : '', hot ? 'hot' : ''].filter(Boolean).join(' ');
            return (
            <Fragment key={row.mint}>
            {showTierHeader && (
              <tr className="tier-row">
                <td colSpan={14} className={`tier-${tier}`}>{TIER_LABEL[tier]}</td>
              </tr>
            )}
            <tr className={rowClass || undefined}>
              <td>
                <a
                  className={hot ? 'symbol hot-name token-link' : 'symbol token-link'}
                  href={`https://pump.fun/${row.mint}`}
                  target="_blank"
                  rel="noreferrer"
                  title="Abrir el memecoin en pump.fun"
                >
                  {row.symbol || '—'}
                </a>
                {/* Con el barrendero apagado se marca la basura en vez de esconderla: así se
                    puede comprobar que el filtro no está tirando nada bueno. */}
                {garbage.has(row.mint) ? (
                  <span className="bad" title={garbage.get(row.mint)}> 🧹</span>
                ) : null}
                <div className="mono">{row.name}</div>
              </td>
              <td className="mono">{short(row.mint)}</td>
              <td className="mono">
                <span
                  className={
                    row.capDir === 'up' ? 'good' : row.capDir === 'down' ? 'bad' : undefined
                  }
                >
                  <span title={formatCap(row.market_cap_sol)}>{formatMoney(row.market_cap_sol, price)}</span>
                </span>
              </td>
              <td className="mono">
                {g === null ? (
                  '—'
                ) : (
                  <span className={hot ? 'hot-name' : g < 1 ? 'bad' : undefined}>
                    ×{g.toFixed(2)}
                  </span>
                )}
              </td>
              <td className="mono">
                <span className={`tier-${tier}`}>{score}</span>
              </td>
              <td className="mono">
                {row.prob_50k === null || row.prob_50k === undefined ? (
                  <span
                    className="pending"
                    title={
                      row.prob_sample && row.prob_sample > 0
                        ? `Muestra insuficiente (${row.prob_sample} tokens en este nivel). Necesita más datos.`
                        : 'Aún sin muestra en esta sesión.'
                    }
                  >
                    —
                  </span>
                ) : (
                  <span
                    className={row.prob_50k >= 0.1 ? 'good' : undefined}
                    title={`Real: de ${row.prob_sample ?? '?'} tokens que llegaron a este crecimiento esta sesión, este % alcanzó 300 ◎. No es una predicción.`}
                  >
                    {(row.prob_50k * 100).toFixed(row.prob_50k < 0.1 ? 1 : 0)}%
                  </span>
                )}
              </td>
              <td className="mono">
                {short(row.creator)}
                {launches !== null && launches > 1 ? (
                  <span
                    className={launches >= 10 ? 'bad' : 'warn'}
                    title={`Este creador ha lanzado ${launches} tokens. Muchos lanzamientos suele ser una fábrica de tokens.`}
                  >
                    {' '}· {launches} lanz.
                  </span>
                ) : null}
                {row.user && row.user !== row.creator ? (
                  <span className="warn" title="El creador no es quien firmó la transacción">
                    {' '}≠ firmante
                  </span>
                ) : null}
              </td>
              <td className="mono">
                {row.analysis ? (
                  <span className={row.analysis.opportunity >= 50 ? 'good' : undefined}>
                    {row.analysis.opportunity.toFixed(0)}
                  </span>
                ) : (
                  <span className="pending">analizando…</span>
                )}
              </td>
              <td className="mono">
                {row.analysis ? (
                  <span
                    className={row.analysis.manipulation_risk >= 40 ? 'bad' : undefined}
                    title={row.analysis.reasons.join(' · ')}
                  >
                    {row.analysis.manipulation_risk}
                    {row.analysis.reasons.length > 0 ? ` (${row.analysis.reasons.length})` : ''}
                  </span>
                ) : (
                  '—'
                )}
              </td>
              <td className="mono">
                {row.analysis
                  ? `${row.analysis.holders}${
                      row.analysis.top10_pct !== null
                        ? ` · top10 ${row.analysis.top10_pct.toFixed(0)}%`
                        : ''
                    }`
                  : '—'}
              </td>
              <td className="mono">
                {row.analysis ? (
                  <span className={row.analysis.eligible ? 'good' : 'bad'}>
                    {row.analysis.signal}
                  </span>
                ) : (
                  '—'
                )}
              </td>
              <td className="mono">
                {positions.some((p) => p.mint === row.mint && p.closedCap === undefined) ? (
                  <span className="good">● activa</span>
                ) : (
                  <button
                    className="activate-btn"
                    onClick={() => activate(row.mint, row.symbol, row.market_cap_sol)}
                  >
                    activar
                  </button>
                )}
              </td>
              <td className="mono">
                {row.received_timestamp
                  ? new Date(row.received_timestamp).toLocaleTimeString('es-ES')
                  : '—'}
              </td>
              <td className="mono">
                <span
                  className={
                    (row.detection_lag_ms ?? 0) >= 5000
                      ? 'bad'
                      : (row.detection_lag_ms ?? 0) <= 1000
                        ? 'good'
                        : undefined
                  }
                  title="Cuanto mayor, más tarde llegamos: el token ya se había movido."
                >
                  {formatDetectionLag(row.detection_lag_ms)}
                </span>
              </td>
              <td className="mono">
                <a className="analyze-link" href={`/prevision?mint=${row.mint}`}>
                  analizar →
                </a>
              </td>
            </tr>
            </Fragment>
            );
          })}
        </tbody>
      </table>
      )}

      {view !== 'zona' && view !== 'estampida' && view !== 'subidas' && view !== 'graduando' && view !== 'series' && rows.length === 0 && (
        <div className="empty">
          Sin tokens todavía. Arranca la ingesta con <code>make ingest</code>.
        </div>
      )}

      {rows.length > 0 && onlyPotentials && potentials.length === 0 && (
        <div className="empty">
          Ningún potencial ahora mismo: no hay tokens subiendo con fuerza (≥×{HOT_GROWTH}) de
          creadores no-spam. Quita el filtro para ver todo.
        </div>
      )}


      {view === 'series' && (
        <section className="movers">
          <h2>🔁 Series</h2>
          <p className="mono">
            El mismo símbolo relanzado una y otra vez, <strong>donde algún miembro anterior llegó
            a GRADUAR</strong> ({SERIES_PUMP_CAP_SOL} ◎, ~69k $), y con al menos{' '}
            {SERIES_MIN_CADENCE_MIN} minutos entre iteraciones.
            <br />
            Los dos filtros son deliberados. El de capitalización quita los nombres que nunca
            movieron dinero. El de cadencia quita las <strong>colisiones</strong>: se crean ~1.400
            tokens por hora, así que los nombres populares se repiten solos —se vieron Doge cada
            13 s y TIMMY cada 63 s—. Nadie relanza el mismo símbolo a propósito cada trece
            segundos.
            <br />
            <strong>Se observó con $TNOS</strong>: ocho lanzamientos en ocho días sin llegar a
            8.000 $, y de pronto cuatro en siete horas a 253M, 118M, 72,9M y 43,4M — uno cada ~45
            minutos, <strong>cada uno la mitad que el anterior</strong>. La columna «picos» es
            donde se ve si la serie se está agotando.
            <br />
            <strong>Esto NO es una señal de compra.</strong> Una serie que se parte por la mitad
            en cada iteración indica que el dinero de fuera se está acabando, no que venga más.
          </p>
          <div className="notify-row">
            <button
              className="badge potential-tab"
              onClick={() => {
                if (typeof Notification !== 'undefined') void Notification.requestPermission();
              }}
            >
              🔔 activar avisos
            </button>
            <label className="mono" style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
              <input
                type="checkbox"
                checked={soloSeries}
                onChange={(e) => setSoloSeries(e.target.checked)}
              />
              solo avisos de series
            </label>
            <span className="mono">
              {soloSeries
                ? 'Las estampidas ya no avisan. Solo sonará cuando aparezca una serie.'
                : 'Avisa de series Y de estampidas. Marca la casilla para oír solo las series.'}
            </span>
          </div>
          {seriesList.length === 0 ? (
            <div className="empty">
              Ninguna serie viva ahora mismo, y es lo esperable: hacen falta{' '}
              {SERIES_MIN_MEMBERS} tokens con el mismo símbolo en {SERIES_WINDOW_HOURS}h,
              separados al menos {SERIES_MIN_CADENCE_MIN} minutos, y que alguno haya graduado
              —algo que solo consigue el 0,066% de los tokens—. Que esté vacía casi siempre es
              la señal de que el filtro funciona.
            </div>
          ) : (
            <table className="zone-table">
              <thead>
                <tr>
                  <th>Símbolo</th>
                  <th title="Cuántos tokens con este símbolo han salido en la ventana, y cuántos de ellos bombearon.">Lanzamientos</th>
                  <th title="Mediana del tiempo entre iteraciones. Es el «cada cuánto sale uno».">Cadencia</th>
                  <th title="Pico de cada miembro, del más viejo al más nuevo. Si va bajando, la serie se agota.">Picos</th>
                  <th title="El mejor pico que ha conseguido cualquier miembro de la serie.">Mejor</th>
                  <th title="Hace cuánto salió la iteración más reciente.">Último</th>
                  <th>Cap ahora</th>
                  <th>Ver</th>
                </tr>
              </thead>
              <tbody>
                {seriesList.map((s) => (
                  <tr key={s.key} className={s.decaying ? undefined : 'hot'}>
                    <td>
                      <span className={s.decaying ? 'symbol' : 'symbol hot-name'}>
                        {s.symbol}
                      </span>
                      {s.decaying ? (
                        <div className="warn" title="El último no llegó ni a la mitad del mejor pico de la serie: el dinero de fuera se está agotando.">
                          ↓ agotándose
                        </div>
                      ) : null}
                    </td>
                    <td className="mono">
                      {s.members}
                      <span className="good"> · {s.pumped} bombearon</span>
                    </td>
                    <td className="mono">
                      {s.cadence_seconds === null
                        ? '—'
                        : s.cadence_seconds < 90
                          ? `cada ${Math.round(s.cadence_seconds)} s`
                          : s.cadence_seconds < 3600
                            ? `cada ${Math.round(s.cadence_seconds / 60)} min`
                            : `cada ${(s.cadence_seconds / 3600).toFixed(1)} h`}
                    </td>
                    <td className="mono" title="Del más viejo al más nuevo.">
                      {s.peaks_sol.map((p, i) => (
                        <span key={i}>
                          {i > 0 ? ' → ' : ''}
                          <span
                            className={p >= SERIES_PUMP_CAP_SOL ? 'good' : 'pending'}
                            title={p <= 0 ? 'Aún sin operaciones medidas: acaba de nacer.' : formatCap(p)}
                          >
                            {p <= 0 ? 'sin datos' : formatMoney(p, price)}
                          </span>
                        </span>
                      ))}
                    </td>
                    <td className="mono hot-name" title={formatCap(s.best_peak_sol)}>
                      {formatMoney(s.best_peak_sol, price)}
                    </td>
                    <td className="mono">
                      {s.latest_age_seconds < 3600
                        ? `hace ${Math.round(s.latest_age_seconds / 60)} min`
                        : `hace ${(s.latest_age_seconds / 3600).toFixed(1)} h`}
                    </td>
                    <td className="mono" title={formatCap(s.latest_cap_sol)}>
                      {formatMoney(s.latest_cap_sol, price)}
                    </td>
                    <td className="mono">
                      <a
                        className="analyze-link"
                        href={`https://pump.fun/${s.latest_mint}`}
                        target="_blank"
                        rel="noreferrer"
                      >
                        pump.fun →
                      </a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}

      {view === 'graduando' && (
        <section className="movers">
          <h2>🎓 Camino a la graduación</h2>
          <p className="mono">
            Un token <strong>gradúa</strong> cuando agota los 793.100.000.000.000 tokens que su
            curva pone a la venta — hacen falta ~85 SOL de compras. Al completarse, la curva se
            cierra y el token pasa a operar en <strong>PumpSwap</strong>. Lo consigue en torno
            al <strong>1-3%</strong>: es el suceso más raro que mide este sistema.
            <br />
            El progreso sale de las reservas de cada operación, sin coste de RPC. <strong>La ETA
            no es una predicción</strong>: es cuánto tardaría <em>si mantuviera este ritmo</em>,
            y el ritmo cambia constantemente.
            <br />
            <strong>La columna «tras graduar» es el tramo que la curva no puede ver.</strong> Al
            graduar dejamos de recibir operaciones de curva, así que el seguimiento continúa
            suscribiéndose al token en PumpSwap. Todo lo que supere los ~69k $ ocurre ahí. Muchos
            graduados no vuelven a operar nunca y se quedan en «escuchando…»: eso también es un
            dato.
          </p>
          {graduating.length === 0 ? (
            <div className="empty">
              Ningún token ha pasado del 35% del recorrido todavía. Es lo normal: la mayoría
              muere muy por debajo.
            </div>
          ) : (
            <table className="zone-table">
              <thead>
                <tr>
                  <th>Token</th>
                  <th title="Fracción del recorrido de la curva ya completada">Progreso</th>
                  <th title="Puntos de progreso por minuto desde que entró en vigilancia">Ritmo</th>
                  <th title="SOL de compras que faltan para completar la curva">Faltan</th>
                  <th title="Cuánto tardaría manteniendo el ritmo actual. NO es una predicción.">ETA</th>
                  <th title="Capitalización en la curva. Al graduar se congela: la curva deja de emitir operaciones.">Cap (curva)</th>
                  <th title="Vida DESPUÉS de graduar, leída en PumpSwap. Es el tramo que la curva no puede ver y donde ocurre todo lo que pase de ~69k $.">Tras graduar</th>
                  <th>Simular</th>
                  <th>Analizar</th>
                </tr>
              </thead>
              <tbody>
                {graduating.map((g) => {
                  const pct = g.progress * 100;
                  const cerca = pct >= 75;
                  return (
                    <tr key={g.mint} className={g.graduated || cerca ? 'hot' : undefined}>
                      <td>
                        <a
                          className={cerca ? 'symbol hot-name token-link' : 'symbol token-link'}
                          href={`https://pump.fun/${g.mint}`}
                          target="_blank"
                          rel="noreferrer"
                        >
                          {g.symbol || short(g.mint)}
                        </a>
                        {g.graduated ? <span className="good"> 🎓 graduó</span> : null}
                        <div className="mono">{g.name}</div>
                      </td>
                      <td className="mono">
                        <div className={cerca ? 'hot-name' : undefined}>{pct.toFixed(1)}%</div>
                        <div className="grad-bar">
                          <span
                            style={{ width: `${Math.min(100, pct)}%` }}
                            className={g.graduated ? 'done' : cerca ? 'near' : ''}
                          />
                        </div>
                      </td>
                      <td className="mono">
                        {g.progress_per_min > 0.001 ? (
                          <span className="good">+{(g.progress_per_min * 100).toFixed(1)} pts</span>
                        ) : (
                          <span className="pending">parado</span>
                        )}
                      </td>
                      <td className="mono">
                        {g.graduated ? '—' : `${g.sol_to_graduate.toFixed(1)} ◎`}
                      </td>
                      <td className="mono">
                        {g.graduated ? (
                          <span className="good">completada</span>
                        ) : g.eta_minutes === null ? (
                          <span className="pending">sin ritmo</span>
                        ) : (
                          <span className={g.eta_minutes <= 10 ? 'hot-name' : undefined}>
                            ~{g.eta_minutes.toFixed(0)} min
                          </span>
                        )}
                      </td>
                      <td className="mono" title={formatCap(g.market_cap_sol)}>
                        {formatMoney(g.market_cap_sol, price)}
                      </td>
                      <td className="mono">
                        {!g.graduated ? (
                          <span className="pending">—</span>
                        ) : g.swap_market_cap_sol ? (
                          <>
                            <span
                              className={
                                (g.swap_peak_sol ?? 0) >= BIG_CAP_SOL ? 'hot-name' : 'good'
                              }
                              title={`Capitalización actual en PumpSwap: ${formatCap(g.swap_market_cap_sol)}. Máximo alcanzado allí: ${formatCap(g.swap_peak_sol ?? null)}. Sobre ${g.swap_trades ?? 0} operaciones decodificadas.`}
                            >
                              {formatMoney(g.swap_market_cap_sol, price)}
                            </span>
                            <div className="mono">
                              {g.swap_trades ?? 0} tx
                              {g.swap_ratio ? (
                                <span
                                  title={
                                    'Cociente entre lo que marca PumpSwap y la última lectura de la curva. ' +
                                    'AÚN SIN RESOLVER: en las dos primeras medidas salió 0,31 y 0,34. Si en todos ' +
                                    'los tokens se agrupa en el mismo valor, el salto es de referencia entre los dos ' +
                                    'sitios y no una caída real; si sale disperso, es mercado. Por eso esta cifra NO ' +
                                    'se mezcla con la de la curva en ninguna estadística.'
                                  }
                                >
                                  {' '}
                                  · ×{g.swap_ratio.toFixed(2)} vs curva
                                </span>
                              ) : null}
                            </div>
                          </>
                        ) : g.swap_watched ? (
                          <span
                            className="pending"
                            title="Suscrito a sus operaciones en PumpSwap, pero aún no ha llegado ninguna. Es lo habitual justo tras graduar: el pool tarda en crearse, y muchos tokens no vuelven a operar nunca."
                          >
                            escuchando…
                          </span>
                        ) : (
                          <span className="pending">sin seguimiento</span>
                        )}
                      </td>
                      <td className="mono">
                        {positions.some((p) => p.mint === g.mint && p.closedCap === undefined) ? (
                          <span className="good">● activa</span>
                        ) : (
                          <button
                            className="activate-btn"
                            onClick={() => activate(g.mint, g.symbol, g.market_cap_sol)}
                            disabled={g.graduated || !g.market_cap_sol}
                          >
                            activar
                          </button>
                        )}
                      </td>
                      <td className="mono">
                        <a className="analyze-link" href={`/prevision?mint=${g.mint}`}>
                          analizar →
                        </a>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </section>
      )}

      {view === 'subidas' && (
        <section className="movers">
          <h2>↑ Solo subidas</h2>
          <p className="mono">
            Solo tokens en subida <strong>constante</strong>, medida en los tres ejes: la{' '}
            <strong>capitalización</strong> (al menos {(MIN_UP_FRACTION * 100).toFixed(0)}% de
            movimientos al alza, ningún bajón mayor del {(MAX_SINGLE_DIP * 100).toFixed(0)}% y
            cotizando en su máximo), las <strong>operaciones</strong> (ninguna cartera moviendo más
            del {(MAX_TOP_WALLET_SHARE * 100).toFixed(0)}%) y las <strong>compras</strong> (más
            compras que ventas). Además deben subir deprisa: al menos &times;
            {FAST_CLIMB_PER_MIN}/min con el barrendero encendido.{' '}
            <strong>El motor está concentrado en esta vista</strong>: deja de pedir los rankings de
            las otras pestañas y refresca cada 1,2 s.
            <br />
            <strong>Sobre la previsión.</strong> «Techo obj.» y «gradúa %» salen de medir el corpus:
            de los tokens que llegaron al menos hasta donde está este, hasta dónde llegaron y qué
            fracción graduó. Es lo que hicieron otros, no lo que hará este.{' '}
            <strong>«→100k» sale casi siempre 0% y hay que entender por qué</strong>: la curva de
            pump.fun gradúa sobre {GRAD_CAP_SOL} ◎ (~69k $) y ahí el token se marcha a PumpSwap,
            donde dejamos de ver su capitalización. En 2.000 registros el máximo observado son
            411 ◎. Ese 0% significa «no lo medimos por encima de la graduación», no «es imposible».
          </p>
          <div className="two-cols">
            {/* IZQUIERDA · los que se cayeron de la lista */}
            <div>
              <h3 className="col-head">
                ✕ Se cayeron de la lista <span className="mono">({dropped.length})</span>
              </h3>
              <p className="mono col-note">
                Estuvieron aquí y dejaron de subir. Es la proporción real: casi todos acaban
                en esta columna.
              </p>
              {dropped.length === 0 ? (
                <div className="empty">Todavía no se ha caído ninguno.</div>
              ) : (
                <table className="zone-table">
                  <thead>
                    <tr>
                      <th>Token</th>
                      <th title="El máximo que llegó a alcanzar">Llegó a</th>
                      <th>Motivo</th>
                    </tr>
                  </thead>
                  <tbody>
                    {dropped.map((d) => (
                      <tr key={`${d.mint}-${d.at}`}>
                        <td>
                          <a
                            className="symbol token-link"
                            href={`https://pump.fun/${d.mint}`}
                            target="_blank"
                            rel="noreferrer"
                          >
                            {d.symbol || short(d.mint)}
                          </a>
                          <div className="mono">{d.name}</div>
                        </td>
                        <td className="mono" title={formatCap(d.peakCap)}>
                          {formatMoney(d.peakCap, price)}
                          <div className="mono">&times;{d.maxGrowth.toFixed(2)}</div>
                        </td>
                        <td className="mono bad">{d.reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>

            {/* DERECHA · los que siguen subiendo */}
            <div>
              <h3 className="col-head good">
                ↑ Siguen subiendo <span className="mono">({rising.length})</span>
              </h3>
              <p className="mono col-note">
                Subida <strong>constante</strong>, no un tick al alza: al menos{' '}
                {(MIN_UP_FRACTION * 100).toFixed(0)}% de movimientos hacia arriba, sin ningún bajón
                mayor del {(MAX_SINGLE_DIP * 100).toFixed(0)}%, en su máximo, y —cuando hay dato—
                más compras que ventas y sin una cartera dominando. Los que llevan más de{' '}
                {SUSTAINED_RISE_SECONDS}s subiendo salen primero, marcados como{' '}
                <strong>⬆ sostenido</strong>.
              </p>
          {rising.length === 0 ? (
            <div className="empty">
              Ningún token en subida constante ahora mismo. Es lo normal, y con este filtro más
              todavía: la inmensa mayoría sube a trompicones o ya ha retrocedido.
            </div>
          ) : (
            <table className="zone-table">
              <thead>
                <tr>
                  <th>Token</th>
                  <th title="Forma de la línea de precio en los últimos segundos, medida comparando el último tramo contra el anterior.">Línea</th>
                  <th>Cap</th>
                  <th>Crece</th>
                  <th title="Techo EMPÍRICO condicionado a dónde está AHORA: de los tokens del corpus que llegaron al menos hasta aquí, hasta dónde llegaron. Mediana y p75. NO es una predicción.">Techo obj.</th>
                  <th title="Previsión medida sobre esa misma población: qué fracción llegó a graduar (~380 ◎) y qué fracción a la zona de 100k. La curva deja de medir al graduar, por eso la de 100k sale ~0.">Previsión</th>
                  <th title="Segundos que lleva la subida EN CURSO. Se reinicia si el token cae más de un 10% desde su máximo.">Subiendo</th>
                  <th title="Caída desde su máximo. 0% = está en máximos ahora mismo.">Desde pico</th>
                  <th title="Cuánto crece por minuto desde que lo detectamos. Es la velocidad real de la subida.">Velocidad</th>
                  <th title="Cuántas veces más tiene que multiplicarse para tocar la zona grande (600 ◎). NO es una predicción: es la distancia que le falta.">Falta a 600 ◎</th>
                  <th title="Ritmo reciente frente al anterior. Solo disponible en estampidas.">Ritmo</th>
                  <th title="Carteras distintas operando y operaciones del lanzamiento. Solo en estampidas.">Manos / tx</th>
                  <th>Simular</th>
                  <th>Analizar</th>
                </tr>
              </thead>
              <tbody>
                {rising.map((t) => {
                  const tend = trendOf(t.serie);
                  const sostenido = t.risingSeconds >= SUSTAINED_RISE_SECONDS;
                  return (
                  <tr key={t.mint} className={sostenido ? 'hot sostenido' : 'hot'}>
                    <td>
                      <a
                        className="symbol hot-name token-link"
                        href={`https://pump.fun/${t.mint}`}
                        target="_blank"
                        rel="noreferrer"
                      >
                        {t.symbol || short(t.mint)}
                      </a>
                      <div className="mono">{t.name}</div>
                    </td>
                    <td className="mono">
                      <Spark serie={t.serie} cls={tend.cls} />
                      <div className={tend.cls}>
                        {tend.arrow} {tend.label}
                      </div>
                    </td>
                    <td className="mono" title={formatCap(t.cap)}>{formatMoney(t.cap, price)}</td>
                    <td className="mono hot-name">&times;{t.growth.toFixed(2)}</td>
                    <td className="mono">
                      {t.targetCap ? (
                        <>
                          <span title={`Mediana de ${t.targetSample ?? '?'} casos que llegaron al menos hasta donde está este. ${formatCap(t.targetCap)}`}>
                            {formatMoney(t.targetCap, price)}
                          </span>
                          {t.targetCapHigh ? (
                            <div className="mono" title={`p75: uno de cada cuatro pasó de aquí. ${formatCap(t.targetCapHigh)}`}>
                              alto {formatMoney(t.targetCapHigh, price)}
                            </div>
                          ) : null}
                        </>
                      ) : (
                        <span
                          className="pending"
                          title="Menos de 8 casos comparables en el corpus: no se inventa una cifra. Ocurre con tokens aún muy pequeños, para los que no hay población de referencia."
                        >
                          —
                        </span>
                      )}
                    </td>
                    <td className="mono">
                      {t.probGrad === null || t.probGrad === undefined ? (
                        <span className="pending" title="Sin población comparable todavía.">—</span>
                      ) : (
                        <>
                          <span
                            className={t.probGrad >= 0.3 ? 'good' : undefined}
                            title={`De ${t.targetSample ?? '?'} tokens que llegaron hasta aquí, esta fracción acabó graduando (~${GRAD_CAP_SOL} ◎). Es lo que pasó, no lo que hará este.`}
                          >
                            gradúa {(t.probGrad * 100).toFixed(0)}%
                          </span>
                          <div
                            className={t.prob100k ? 'good' : 'pending'}
                            title={`Fracción que llegó a ${BIG_CAP_SOL} ◎ (~100k). Sale ~0 porque la curva GRADÚA sobre ${GRAD_CAP_SOL} ◎: a partir de ahí el token se va a PumpSwap y su capitalización deja de verse desde aquí. No es que sea imposible; es que no lo medimos.`}
                          >
                            →100k {((t.prob100k ?? 0) * 100).toFixed(1)}%
                          </div>
                        </>
                      )}
                    </td>
                    <td className="mono">
                      {t.risingSeconds > 0 ? (
                        <>
                          <span className={sostenido ? 'hot-name' : undefined}>
                            {Math.round(t.risingSeconds)}s
                          </span>
                          {sostenido ? (
                            <div
                              className="good"
                              title={`Lleva más de ${SUSTAINED_RISE_SECONDS}s subiendo sin caer un 10%. Es menos común que un pico de velocidad, pero tampoco predice nada.`}
                            >
                              ⬆ sostenido
                            </div>
                          ) : null}
                        </>
                      ) : (
                        '—'
                      )}
                    </td>
                    <td className="mono">
                      {t.drawdownPct > 0 ? (
                        <span className="warn">&minus;{t.drawdownPct.toFixed(1)}%</span>
                      ) : (
                        <span className="good">en máximos</span>
                      )}
                    </td>
                    <td className="mono">
                      <span className={t.climbPerMin >= 2 ? 'hot-name' : 'good'}>
                        &times;{t.climbPerMin.toFixed(2)}/min
                      </span>
                      <span className="mono"> ({Math.round(t.ageSeconds)}s)</span>
                    </td>
                    <td className="mono">
                      {Number.isFinite(t.toBigCapMultiple) ? (
                        <span className={t.toBigCapMultiple <= 3 ? 'good' : undefined}>
                          &times;{t.toBigCapMultiple.toFixed(1)} más
                        </span>
                      ) : (
                        '—'
                      )}
                    </td>
                    <td className="mono">
                      {t.momentum === undefined ? (
                        '—'
                      ) : (
                        <span className={t.momentum >= 1 ? 'good' : 'bad'}>
                          {t.momentum >= 1 ? '↑' : '↓'} {t.momentum.toFixed(2)}
                        </span>
                      )}
                    </td>
                    <td className="mono">
                      {t.uniqueWallets ?? '—'}
                      {t.launchTrades !== undefined ? (
                        <span className="mono"> / {t.launchTrades}tx</span>
                      ) : null}
                    </td>
                    <td className="mono">
                      {positions.some((p) => p.mint === t.mint && p.closedCap === undefined) ? (
                        <span className="good">● activa</span>
                      ) : (
                        <button
                          className="activate-btn"
                          onClick={() => activate(t.mint, t.symbol, t.cap)}
                        >
                          activar
                        </button>
                      )}
                    </td>
                    <td className="mono">
                      <a className="analyze-link" href={`/prevision?mint=${t.mint}`}>
                        analizar →
                      </a>
                    </td>
                  </tr>
                  );
                })}
              </tbody>
            </table>
          )}
            </div>
          </div>
        </section>
      )}

      {view === 'estampida' && (
        <section className="movers">
          <h2>🌊 Lanzamientos en estampida</h2>
          <p className="mono">
            Tokens con <strong>≥100 operaciones en sus primeros 15 segundos</strong> — el patrón
            de V713/VanillaFunk, que hizo 281 en 10 s y graduó. Umbral medido, no inventado: la
            mediana de un lanzamiento normal es <strong>8</strong> operaciones y solo 1 de cada
            26 supera las 100. Solo entran tokens cuyo nacimiento hemos visto en directo.
          </p>
          <div className="notify-row">
            <button
              className="badge potential-tab"
              onClick={() => {
                if (typeof Notification !== 'undefined') void Notification.requestPermission();
              }}
            >
              🔔 activar avisos
            </button>
            <span className="mono">
              Aviso del navegador cuando una estampida supere {NOTIFY_CAP_SOL} ◎
              {toEur(NOTIFY_CAP_SOL, price) ? ` (${toEur(NOTIFY_CAP_SOL, price)})` : ''}.
            </span>
          </div>
          {stampede.length === 0 ? (
            <div className="empty">
              Ninguna estampida detectada todavía. Es un patrón raro: ~4% de los lanzamientos.
            </div>
          ) : (
            <table className="zone-table">
              <thead>
                <tr>
                  <th>Token</th>
                  <th title="Operaciones en los primeros 15 segundos de vida">Ráfaga</th>
                  <th title="Estado AHORA, medido con la mediana de las últimas lecturas: un pico de ventas aislado no cuenta, una caída sostenida sí.">Estado</th>
                  <th title="Carteras distintas operando. Muchas operaciones por cartera = pocas manos girando volumen.">Manos</th>
                  <th title="Cuota de volumen de la cartera más grande. Alta = un solo actor domina.">Concentr.</th>
                  <th title="Ritmo reciente frente al anterior. >1 acelera, <1 se apaga.">Ritmo</th>
                  <th title="Forma de la subida hasta su máximo: subir de golpe con retrocesos breves es buena señal; hundirse a mitad de camino es mala. Heurística sin validar.">Subida</th>
                  <th title="Abre una posición SIMULADA con el importe de arriba. No envía ninguna orden.">Simular</th>
                  <th title="Techo EMPÍRICO: mediana y p75 de lo que alcanzaron estampidas anteriores, aplicado a su capitalización de partida. No es una predicción.">Techo aprox.</th>
                  <th>Cap</th>
                  <th title="Caída sostenida desde su máximo">Desde pico</th>
                  <th>Crece</th>
                  <th>Creador</th>
                  <th>Analizar</th>
                </tr>
              </thead>
              <tbody>
                {stampede.map((t, i) => {
                  const dying = t.state === 'enfriando' || t.state === 'cayendo';
                  // Separador: los que nunca tocaron la zona grande (600 ◎) quedan debajo.
                  const previousBig = i > 0 ? !!stampede[i - 1]!.reached_big_cap : true;
                  const showSplit = !t.reached_big_cap && previousBig;
                  return (
                  <Fragment key={t.mint}>
                  {showSplit && (
                    <tr className="tier-row">
                      <td colSpan={13} className="tier-bajo">
                        · No han llegado a la zona grande (~{BIG_CAP_SOL} ◎ )
                      </td>
                    </tr>
                  )}
                  <tr className={dying ? undefined : 'hot'}>
                    <td>
                      <a
                        className={dying ? 'symbol token-link' : 'symbol hot-name token-link'}
                        href={`https://pump.fun/${t.mint}`}
                        target="_blank"
                        rel="noreferrer"
                      >
                        {t.symbol || short(t.mint)}
                      </a>
                      <div className="mono">{t.name}</div>
                    </td>
                    <td className="mono">{t.launch_trades} tx / {t.window_seconds}s</td>
                    <td className="mono">
                      <span className={`state-${t.state}`}>{STATE_LABEL[t.state]}</span>
                    </td>
                    <td className="mono">
                      {t.unique_wallets === undefined ? (
                        '—'
                      ) : (
                        <>
                          {t.unique_wallets}
                          {(() => {
                            const v = crowdVerdict(t);
                            return v ? (
                              <span className={v.cls}>
                                {' '}· {v.label} ({t.trades_per_wallet?.toFixed(1)}×)
                              </span>
                            ) : null;
                          })()}
                        </>
                      )}
                    </td>
                    <td className="mono">
                      {t.top_wallet_share === undefined ? (
                        '—'
                      ) : (
                        <span className={t.top_wallet_share >= 0.4 ? 'bad' : undefined}>
                          {(t.top_wallet_share * 100).toFixed(0)}%
                        </span>
                      )}
                    </td>
                    <td className="mono">
                      {t.momentum === undefined ? (
                        '—'
                      ) : (
                        <span className={t.momentum >= 1 ? 'good' : 'bad'}>
                          {t.momentum >= 1 ? '↑' : '↓'} {t.momentum.toFixed(2)}
                        </span>
                      )}
                    </td>
                    <td className="mono">
                      {(() => {
                        const v = climbVerdict(t);
                        if (!v) return '—';
                        return (
                          <span className={v.cls} title={v.detail}>
                            {v.label}
                            <span className="mono"> (−{((t.max_dip ?? 0) * 100).toFixed(0)}%)</span>
                          </span>
                        );
                      })()}
                    </td>
                    <td className="mono">
                      {positions.some((p) => p.mint === t.mint && p.closedCap === undefined) ? (
                        <span className="good">● activa</span>
                      ) : (
                        <button className="activate-btn" onClick={() => activate(t.mint, t.symbol, t.market_cap_sol)}>
                          activar
                        </button>
                      )}
                    </td>
                    <td className="mono">
                      {t.ceiling_sol === null || t.ceiling_sol === undefined ? (
                        <span
                          className="pending"
                          title={`Muestra insuficiente (${t.ceiling_sample ?? 0} estampidas resueltas). Hacen falta al menos 8 para no inventar una cifra.`}
                        >
                          —
                        </span>
                      ) : (
                        <span title={`Mediana y p75 de ${t.ceiling_sample} casos anteriores. Lo que alcanzaron otros, NO lo que hará este.`}>
                          {formatMoney(t.ceiling_sol, price)}
                          {t.ceiling_high_sol ? (
                            <span className="mono"> · alto {formatMoney(t.ceiling_high_sol, price)}</span>
                          ) : null}
                        </span>
                      )}
                    </td>
                    <td className="mono" title={formatCap(t.market_cap_sol)}>{formatMoney(t.market_cap_sol, price)}</td>
                    <td className="mono">
                      {t.drawdown_pct > 0 ? (
                        <span className={dying ? 'bad' : undefined}>
                          −{t.drawdown_pct.toFixed(0)}%
                          <span className="mono"> (pico {formatMoney(t.peak_market_cap_sol, price)})</span>
                        </span>
                      ) : (
                        '—'
                      )}
                    </td>
                    <td className="mono">×{t.growth.toFixed(2)}</td>
                    <td className="mono">{short(t.creator)}</td>
                    <td className="mono">
                      <a className="analyze-link" href={`/prevision?mint=${t.mint}`}>
                        analizar →
                      </a>
                    </td>
                  </tr>
                  </Fragment>
                  );
                })}
              </tbody>
            </table>
          )}
        </section>
      )}

      {view === 'zona' && (
        <section className="movers">
          <h2>⚡ Zona 170-360 ◎ · a un paso de explotar</h2>
          <p className="mono">
            Tokens que están AHORA en esa banda. El % es la tasa base REAL medida: de los que
            pasaron por esta banda, cuántos siguieron hasta la zona de graduación (~$69k). Es lo
            que ya pasó, NO una predicción sobre este token.
          </p>
          {hotZone.length === 0 ? (
            <div className="empty">
              Ningún token en la banda 170-360 ◎ ahora mismo. Es una zona rara: la mayoría muere
              mucho antes de llegar.
            </div>
          ) : (
            <table className="zone-table">
              <thead>
                <tr>
                  <th>Token</th>
                  <th>Cap</th>
                  <th>Crece</th>
                  <th title="Tasa base real de esta banda">Explotan</th>
                  <th>Muestra</th>
                  <th>Analizar</th>
                </tr>
              </thead>
              <tbody>
                {hotZone.map((t) => (
                  <tr key={t.mint} className="hot">
                    <td>
                      <a
                        className="symbol hot-name token-link"
                        href={`https://pump.fun/${t.mint}`}
                        target="_blank"
                        rel="noreferrer"
                      >
                        {t.symbol || short(t.mint)}
                      </a>
                      <div className="mono">{t.name}</div>
                    </td>
                    <td className="mono" title={formatCap(t.market_cap_sol)}>{formatMoney(t.market_cap_sol, price)}</td>
                    <td className="mono hot-name">×{t.growth.toFixed(2)}</td>
                    <td className="mono">
                      {t.explode_prob === null ? (
                        <span className="pending" title="Muestra insuficiente en esta banda.">
                          —
                        </span>
                      ) : (
                        <span className={t.explode_prob >= 0.2 ? 'good' : undefined}>
                          {(t.explode_prob * 100).toFixed(0)}%
                        </span>
                      )}
                    </td>
                    <td className="mono">{t.explode_sample}</td>
                    <td className="mono">
                      <a className="analyze-link" href={`/prevision?mint=${t.mint}`}>
                        analizar →
                      </a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}

      {onlyPotentials && (
        <section className="movers">
          <h2>🚀 Los que más han explotado (esta sesión)</h2>
          <p className="mono">
            Referencia de entrenamiento: cada token que supera ×{3} se graba como ejemplo
            etiquetado del corpus con el que aprenderá el detector. No es una predicción.
          </p>
          {topMovers.length === 0 ? (
            <div className="empty">Aún sin explosiones registradas esta sesión.</div>
          ) : (
            <ol className="movers-list">
              {topMovers.map((m, i) => (
                <li key={m.mint}>
                  <span className="movers-rank">#{i + 1}</span>
                  <a
                    className="symbol token-link"
                    href={`https://pump.fun/${m.mint}`}
                    target="_blank"
                    rel="noreferrer"
                  >
                    {m.symbol || short(m.mint)}
                  </a>
                  <span className="hot-name movers-growth">×{m.growth.toFixed(2)}</span>
                  <span className="mono">{formatMoney(m.peak_market_cap_sol, price)} pico</span>
                  <a className="analyze-link" href={`/prevision?mint=${m.mint}`}>
                    analizar →
                  </a>
                </li>
              ))}
            </ol>
          )}
        </section>
      )}

      <footer>
        Los veredictos son análisis, no recomendaciones. Esta interfaz no puede abrir ni
        cerrar ninguna posición, y el sistema no tiene el trading real habilitado.
        Pasa el ratón por el riesgo para ver las razones concretas.
      </footer>
    </div>
  );
}
