"""Precio de SOL en moneda fiat (SPEC.md 5).

**Por que existe.** Todas las cifras del sistema son on-chain y se miden en SOL, que es lo
correcto: no dependen de ningun proveedor y no envejecen. Pero un usuario piensa en euros, y
sin una referencia de precio las equivalencias que se enseñaban eran suposiciones. De hecho lo
eran: se venia asumiendo SOL~166 USD cuando cotizaba a 74, asi que los umbrales rotulados como
"$50k" valian en realidad menos de la mitad.

**Se cachea a proposito.** El precio de SOL no se mueve lo bastante en un minuto como para
justificar una llamada por cada refresco del panel, y machacar un endpoint publico gratuito es
la forma de que deje de responder.

**Si falla, se dice.** Ante error se devuelve el ultimo valor conocido marcado como obsoleto, y
si no hay ninguno se devuelve `None`. Nunca se inventa un precio: una conversion inventada es
peor que no dar la conversion.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

PRICE_URL = "https://api.coingecko.com/api/v3/simple/price"
CACHE_SECONDS = 60.0
TIMEOUT_SECONDS = 6.0


@dataclass(frozen=True, slots=True)
class SolPrice:
    """Precio de 1 SOL. `stale` avisa de que la ultima consulta fallo."""

    eur: float
    usd: float
    fetched_at: float
    stale: bool = False

    def as_dict(self) -> dict[str, float | bool | None]:
        return {
            "eur": round(self.eur, 4),
            "usd": round(self.usd, 4),
            "age_seconds": round(time.time() - self.fetched_at, 1),
            "stale": self.stale,
        }


class SolPriceService:
    """Sirve el precio de SOL desde cache, refrescandolo como mucho cada `CACHE_SECONDS`."""

    def __init__(self, url: str = PRICE_URL, cache_seconds: float = CACHE_SECONDS) -> None:
        self._url = url
        self._cache_seconds = cache_seconds
        self._cached: SolPrice | None = None

    async def get(self) -> SolPrice | None:
        cached = self._cached
        if cached is not None and time.time() - cached.fetched_at < self._cache_seconds:
            return cached

        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                response = await client.get(
                    self._url, params={"ids": "solana", "vs_currencies": "eur,usd"}
                )
                response.raise_for_status()
                payload = response.json()
            quote = payload["solana"]
            price = SolPrice(
                eur=float(quote["eur"]), usd=float(quote["usd"]), fetched_at=time.time()
            )
        except (httpx.HTTPError, KeyError, ValueError, TypeError):
            # Se conserva el ultimo valor conocido, marcado como obsoleto. Sin valor previo se
            # devuelve None y el cliente muestra las cifras en SOL, que siempre son exactas.
            if cached is None:
                return None
            return SolPrice(
                eur=cached.eur, usd=cached.usd, fetched_at=cached.fetched_at, stale=True
            )

        self._cached = price
        return price
