'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';
const REFRESH_MS = 800;

type Candle = {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume_sol: number;
  trades: number;
  projected: boolean;
};

type Snapshot = {
  price_sol_exact: string;
  market_cap_sol_exact: string;
  liquidity_sol_exact: string;
  sol_to_graduate: number;
  graduation_market_cap_sol: number;
  progress_pct: number;
  virtual_sol_reserves: number;
  virtual_token_reserves: number;
  invariant_k: string;
  buys: number;
  sells: number;
  unique_traders: number;
  volume_sol: string;
  price_impact_bps: Record<string, number>;
};

type Signal = { name: string; value: number; weight: number; detail: string };
type Traction = { score: number; label: string; signals: Signal[] };
type Recommendation = { action: string; reason: string; confidence: string };

type Whale = { present: boolean; direction: string; share_of_volume: number; sol_amount: number; detail: string };
type PreBounce = { present: boolean; drop_pct: number; recovery_pct: number; detail: string };
type Flow = Record<string, number | string>;

type Live = {
  mint: string;
  traction?: Traction;
  recommendation?: Recommendation;
  whale?: Whale;
  pre_bounce?: PreBounce;
  flow?: Flow;
  candles: Candle[];
  projected: Candle[];
  trades: number;
  volatility_per_second: number;
  refresh_ms: number;
  error: string | null;
  snapshot?: Snapshot;
};

type Sim = {
  ok: boolean;
  detail?: string;
  runs?: number;
  trades_closed?: number;
  p10_sol?: number;
  median_sol?: number;
  p90_sol?: number;
  worst_sol?: number;
  best_sol?: number;
  losing_runs?: number;
  avg_entry_slippage_bps?: number;
  avg_latency_ms?: number;
  fees_sol?: number;
  stuck_positions?: number;
  failures?: Record<string, number>;
};

const CW = 480;
const CH = 300;
const PAD = 46;

function scaleY(value: number, min: number, max: number): number {
  if (max === min) return CH / 2;
  return CH - PAD - ((value - min) / (max - min)) * (CH - 2 * PAD);
}

/** Velas japonesas. `projected` se dibuja con relleno translucido: es un cono, no un hecho. */
function CandleChart({ candles, title, projected }: { candles: Candle[]; title: string; projected: boolean }) {
  if (candles.length === 0) {
    return (
      <section>
        <h3>{title}</h3>
        <div className="chart empty-chart">Esperando operaciones on-chain…</div>
      </section>
    );
  }
  const highs = candles.map((c) => c.high);
  const lows = candles.map((c) => c.low);
  const min = Math.min(...lows);
  const max = Math.max(...highs);
  const slot = (CW - 2 * PAD) / candles.length;
  const bodyW = Math.max(2, slot * 0.6);

  return (
    <section>
      <h3>{title}</h3>
      <svg viewBox={`0 0 ${CW} ${CH}`} className="chart" role="img" aria-label={title}>
        <line x1={PAD} y1={CH - PAD} x2={CW - PAD} y2={CH - PAD} stroke="var(--border)" />
        <line x1={PAD} y1={PAD} x2={PAD} y2={CH - PAD} stroke="var(--border)" />
        {candles.map((c, i) => {
          const x = PAD + slot * i + slot / 2;
          const up = c.close >= c.open;
          const color = projected ? 'var(--accent)' : up ? 'var(--ok)' : 'var(--down)';
          const bodyTop = scaleY(Math.max(c.open, c.close), min, max);
          const bodyBottom = scaleY(Math.min(c.open, c.close), min, max);
          return (
            <g key={`${c.time}-${i}`}>
              <line x1={x} y1={scaleY(c.high, min, max)} x2={x} y2={scaleY(c.low, min, max)} stroke={color} strokeWidth="1" />
              <rect
                x={x - bodyW / 2}
                y={bodyTop}
                width={bodyW}
                height={Math.max(1, bodyBottom - bodyTop)}
                fill={projected ? 'none' : color}
                stroke={color}
                strokeWidth={projected ? 1.5 : 0}
                strokeDasharray={projected ? '3 2' : undefined}
                opacity={projected ? 0.8 : 1}
              />
            </g>
          );
        })}
        <text x={6} y={PAD + 4} className="axis">{max.toExponential(2)}</text>
        <text x={6} y={CH - PAD} className="axis">{min.toExponential(2)}</text>
      </svg>
    </section>
  );
}

export default function PrevisionPage() {
  const [reference, setReference] = useState('');
  const [mint, setMint] = useState('');
  const [live, setLive] = useState<Live | null>(null);
  const [sim, setSim] = useState<Sim | null>(null);
  const [simSize, setSimSize] = useState('0.05');
  const [simHold, setSimHold] = useState('60');
  const [simLoading, setSimLoading] = useState(false);
  const [popup, setPopup] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const poll = useCallback(async (target: string) => {
    try {
      const response = await fetch(`${API}/v1/tokens/${encodeURIComponent(target)}/live`);
      setLive((await response.json()) as Live);
    } catch {
      /* reintenta en el siguiente tick */
    }
    timer.current = setTimeout(() => poll(target), REFRESH_MS);
  }, []);

  useEffect(() => {
    if (!mint) return;
    void poll(mint);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [mint, poll]);

  const start = () => {
    const value = reference.trim();
    if (!value) return;
    const withoutQuery = value.split('?')[0] ?? value;
    const cleaned = withoutQuery.replace(/\/$/, '').split('/').pop() ?? value;
    setLive(null);
    setSim(null);
    setMint(cleaned);
  };

  const simulate = async () => {
    if (!mint) return;
    setSimLoading(true);
    try {
      const response = await fetch(
        `${API}/v1/tokens/${encodeURIComponent(mint)}/simulate?size_sol=${simSize}&hold_seconds=${simHold}`,
      );
      setSim((await response.json()) as Sim);
    } catch {
      setSim({ ok: false, detail: 'No se pudo simular.' });
    } finally {
      setSimLoading(false);
    }
  };

  const snapshot = live?.snapshot;

  return (
    <div className="wrap">
      <header>
        <h1>Previsión en vivo</h1>
        <span className="badge read-only">solo lectura</span>
        <a className="badge" href="/">← radar</a>
        {live && (
          <span className="status">
            <span className="dot live" />
            refresco {live.refresh_ms} ms
          </span>
        )}
        {mint && (
          <button className="popup-btn" onClick={() => setPopup((v) => !v)}>
            {popup ? 'cerrar señal' : 'ventana de señal'}
          </button>
        )}
      </header>

      {popup && mint && live?.recommendation && (
        <div className="signal-popup">
          <div className="signal-popup-head">
            <span>Señal en vivo</span>
            <button onClick={() => setPopup(false)} aria-label="cerrar">×</button>
          </div>
          <div className={`popup-action reco-${live.recommendation.action.toLowerCase()}`}>
            {live.recommendation.action}
          </div>
          {live.traction && (
            <div className="popup-row"><span>Empuje</span><b>{live.traction.score.toFixed(0)}/100</b></div>
          )}
          {live.whale?.present && (
            <div className="popup-row whale"><span>🐋 Ballena</span><b>{live.whale.direction}</b></div>
          )}
          {live.pre_bounce?.present && (
            <div className="popup-row bounce"><span>↩ Prerrebote</span><b>+{live.pre_bounce.recovery_pct}%</b></div>
          )}
          <p className="popup-note mono">{live.recommendation.reason}</p>
        </div>
      )}

      <div className="lookup">
        <input
          value={reference}
          onChange={(e) => setReference(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && start()}
          placeholder="Pega el enlace de pump.fun o el mint"
          aria-label="Enlace o mint"
        />
        <button onClick={start}>seguir en vivo</button>
      </div>

      {live?.error && !live.candles.length && <p className="empty">RPC: {live.error}</p>}

      {mint && live?.recommendation && (
        <div className={`reco reco-${live.recommendation.action.toLowerCase()}`}>
          <div className="reco-action">{live.recommendation.action}</div>
          <div className="reco-body">
            {live.traction && (
              <div className="reco-traction">
                <div className="reco-bar"><span style={{ width: `${live.traction.score}%` }} /></div>
                <span>Empuje {live.traction.score.toFixed(0)}/100 · {live.traction.label}</span>
              </div>
            )}
            <p className="mono">{live.recommendation.reason}</p>
          </div>
        </div>
      )}

      {mint && (live?.whale?.present || live?.pre_bounce?.present) && (
        <div className="alerts">
          {live?.whale?.present && (
            <div className={`alert ${live.whale.direction === 'vendiendo' ? 'alert-bad' : 'alert-warn'}`}>
              🐋 Ballena {live.whale.direction} · {live.whale.share_of_volume}% del volumen · {live.whale.detail}
            </div>
          )}
          {live?.pre_bounce?.present && (
            <div className="alert alert-good">
              ↩ Posible prerrebote: {live.pre_bounce.detail}
            </div>
          )}
        </div>
      )}

      {mint && live?.traction && live.traction.signals.length > 1 && (
        <div className="impacts traction-signals">
          {live.traction.signals.map((sig) => (
            <div key={sig.name}>
              <span className="k">{sig.name.replace(/_/g, ' ')}</span>
              <span className="v" title={sig.detail}>{(sig.value * 100).toFixed(0)}%</span>
            </div>
          ))}
        </div>
      )}

      {mint && (
        <>
          <div className="charts">
            <CandleChart candles={live?.projected ?? []} title="Proyección +4s (cono)" projected />
            <CandleChart candles={live?.candles ?? []} title="Precio real (velas)" projected={false} />
          </div>
          <p className="mono charts-note">
            Izquierda: velas proyectadas del cono de percentiles, NO una predicción. Derecha:
            velas reales on-chain. Volatilidad medida: {live?.volatility_per_second ?? 0}/s.
          </p>

          {snapshot && (
            <>
              <h2>Capitalización y liquidez (cifras completas)</h2>
              <div className="fulltable">
                <div><span className="k">Precio (SOL)</span><span className="v">{snapshot.price_sol_exact}</span></div>
                <div><span className="k">Market cap (SOL)</span><span className="v">{snapshot.market_cap_sol_exact}</span></div>
                <div><span className="k">Liquidez (SOL)</span><span className="v">{snapshot.liquidity_sol_exact}</span></div>
                <div><span className="k">Para graduar</span><span className="v">{snapshot.sol_to_graduate.toFixed(4)} SOL</span></div>
                <div><span className="k">Cap al graduar</span><span className="v">{snapshot.graduation_market_cap_sol.toFixed(2)} SOL</span></div>
                <div><span className="k">Progreso</span><span className="v">{snapshot.progress_pct.toFixed(2)}%</span></div>
                <div><span className="k">Reservas SOL (v)</span><span className="v">{snapshot.virtual_sol_reserves.toLocaleString()}</span></div>
                <div><span className="k">Reservas token (v)</span><span className="v">{snapshot.virtual_token_reserves.toLocaleString()}</span></div>
                <div><span className="k">Invariante k</span><span className="v">{snapshot.invariant_k}</span></div>
                <div><span className="k">Compras / Ventas</span><span className="v">{snapshot.buys} / {snapshot.sells}</span></div>
                <div><span className="k">Traders únicos</span><span className="v">{snapshot.unique_traders}</span></div>
                <div><span className="k">Volumen observado</span><span className="v">{snapshot.volume_sol} SOL</span></div>
              </div>

              {live?.flow && Object.keys(live.flow).length > 0 && (
                <>
                  <h3>Flujo observado</h3>
                  <div className="fulltable">
                    <div><span className="k">Flujo neto</span><span className="v">{String(live.flow.net_flow_sol)} SOL</span></div>
                    <div><span className="k">SOL entrando</span><span className="v">{String(live.flow.sol_in)}</span></div>
                    <div><span className="k">SOL saliendo</span><span className="v">{String(live.flow.sol_out)}</span></div>
                    <div><span className="k">Ratio compra/venta</span><span className="v">{String(live.flow.buy_sell_ratio)}</span></div>
                    <div><span className="k">Operaciones/min</span><span className="v">{String(live.flow.trades_per_minute)}</span></div>
                    <div><span className="k">Aceleración</span><span className="v">{String(live.flow.acceleration)}×</span></div>
                    <div><span className="k">Mayor operación</span><span className="v">{String(live.flow.largest_trade_sol)} SOL ({String(live.flow.largest_trade_side)})</span></div>
                    <div><span className="k">Operación media</span><span className="v">{String(live.flow.avg_trade_sol)} SOL</span></div>
                  </div>
                </>
              )}

              <h3>Coste de entrar (price impact)</h3>
              <div className="impacts">
                {Object.entries(snapshot.price_impact_bps).map(([size, bps]) => (
                  <div key={size}><span className="k">{size} SOL</span><span className="v">{(bps / 100).toFixed(2)}%</span></div>
                ))}
              </div>
            </>
          )}

          <h2>Simulador</h2>
          <div className="sim-controls">
            <label>Tamaño (SOL)<input value={simSize} onChange={(e) => setSimSize(e.target.value)} /></label>
            <label>Mantener (s)<input value={simHold} onChange={(e) => setSimHold(e.target.value)} /></label>
            <button onClick={simulate} disabled={simLoading || !live?.trades}>
              {simLoading ? 'simulando…' : 'simular operación'}
            </button>
          </div>

          {sim && !sim.ok && <p className="empty">{sim.detail}</p>}
          {sim?.ok && (
            <>
              <div className="stats">
                <div className="stat"><div className="label">Mediana</div><div className={`value ${(sim.median_sol ?? 0) >= 0 ? 'good' : 'bad'}`}>{(sim.median_sol ?? 0).toFixed(6)} SOL</div></div>
                <div className="stat"><div className="label">P10 (malo)</div><div className="value bad">{(sim.p10_sol ?? 0).toFixed(6)}</div></div>
                <div className="stat"><div className="label">P90 (bueno)</div><div className="value good">{(sim.p90_sol ?? 0).toFixed(6)}</div></div>
                <div className="stat"><div className="label">Corridas en pérdida</div><div className="value">{sim.losing_runs}/{sim.runs}</div></div>
                <div className="stat"><div className="label">Slippage medio</div><div className="value">{sim.avg_entry_slippage_bps} bps</div></div>
                <div className="stat"><div className="label">Latencia media</div><div className="value">{sim.avg_latency_ms} ms</div></div>
              </div>
              <p className="mono">
                Peor caso {(sim.worst_sol ?? 0).toFixed(6)} · mejor {(sim.best_sol ?? 0).toFixed(6)} · fees {sim.fees_sol} SOL ·
                posiciones atrapadas {sim.stuck_positions}. Distribución sobre {sim.runs} corridas
                con costes reales. Ninguna orden se envía.
              </p>
            </>
          )}
        </>
      )}

      <footer>Sin trading. Los veredictos son análisis, no recomendaciones. No puede abrir ni cerrar posiciones.</footer>
    </div>
  );
}
