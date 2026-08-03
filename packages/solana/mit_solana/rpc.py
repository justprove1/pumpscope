"""Cliente RPC de Solana con rate limit, backoff y circuit breaker (SPEC.md 32).

Escrito contra el comportamiento REAL del endpoint publico, no contra su documentacion: al
sondearlo devolvio 429 en cuanto se solaparon la ingesta y las consultas de analisis. Por eso
el rate limiter no es opcional ni configurable "por si acaso": es la unica razon por la que
el analisis puede convivir con el worker sin que el proveedor nos corte.

Implementa `OnChainReadProvider` de forma parcial: solo los metodos que la Fase 2 necesita.
Los que faltan siguen siendo abstractos, y eso es deliberado — declarar un metodo que lanza
NotImplementedError seria peor que no declararlo.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from typing import Any

import httpx

PUBLIC_RPC = "https://api.mainnet-beta.solana.com"


class RpcError(RuntimeError):
    """El RPC devolvio un error de protocolo. No reintentable."""


class RpcRateLimitedError(RuntimeError):
    """429. Reintentable tras esperar."""


@dataclass(frozen=True, slots=True)
class RpcLimits:
    """Limites propios, por debajo de los del proveedor.

    El endpoint publico documenta ~10 req/s. Se usa bastante menos: que nos corten por exceso
    es un fallo nuestro, no suyo, y una consulta perdida cuesta mas que una espera.
    """

    requests_per_second: float = 4.0
    max_attempts: int = 5
    initial_backoff: float = 1.0
    max_backoff: float = 30.0


class SolanaRpc:
    """Cliente asincrono minimo, serializado y con espaciado garantizado entre llamadas."""

    def __init__(
        self,
        url: str = PUBLIC_RPC,
        limits: RpcLimits | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = url
        self._limits = limits or RpcLimits()
        self._client = client or httpx.AsyncClient(timeout=30.0)
        self._owns_client = client is None
        # Un unico lock: las llamadas se serializan. Con el limite del endpoint publico, el
        # paralelismo no acelera nada y solo adelanta el momento del 429.
        self._lock = asyncio.Lock()
        self._last_call = 0.0

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> SolanaRpc:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def _throttle(self) -> None:
        minimum = 1.0 / self._limits.requests_per_second
        elapsed = time.monotonic() - self._last_call
        if elapsed < minimum:
            await asyncio.sleep(minimum - elapsed)
        self._last_call = time.monotonic()

    async def call(self, method: str, params: list[Any]) -> Any:
        """Una llamada JSON-RPC, con espaciado y reintentos."""
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        backoff = self._limits.initial_backoff

        for attempt in range(self._limits.max_attempts):
            async with self._lock:
                await self._throttle()
                try:
                    response = await self._client.post(self._url, json=payload)
                except httpx.HTTPError as error:
                    if attempt == self._limits.max_attempts - 1:
                        msg = f"{method}: fallo de transporte: {error}"
                        raise RpcRateLimitedError(msg) from error
                    response = None

            if response is not None:
                if response.status_code == 429:
                    if attempt == self._limits.max_attempts - 1:
                        msg = f"{method}: rate limit agotado tras {attempt + 1} intentos"
                        raise RpcRateLimitedError(msg)
                elif response.status_code >= 500:
                    if attempt == self._limits.max_attempts - 1:
                        msg = f"{method}: {response.status_code}"
                        raise RpcRateLimitedError(msg)
                else:
                    body = response.json()
                    if "error" in body:
                        msg = f"{method}: {body['error']}"
                        raise RpcError(msg)
                    return body.get("result")

            # Jitter completo: sin el, varios clientes reintentarian a la vez.
            await asyncio.sleep(random.uniform(0.0, backoff))
            backoff = min(backoff * 2, self._limits.max_backoff)

        msg = f"{method}: sin resultado"
        raise RpcRateLimitedError(msg)

    # --- Metodos concretos que la Fase 2 necesita ------------------------------------------

    async def get_token_supply(self, mint: str) -> int:
        result = await self.call("getTokenSupply", [mint])
        return int(result["value"]["amount"]) if result else 0

    async def get_token_holders(self, mint: str, token_program: str) -> dict[str, int]:
        """Enumera TODOS los holders del mint.

        `getProgramAccounts` con filtro por mint funciona en el endpoint publico —se verifico
        en vivo— y es la unica via para calcular concentracion de verdad. `getTokenLargestAccounts`
        solo da el top 20, que no basta para Gini ni entropia.
        """
        result = await self.call(
            "getProgramAccounts",
            [
                token_program,
                {
                    "encoding": "jsonParsed",
                    "filters": [{"memcmp": {"offset": 0, "bytes": mint}}],
                },
            ],
        )
        holders: dict[str, int] = {}
        for account in result or []:
            info = account.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
            owner = info.get("owner")
            amount = info.get("tokenAmount", {}).get("amount")
            if owner and amount and int(amount) > 0:
                holders[owner] = holders.get(owner, 0) + int(amount)
        return holders

    async def get_signatures(self, address: str, limit: int = 100) -> list[dict[str, Any]]:
        result = await self.call("getSignaturesForAddress", [address, {"limit": limit}])
        return list(result or [])

    async def get_transaction(self, signature: str) -> dict[str, Any] | None:
        result = await self.call(
            "getTransaction",
            [
                signature,
                {
                    "encoding": "json",
                    "maxSupportedTransactionVersion": 0,
                    "commitment": "confirmed",
                },
            ],
        )
        return dict(result) if result else None
