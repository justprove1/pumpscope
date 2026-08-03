"""Minero de corpus historico para calibracion y entrenamiento (SPEC.md 18).

**Captura TODO lo que nace, sin filtrar.** Es la unica defensa contra el sesgo de
supervivencia: si se minan solo los tokens que uno ya conoce —o los que "prometian"—, el
corpus queda contaminado antes de empezar y el backtest medira la seleccion posterior, no la
estrategia. SPEC.md 18 exige que los rugs, los tokens sin liquidez y los que murieron en
minutos esten DENTRO.

Funciona en dos hilos logicos:

1. Escucha creaciones en vivo y las registra todas, ganen o pierdan.
2. Cada cierto tiempo revisita los tokens ya registrados y amplia su serie de precio con las
   operaciones nuevas.

El resultado es un corpus creciente en `tests/fixtures/corpus/`, un archivo por token, con su
procedencia declarada. Es reanudable: al arrancar carga lo que ya hay y sigue.

El ritmo esta deliberadamente por debajo del limite del RPC publico. Minar rapido y que te
corten produce un corpus con agujeros, que es peor que uno pequeno y completo.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import signal
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import websockets
from mit_pumpfun.constants import PUMPFUN_PROGRAM_ID
from mit_pumpfun.detector import NewTokenDetector
from mit_pumpfun.events import find_trade_events
from mit_solana.logs_stream import ResilientLogStream
from mit_solana.rpc import RpcLimits, SolanaRpc

CORPUS_DIR = Path(__file__).resolve().parents[2] / "tests/fixtures/corpus"
WSS = "wss://api.mainnet-beta.solana.com"

# Ritmo conservador: el endpoint publico corta con 429 en cuanto se aprieta, y un corpus con
# agujeros vale menos que uno pequeno y completo.
REVISIT_EVERY_SECONDS = 90
SIGNATURES_PER_VISIT = 40
TRANSACTIONS_PER_VISIT = 12


def _token_path(mint: str) -> Path:
    return CORPUS_DIR / f"{mint}.json"


def _load(mint: str) -> dict[str, Any]:
    path = _token_path(mint)
    if path.exists():
        payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return payload
    return {}


def _save(mint: str, payload: dict[str, Any]) -> None:
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    _token_path(mint).write_text(json.dumps(payload, indent=1), encoding="utf-8")


class CorpusMiner:
    """Registra creaciones y amplia sus series de precio."""

    def __init__(self) -> None:
        self._detector = NewTokenDetector(provider="solana-public-rpc")
        self._known: dict[str, dict[str, Any]] = {}
        self._stop = asyncio.Event()

    def request_stop(self) -> None:
        self._stop.set()

    def _resume(self) -> None:
        CORPUS_DIR.mkdir(parents=True, exist_ok=True)
        for path in CORPUS_DIR.glob("*.json"):
            with contextlib.suppress(Exception):
                payload = json.loads(path.read_text(encoding="utf-8"))
                self._known[payload["mint"]] = payload
        if self._known:
            print(f"[minero] reanudado con {len(self._known)} tokens ya capturados", flush=True)

    async def _connect(self) -> Any:
        return await websockets.connect(WSS, ping_interval=20, max_size=20_000_000)

    async def _listen(self) -> None:
        """Registra TODA creacion. Sin filtro: el filtro es el sesgo."""
        stream = ResilientLogStream(PUMPFUN_PROGRAM_ID, self._connect)
        async for notification in stream:
            if self._stop.is_set():
                return
            token = self._detector.observe(notification)
            if token is None or token.mint in self._known:
                continue
            payload = {
                "_fixture_meta": {
                    "captured_at_utc": datetime.now(UTC).isoformat(),
                    "source": WSS,
                    "method": "logsSubscribe (creacion) + getTransaction (historial)",
                    "case": "corpus_sin_filtrar",
                    "note": (
                        "Capturado SIN filtrar para evitar sesgo de supervivencia: incluye "
                        "rugs y tokens muertos. Solo direcciones publicas de Solana."
                    ),
                },
                "mint": token.mint,
                "creator": token.event.creator,
                "symbol": token.event.symbol,
                "name": token.event.name,
                "created_at": token.event.timestamp,
                "series": [],
            }
            self._known[token.mint] = payload
            _save(token.mint, payload)
            print(
                f"[minero] +{token.event.symbol:<12} {token.mint[:14]} (total {len(self._known)})",
                flush=True,
            )

    async def _revisit(self) -> None:
        """Amplia la serie de precio de los tokens ya registrados."""
        async with SolanaRpc(limits=RpcLimits(requests_per_second=2.5)) as rpc:
            while not self._stop.is_set():
                await asyncio.sleep(REVISIT_EVERY_SECONDS)
                # Los mas recientes primero: es donde hay actividad que capturar.
                for mint in list(self._known)[-12:]:
                    if self._stop.is_set():
                        return
                    try:
                        signatures = await rpc.get_signatures(mint, limit=SIGNATURES_PER_VISIT)
                    except Exception as error:
                        # Un 429 o un corte no puede parar el minero: se anota y se sigue con
                        # el siguiente token. Perder una visita es barato; perder el minero, no.
                        print(f"[minero] {mint[:12]}: {str(error)[:50]}", flush=True)
                        continue
                    payload = self._known[mint]
                    seen = {point["sig"] for point in payload["series"]}
                    added = 0
                    for entry in signatures[:TRANSACTIONS_PER_VISIT]:
                        if entry.get("err") or entry["signature"] in seen:
                            continue
                        try:
                            transaction = await rpc.get_transaction(entry["signature"])
                        except Exception:
                            break
                        if not transaction:
                            continue
                        logs = (transaction.get("meta") or {}).get("logMessages") or []
                        for event in find_trade_events(logs):
                            payload["series"].append(
                                {
                                    "sig": entry["signature"],
                                    "t": event.timestamp,
                                    "vsol": event.virtual_sol_reserves,
                                    "vtok": event.virtual_token_reserves,
                                    "side": event.side,
                                    "sol": event.sol_amount,
                                    "user": event.user,
                                }
                            )
                            added += 1
                    if added:
                        _save(mint, payload)
                total_points = sum(len(p["series"]) for p in self._known.values())
                print(
                    f"[minero] {len(self._known)} tokens · {total_points} puntos de precio",
                    flush=True,
                )

    async def run(self) -> None:
        self._resume()
        print("[minero] capturando TODO lo que nace (sin filtrar). Ctrl-C para parar.", flush=True)
        await asyncio.gather(self._listen(), self._revisit())


async def main() -> None:
    miner = CorpusMiner()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, miner.request_stop)
    with contextlib.suppress(asyncio.CancelledError):
        await miner.run()


if __name__ == "__main__":
    asyncio.run(main())
