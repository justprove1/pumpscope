/** Operativa real sobre Pump.fun desde el navegador.
 *
 * La transacción la construye la API y la FIRMA la cartera del usuario. Aquí no hay ninguna
 * clave ni acceso a ella: lo único que viaja es una transacción sin firmar, y la cartera se
 * la enseña al usuario antes de enviar nada.
 *
 * La API simula cada operación contra la cadena antes de devolverla, así que lo que llega
 * aquí es algo que el programa ya ha aceptado. Lo que puede fallar después es el precio
 * moviéndose, y para eso está el slippage.
 */

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

/** Lo mínimo del proveedor de Phantom que este módulo usa. */
type PhantomProvider = {
  isPhantom?: boolean;
  connect: (opts?: { onlyIfTrusted: boolean }) => Promise<{ publicKey: { toString: () => string } }>;
  disconnect: () => Promise<void>;
  request: (args: { method: string; params: { message: string } }) => Promise<{ signature: string }>;
};

type WindowWithPhantom = Window & {
  phantom?: { solana?: PhantomProvider };
  solana?: PhantomProvider;
};

export type TradeSummary = {
  side: 'buy' | 'sell';
  amount_sol?: number;
  tokens_expected?: number;
  tokens_sold?: number;
  max_cost_sol?: number;
  expected_sol?: number;
  min_output_sol?: number;
  fee_bps: number;
  slippage_bps: number;
};

export type TradeResult = { signature: string; summary: TradeSummary };

export class TradeError extends Error {}

export function getProvider(): PhantomProvider | null {
  if (typeof window === 'undefined') return null;
  const w = window as WindowWithPhantom;
  const provider = w.phantom?.solana ?? w.solana;
  return provider?.isPhantom ? provider : null;
}

export async function connectWallet(): Promise<string> {
  const provider = getProvider();
  if (!provider) {
    throw new TradeError('No se detecta Phantom. Instálalo en phantom.app y recarga la página.');
  }
  const { publicKey } = await provider.connect();
  return publicKey.toString();
}

/** Reconecta sin molestar si el usuario ya autorizó este sitio antes. */
export async function reconnectWallet(): Promise<string | null> {
  const provider = getProvider();
  if (!provider) return null;
  try {
    const { publicKey } = await provider.connect({ onlyIfTrusted: true });
    return publicKey.toString();
  } catch {
    return null;
  }
}

export async function disconnectWallet(): Promise<void> {
  await getProvider()?.disconnect();
}

/** Deja lista la parte cara de la preparación antes de que se pulse el botón. */
export async function warmToken(mint: string): Promise<void> {
  try {
    await fetch(`${API}/v1/trade/warm/${encodeURIComponent(mint)}`, { method: 'POST' });
  } catch {
    // Precalentar es una optimización: si falla, la compra sigue funcionando, solo más lenta.
  }
}

export async function tokenBalance(user: string, mint: string): Promise<number> {
  const response = await fetch(`${API}/v1/trade/position/${user}/${mint}`);
  if (!response.ok) return 0;
  const data = (await response.json()) as { balance: number };
  return data.balance;
}

type PrepareBody = {
  mint: string;
  user: string;
  side: 'buy' | 'sell';
  amount_sol?: number;
  sell_percent?: number;
  slippage_bps: number;
};

/** Prepara, firma y envía. Devuelve la firma; no espera a la confirmación. */
async function submit(body: PrepareBody): Promise<TradeResult> {
  const provider = getProvider();
  if (!provider) throw new TradeError('Conecta Phantom primero.');

  const response = await fetch(`${API}/v1/trade/prepare`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = (await response.json().catch(() => ({}))) as {
    detail?: string;
    message_base58?: string;
    summary?: TradeSummary;
  };
  if (!response.ok || !data.message_base58 || !data.summary) {
    throw new TradeError(data.detail ?? `la API respondió ${response.status}`);
  }

  // Phantom firma Y envía; la clave no sale de la extensión en ningún momento.
  const { signature } = await provider.request({
    method: 'signAndSendTransaction',
    params: { message: data.message_base58 },
  });
  return { signature, summary: data.summary };
}

export function buy(
  mint: string,
  user: string,
  amountSol: number,
  slippagePct: number,
): Promise<TradeResult> {
  return submit({
    mint,
    user,
    side: 'buy',
    amount_sol: amountSol,
    slippage_bps: Math.round(slippagePct * 100),
  });
}

export function sell(
  mint: string,
  user: string,
  percent: number,
  slippagePct: number,
): Promise<TradeResult> {
  return submit({
    mint,
    user,
    side: 'sell',
    sell_percent: percent,
    slippage_bps: Math.round(slippagePct * 100),
  });
}

export type ConfirmState = 'confirmada' | 'fallida' | 'pendiente';

/** Espera a saber si entró.
 *
 * `pendiente` NO es `fallida`, y mezclarlas cuesta dinero: dar por perdida una compra que sí
 * entró lleva a comprar otra vez el mismo token.
 */
export async function waitForConfirmation(
  signature: string,
  seconds = 45,
): Promise<{ state: ConfirmState; error?: string }> {
  const until = Date.now() + seconds * 1000;
  while (Date.now() < until) {
    try {
      const response = await fetch(`${API}/v1/trade/status/${signature}`);
      const data = (await response.json()) as { state: ConfirmState; error?: string };
      if (data.state === 'confirmada' || data.state === 'fallida') return data;
    } catch {
      // Un corte puntual al consultar no significa que la transacción se haya perdido.
    }
    await new Promise((resolve) => setTimeout(resolve, 1200));
  }
  return { state: 'pendiente' };
}

export const explorerUrl = (signature: string) => `https://solscan.io/tx/${signature}`;
