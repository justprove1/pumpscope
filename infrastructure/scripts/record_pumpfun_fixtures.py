"""Graba fixtures REALES de creaciones de token de Pump.fun.

CLAUDE.md 2 prohibe inventar respuestas de API. Este script es la unica via por la que entra
una fixture al repositorio: se conecta al RPC, escucha creaciones de verdad y guarda lo que
llega, sin retocar.

Uso:
    python infrastructure/scripts/record_pumpfun_fixtures.py --count 5

Por defecto usa el RPC publico, que no requiere credencial. Con `--rpc-url` y `--wss-url` se
apunta a Helius u otro proveedor.

Lo capturado por cada creacion:
    - La notificacion de `logsSubscribe` tal cual llego (con su timestamp de recepcion).
    - La transaccion completa de `getTransaction`, con cuentas y datos de instruccion.

Ambas cosas hacen falta: la primera para probar el detector y medir latencia, la segunda
para probar el decoder de instrucciones y cuentas.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import websockets

# Verificado on-chain: cuenta ejecutable, propiedad de BPFLoaderUpgradeable.
PUMPFUN_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

DEFAULT_RPC = "https://api.mainnet-beta.solana.com"
DEFAULT_WSS = "wss://api.mainnet-beta.solana.com"

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures"

# El programa registra la instruccion en los logs. Se busca el prefijo porque la version
# vigente emite `CreateV2`, y una version futura podria emitir `CreateV3`: mejor capturar de
# mas y filtrar despues que perder creaciones en silencio.
CREATE_LOG_PREFIX = "Program log: Instruction: Create"


def _now() -> datetime:
    return datetime.now(UTC)


async def _fetch_transaction(client: httpx.AsyncClient, rpc_url: str, signature: str) -> Any:
    """Pide la transaccion completa, reintentando mientras el nodo aun no la sirva.

    Con commitment `confirmed` la notificacion puede llegar antes de que `getTransaction`
    devuelva algo. No es un error: es la carrera normal entre los dos endpoints.
    """
    for attempt in range(6):
        response = await client.post(
            rpc_url,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [
                    signature,
                    {
                        "encoding": "json",
                        "maxSupportedTransactionVersion": 0,
                        "commitment": "confirmed",
                    },
                ],
            },
            timeout=20.0,
        )
        payload = response.json()
        if payload.get("result"):
            return payload
        await asyncio.sleep(1.0 + attempt)
    return None


async def record(count: int, rpc_url: str, wss_url: str, timeout_seconds: float) -> int:
    captured: list[dict[str, Any]] = []
    started = time.monotonic()
    total_events = 0

    async with (
        httpx.AsyncClient() as client,
        websockets.connect(wss_url, ping_interval=20, max_size=20_000_000) as ws,
    ):
        await ws.send(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "logsSubscribe",
                    "params": [
                        {"mentions": [PUMPFUN_PROGRAM]},
                        {"commitment": "confirmed"},
                    ],
                }
            )
        )
        ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
        if "error" in ack:
            msg = f"logsSubscribe rechazado: {ack['error']}"
            raise RuntimeError(msg)
        print(f"suscrito (id={ack.get('result')}), escuchando...")

        while len(captured) < count and time.monotonic() - started < timeout_seconds:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=30)
            except TimeoutError:
                print("sin eventos en 30s")
                break

            received_at = _now()
            total_events += 1
            notification = json.loads(raw)
            value = notification.get("params", {}).get("result", {}).get("value", {})
            logs: list[str] = value.get("logs", [])

            if value.get("err") is not None:
                continue
            if not any(log.startswith(CREATE_LOG_PREFIX) for log in logs):
                continue

            signature = value.get("signature")
            if not signature:
                continue

            print(f"  creacion detectada: {signature[:20]}... obteniendo transaccion")
            transaction = await _fetch_transaction(client, rpc_url, signature)
            if transaction is None:
                print("    (el nodo aun no la sirve; se descarta)")
                continue

            captured.append(
                {
                    "signature": signature,
                    "received_timestamp": received_at.isoformat(),
                    "log_notification": notification,
                    "transaction": transaction,
                }
            )
            print(f"    guardada ({len(captured)}/{count})")

    if not captured:
        print("no se capturo ninguna creacion")
        return 0

    out = FIXTURES_DIR / "pumpfun_create_events.json"
    out.write_text(
        json.dumps(
            {
                "_fixture_meta": {
                    "captured_at_utc": _now().isoformat(),
                    "source": rpc_url,
                    "method": "logsSubscribe + getTransaction",
                    "program_id": PUMPFUN_PROGRAM,
                    "sample_count": len(captured),
                    "observed_events_total": total_events,
                    "note": (
                        "Respuestas REALES sin modificar. Solo contienen direcciones "
                        "publicas de Solana; ningun dato personal."
                    ),
                },
                "events": captured,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n{len(captured)} creaciones guardadas en {out}")
    print(f"eventos totales observados: {total_events}")
    return len(captured)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--rpc-url", default=DEFAULT_RPC)
    parser.add_argument("--wss-url", default=DEFAULT_WSS)
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args()
    asyncio.run(record(args.count, args.rpc_url, args.wss_url, args.timeout))


if __name__ == "__main__":
    main()
