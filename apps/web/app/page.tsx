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

type Row = Token & { fresh?: boolean };

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
          payload?: Token;
        };
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
            <th>Slot</th>
            <th>Latencia</th>
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
              <td className="mono">{row.slot || '—'}</td>
              <td className="mono">
                {row.pipeline_latency_ms ? `${row.pipeline_latency_ms.toFixed(1)} ms` : '—'}
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
        Sin trading. Sin señales. Esta interfaz no puede abrir ni cerrar ninguna posición.
      </footer>
    </div>
  );
}
