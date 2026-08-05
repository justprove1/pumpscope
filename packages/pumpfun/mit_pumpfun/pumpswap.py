"""PumpSwap: el AMM al que pasan los tokens de Pump.fun tras graduarse (SPEC.md 4).

**El agujero que tapa este modulo.** Cuando un token gradua, deja de emitir `TradeEvent` de la
bonding curve y empieza a operar en PumpSwap. Todo lo que mira la curva se queda ciego justo con
los tokens que triunfan. Se observo con V713/VanillaFunk: ~5 transacciones por segundo en cadena
mientras el visor mostraba cero velas.

**Como se dedujo el layout.** No hace falta ninguna API ni credencial: PumpSwap es un programa
on-chain que emite eventos Anchor, con el mismo esquema de discriminador que Pump.fun
(`sha256("event:<Nombre>")[:8]`). Los offsets se verificaron contra mainnet y se validan con la
aritmetica interna del propio evento: `lp_fee == quote_in_with_lp_fee * lp_fee_bps / 10000`. Si
los campos estuvieran desplazados, esa igualdad no se cumpliria.

**Base y quote.** `base` es el memecoin, `quote` es SOL. Se confirmo con las reservas: un pool
real daba 64,09 SOL contra 215,3M tokens, lo que arroja una capitalizacion de ~298 SOL. Con la
lectura invertida salian cifras absurdas.

**El mint no viene en el evento.** Trae `pool` y `user`, no el mint. No es un problema: estos
eventos se consumen desde una suscripcion ya filtrada por mint (`mentions=[mint]`), asi que el
llamante sabe de que token son y lo pasa. Adivinarlo requeriria leer la cuenta del pool.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
from dataclasses import dataclass
from typing import Final

PUMPSWAP_PROGRAM_ID: Final = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
PROGRAM_DATA_PREFIX: Final = "Program data: "

DISCRIMINATOR_BUY: Final = hashlib.sha256(b"event:BuyEvent").digest()[:8]
DISCRIMINATOR_SELL: Final = hashlib.sha256(b"event:SellEvent").digest()[:8]

# Offsets dentro del cuerpo (tras los 8 bytes de discriminador). Ambos eventos comparten
# estructura: cambia el sentido de los importes, no su posicion.
_OFF_TIMESTAMP: Final = 0
_OFF_BASE_AMOUNT: Final = 8  # tokens que salen (compra) o entran (venta)
_OFF_POOL_BASE_RESERVES: Final = 40
_OFF_POOL_QUOTE_RESERVES: Final = 48
_OFF_QUOTE_AMOUNT: Final = 56  # SOL que entra (compra) o sale (venta)
# Hasta `quote_amount` hay 8 campos u64: es el minimo para poder decodificar.
_MIN_BODY_BYTES: Final = _OFF_QUOTE_AMOUNT + 8


@dataclass(frozen=True, slots=True)
class PumpSwapTrade:
    """Una operacion en PumpSwap, en la MISMA forma que un `TradeEvent` de la curva.

    Los nombres `virtual_*_reserves` se conservan a proposito aunque en un AMM las reservas sean
    reales: permiten que velas, traccion, ballenas y prerrebotes funcionen sin tocar una linea.
    Para todo lo de aguas abajo, un token graduado se comporta igual que uno en la curva.
    """

    mint: str
    sol_amount: int
    token_amount: int
    is_buy: bool
    user: str
    timestamp: int
    virtual_sol_reserves: int
    virtual_token_reserves: int

    @property
    def side(self) -> str:
        return "buy" if self.is_buy else "sell"


def _u64(body: bytes, offset: int) -> int:
    return int.from_bytes(body[offset : offset + 8], "little")


def decode_pumpswap_trade(raw: bytes, mint: str) -> PumpSwapTrade | None:
    """Decodifica un evento de PumpSwap. `None` si no es una operacion utilizable.

    Devolver `None` es el camino normal: la mayoria de las lineas `Program data:` de un bloque
    no son compras ni ventas de este pool.
    """
    discriminator = raw[:8]
    if discriminator == DISCRIMINATOR_BUY:
        is_buy = True
    elif discriminator == DISCRIMINATOR_SELL:
        is_buy = False
    else:
        return None

    body = raw[8:]
    if len(body) < _MIN_BODY_BYTES:
        # Evento truncado: los logs son datos de terceros y no pueden tumbar la ingesta.
        return None

    sol_amount = _u64(body, _OFF_QUOTE_AMOUNT)
    token_amount = _u64(body, _OFF_BASE_AMOUNT)
    # Un intercambio de cero no es una operacion: ensuciaria precios y volumen.
    if sol_amount <= 0 or token_amount <= 0:
        return None

    quote_reserves = _u64(body, _OFF_POOL_QUOTE_RESERVES)
    base_reserves = _u64(body, _OFF_POOL_BASE_RESERVES)
    if quote_reserves <= 0 or base_reserves <= 0:
        return None

    return PumpSwapTrade(
        mint=mint,
        sol_amount=sol_amount,
        token_amount=token_amount,
        is_buy=is_buy,
        # El evento trae `user`, pero tras los 14 u64 y solo en algunas variantes de longitud.
        # Se deja vacio en lugar de leer bytes que podrian no ser esa clave: una atribucion
        # inventada romperia la deteccion de clusters mas de lo que la ausencia molesta.
        user="",
        timestamp=_u64(body, _OFF_TIMESTAMP),
        virtual_sol_reserves=quote_reserves,
        virtual_token_reserves=base_reserves,
    )


def find_pumpswap_trades(logs: list[str], mint: str) -> list[PumpSwapTrade]:
    """Todas las operaciones de PumpSwap en unos logs. Lista vacia si no hay ninguna."""
    trades: list[PumpSwapTrade] = []
    for line in logs:
        if not line.startswith(PROGRAM_DATA_PREFIX):
            continue
        payload = line[len(PROGRAM_DATA_PREFIX) :].strip()
        try:
            raw = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError):
            continue
        trade = decode_pumpswap_trade(raw, mint)
        if trade is not None:
            trades.append(trade)
    return trades
