'use client';

import { useState } from 'react';

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

type Snapshot = {
  price_sol: number;
  market_cap_sol: number;
  liquidity_sol: number;
  sol_to_graduate: number;
  graduation_market_cap_sol: number;
  progress_pct: number;
  price_impact_bps: Record<string, number>;
};

type ProjectionPoint = { seconds_ahead: number; percentile: number; price_sol: number };

type Detail = {
  found: boolean;
  mint: string;
  detail?: string;
  symbol?: string;
  name?: string;
  creator?: string;
  snapshot?: Snapshot;
  history?: number[];
  projection?: ProjectionPoint[];
  disclaimer?: string;
};

const W = 460;
const H = 260;
const PAD = 40;

function scale(value: number, min: number, max: number, from: number, to: number): number {
  if (max === min) return (from + to) / 2;
  return from + ((value - min) / (max - min)) * (to - from);
}

/** Cono de percentiles. Ancho = incertidumbre, y eso es informacion, no un defecto. */
function ProjectionChart({ points }: { points: ProjectionPoint[] }) {
  if (points.length === 0) return <p className="empty">Sin datos para proyectar.</p>;

  const prices = points.map((p) => p.price_sol);
  const min = Math.min(...prices);
  const max = Math.max(...prices);
  const maxT = Math.max(...points.map((p) => p.seconds_ahead));

  const band = (low: number, high: number, fill: string) => {
    const lows = points.filter((p) => p.percentile === low).sort((a, b) => a.seconds_ahead - b.seconds_ahead);
    const highs = points.filter((p) => p.percentile === high).sort((a, b) => b.seconds_ahead - a.seconds_ahead);
    const path = [...lows, ...highs]
      .map((p, i) => {
        const x = scale(p.seconds_ahead, 0, maxT, PAD, W - PAD);
        const y = scale(p.price_sol, min, max, H - PAD, PAD);
        return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(' ');
    return <path d={`${path} Z`} fill={fill} stroke="none" />;
  };

  const median = points
    .filter((p) => p.percentile === 0.5)
    .sort((a, b) => a.seconds_ahead - b.seconds_ahead)
    .map((p, i) => {
      const x = scale(p.seconds_ahead, 0, maxT, PAD, W - PAD);
      const y = scale(p.price_sol, min, max, H - PAD, PAD);
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="chart" role="img" aria-label="Cono de proyección">
      {band(0.1, 0.9, 'rgba(78,161,255,0.12)')}
      {band(0.25, 0.75, 'rgba(78,161,255,0.25)')}
      <path d={median} fill="none" stroke="var(--accent)" strokeWidth="2" strokeDasharray="4 3" />
      <line x1={PAD} y1={H - PAD} x2={W - PAD} y2={H - PAD} stroke="var(--border)" />
      <line x1={PAD} y1={PAD} x2={PAD} y2={H - PAD} stroke="var(--border)" />
      <text x={PAD} y={H - 14} className="axis">ahora</text>
      <text x={W - PAD - 24} y={H - 14} className="axis">+{maxT}s</text>
      <text x={6} y={PAD + 4} className="axis">{max.toExponential(2)}</text>
      <text x={6} y={H - PAD} className="axis">{min.toExponential(2)}</text>
    </svg>
  );
}

/** Precio real observado. Sin suavizar: se muestra lo que paso. */
function HistoryChart({ prices }: { prices: number[] }) {
  if (prices.length < 2) {
    return <p className="empty">Aún no hay suficientes precios observados de este token.</p>;
  }
  const min = Math.min(...prices);
  const max = Math.max(...prices);
  const path = prices
    .map((price, i) => {
      const x = scale(i, 0, prices.length - 1, PAD, W - PAD);
      const y = scale(price, min, max, H - PAD, PAD);
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="chart" role="img" aria-label="Precio observado">
      <path d={path} fill="none" stroke="var(--ok)" strokeWidth="2" />
      <line x1={PAD} y1={H - PAD} x2={W - PAD} y2={H - PAD} stroke="var(--border)" />
      <line x1={PAD} y1={PAD} x2={PAD} y2={H - PAD} stroke="var(--border)" />
      <text x={PAD} y={H - 14} className="axis">{prices.length} obs.</text>
      <text x={6} y={PAD + 4} className="axis">{max.toExponential(2)}</text>
      <text x={6} y={H - PAD} className="axis">{min.toExponential(2)}</text>
    </svg>
  );
}

export default function PrevisionPage() {
  const [reference, setReference] = useState('');
  const [detail, setDetail] = useState<Detail | null>(null);
  const [loading, setLoading] = useState(false);

  const lookup = async () => {
    const value = reference.trim();
    if (!value) return;
    setLoading(true);
    try {
      // `noUncheckedIndexedAccess` obliga a tratar el split como posiblemente vacio.
      const withoutQuery = value.split('?')[0] ?? value;
      const cleaned = withoutQuery.replace(/\/$/, '').split('/').pop() ?? value;
      const response = await fetch(`${API}/v1/tokens/${encodeURIComponent(cleaned)}/detail`);
      setDetail((await response.json()) as Detail);
    } catch {
      setDetail({ found: false, mint: value, detail: 'No se pudo consultar la API.' });
    } finally {
      setLoading(false);
    }
  };

  const snapshot = detail?.snapshot;

  return (
    <div className="wrap">
      <header>
        <h1>Previsión de token</h1>
        <span className="badge read-only">solo lectura</span>
        <a className="badge" href="/">← radar</a>
      </header>

      <div className="lookup">
        <input
          value={reference}
          onChange={(event) => setReference(event.target.value)}
          onKeyDown={(event) => event.key === 'Enter' && lookup()}
          placeholder="Pega el enlace de pump.fun o el mint"
          aria-label="Enlace o mint del token"
        />
        <button onClick={lookup} disabled={loading}>
          {loading ? 'consultando…' : 'analizar'}
        </button>
      </div>

      {detail && !detail.found && <p className="empty">{detail.detail}</p>}

      {detail?.found && snapshot && (
        <>
          <h2>
            <span className="symbol">{detail.symbol}</span> {detail.name}
          </h2>

          <div className="stats">
            <div className="stat"><div className="label">Precio</div><div className="value">{snapshot.price_sol.toExponential(3)}</div></div>
            <div className="stat"><div className="label">Market cap</div><div className="value">{snapshot.market_cap_sol.toFixed(2)} SOL</div></div>
            <div className="stat"><div className="label">Liquidez</div><div className="value">{snapshot.liquidity_sol.toFixed(2)} SOL</div></div>
            <div className="stat"><div className="label">Para graduar</div><div className="value">{snapshot.sol_to_graduate.toFixed(2)} SOL</div></div>
            <div className="stat"><div className="label">Progreso</div><div className="value">{snapshot.progress_pct.toFixed(1)}%</div></div>
          </div>

          <div className="charts">
            <section>
              <h3>Proyección +4s</h3>
              <ProjectionChart points={detail.projection ?? []} />
              <p className="mono">
                Banda clara: percentiles 10–90. Banda oscura: 25–75. Línea discontinua: mediana.
              </p>
            </section>
            <section>
              <h3>Precio real observado</h3>
              <HistoryChart prices={detail.history ?? []} />
              <p className="mono">Datos on-chain, sin suavizar.</p>
            </section>
          </div>

          <h3>Coste de entrar (price impact)</h3>
          <table>
            <thead><tr><th>Tamaño</th><th>Impacto</th></tr></thead>
            <tbody>
              {Object.entries(snapshot.price_impact_bps).map(([size, bps]) => (
                <tr key={size}>
                  <td className="mono">{size} SOL</td>
                  <td className="mono">{(bps / 100).toFixed(2)}%</td>
                </tr>
              ))}
            </tbody>
          </table>

          <p className="warn disclaimer">{detail.disclaimer}</p>
        </>
      )}

      <footer>Sin trading. Esta interfaz no puede abrir ni cerrar ninguna posición.</footer>
    </div>
  );
}
