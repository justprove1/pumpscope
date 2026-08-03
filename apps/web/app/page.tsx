'use client';

import { useEffect, useRef, useState } from 'react';

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

type Row = Token & { fresh?: boolean; analysis?: Analysis };

function short(address: string): string {
  return address.length > 12 ? `${address.slice(0, 4)}…${address.slice(-4)}` : address;
}

export default function Page() {
  const [rows, setRows] = useState<Row[]>([]);
  const [connected, setConnected] = useState(false);
  const [detected, setDetected] = useState(0);
  const socket = useRef<WebSocket | null>(null);

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
          payload?: Token & Partial<Analysis>;
        };
        if (parsed.channel === 'tokens.analysis' && parsed.payload) {
          const verdict = parsed.payload as unknown as Analysis;
          setRows((current) =>
            current.map((row) => (row.mint === verdict.mint ? { ...row, analysis: verdict } : row)),
          );
          return;
        }
        if (parsed.channel !== 'tokens.new' || !parsed.payload) return;
        setDetected((n) => n + 1);
        setRows((current) => [{ ...parsed.payload!, fresh: true }, ...current].slice(0, MAX_ROWS));
      };
    };

    connect();
    return () => {
      stopped = true;
      clearTimeout(retry);
      socket.current?.close();
    };
  }, []);

  return (
    <div className="wrap">
      <header>
        <h1>Memecoin Intelligence Terminal</h1>
        <span className="badge read-only">solo lectura</span>
        <span className="badge">fase 1</span>
        <a className="badge" href="/prevision">previsión tokens →</a>
        <span className="status">
          <span className={connected ? 'dot live' : 'dot'} />
          {connected ? 'en vivo' : 'desconectado'}
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

      <table>
        <thead>
          <tr>
            <th>Token</th>
            <th>Mint</th>
            <th>Creador</th>
            <th>Score</th>
            <th>Riesgo</th>
            <th>Holders</th>
            <th>Señal</th>
            <th>Visto</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.mint} className={row.fresh ? 'fresh' : undefined}>
              <td>
                <span className="symbol">{row.symbol || '—'}</span>
                <div className="mono">{row.name}</div>
              </td>
              <td className="mono">{short(row.mint)}</td>
              <td className="mono">
                {short(row.creator)}
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
                {row.received_timestamp
                  ? new Date(row.received_timestamp).toLocaleTimeString('es-ES')
                  : '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {rows.length === 0 && (
        <div className="empty">
          Sin tokens todavía. Arranca la ingesta con <code>make ingest</code>.
        </div>
      )}

      <footer>
        Los veredictos son análisis, no recomendaciones. Esta interfaz no puede abrir ni
        cerrar ninguna posición, y el sistema no tiene el trading real habilitado.
        Pasa el ratón por el riesgo para ver las razones concretas.
      </footer>
    </div>
  );
}
