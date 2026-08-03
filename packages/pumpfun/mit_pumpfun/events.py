"""Eventos Anchor que Pump.fun emite en los logs.

**Por que esto importa mas que el decoder de instrucciones.**

El programa emite el `CreateEvent` completo dentro de los propios logs, en una linea
`Program data: <base64>`. Contiene nombre, simbolo, URI, mint, bonding curve, usuario,
creador, timestamp y las reservas iniciales de la curva.

Es decir: la notificacion de `logsSubscribe` ya trae TODO lo necesario para registrar un
token nuevo. No hace falta llamar a `getTransaction`.

Eso no es una optimizacion menor. El objetivo de SPEC.md 6 es registrar el token en menos de
1 segundo, y una ida y vuelta extra al RPC se come una fraccion enorme de ese presupuesto
—mas aun con un endpoint publico con rate limit—. Decodificar desde el log deja la deteccion
en el coste de un `base64.b64decode` y unos cuantos slices.

El decoder de instrucciones (`decoder.py`) sigue haciendo falta para reconstruir historico a
partir de transacciones ya confirmadas, donde no hay notificacion en vivo.

Todo lo de aqui esta verificado contra 5 creaciones reales de mainnet.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Final

import based58

from mit_pumpfun.decoder import BorshReader, DecodeError

PROGRAM_DATA_PREFIX: Final = "Program data: "


def anchor_event_discriminator(event_name: str) -> bytes:
    """Discriminador de evento Anchor: `sha256("event:<Nombre>")[:8]`."""
    return hashlib.sha256(f"event:{event_name}".encode()).digest()[:8]


# Verificado: el valor observado en mainnet es 1b72a94ddeeb6376.
DISCRIMINATOR_CREATE_EVENT: Final = anchor_event_discriminator("CreateEvent")
DISCRIMINATOR_TRADE_EVENT: Final = anchor_event_discriminator("TradeEvent")


@dataclass(frozen=True, slots=True)
class TradeEvent:
    """Una compra o venta, decodificada del log.

    **Por que del log y no de la instruccion.** Se intento primero decodificar la
    instruccion `buy`/`sell` y salieron dos errores encadenados:

    1. Las operaciones reales llegan como instrucciones ANIDADAS, invocadas desde un router.
       Recorrer solo el nivel superior devolvia cero operaciones. Silencioso.
    2. Los argumentos de la instruccion son `max_sol_cost` y `min_sol_output`: LIMITES, no
       lo ejecutado. Usarlos como importe habria falseado todo el analisis de flujo.

    El evento trae los importes reales y, sobre todo, el `user` verdadero: cuando la
    operacion pasa por un agregador, el pagador de la transaccion es el router, no el trader.
    Atribuir el volumen al router destruiria la deteccion de clusters y wash trading.
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


@dataclass(frozen=True, slots=True)
class CreateEvent:
    """Creacion de token, decodificada directamente del log.

    `user` y `creator` son campos DISTINTOS del evento y no siempre coinciden: se observo
    una discrepancia en 1 de cada 5 creaciones capturadas. `creator` es el que cuenta para
    atribuir historial; `user` es quien ejecuto la transaccion.
    """

    name: str
    symbol: str
    uri: str
    mint: str
    bonding_curve: str
    user: str
    creator: str
    timestamp: int
    virtual_token_reserves: int
    virtual_sol_reserves: int
    real_token_reserves: int
    token_total_supply: int
    # Los ultimos ~74 bytes del evento son constantes en toda la muestra disponible salvo un
    # byte que alterna 0x00/0x01. Sin mas datos no se puede afirmar que representan, asi que
    # se conservan crudos en lugar de inventarles un nombre y un significado.
    trailing: bytes


def iter_program_data(logs: list[str]) -> Iterator[bytes]:
    """Recorre las lineas `Program data:` de un conjunto de logs, ya decodificadas.

    Una linea corrupta se salta en silencio: los logs son datos de terceros y una sola linea
    malformada no debe tumbar la ingesta de todo el bloque.
    """
    for line in logs:
        if not line.startswith(PROGRAM_DATA_PREFIX):
            continue
        payload = line[len(PROGRAM_DATA_PREFIX) :].strip()
        try:
            yield base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError):
            continue


def decode_create_event(raw: bytes) -> CreateEvent:
    """Decodifica un `CreateEvent` ya extraido de su linea de log."""
    if raw[:8] != DISCRIMINATOR_CREATE_EVENT:
        msg = f"discriminador {raw[:8].hex()} no es CreateEvent"
        raise DecodeError(msg)

    reader = BorshReader(raw[8:])
    name = reader.string()
    symbol = reader.string()
    uri = reader.string()
    mint = reader.pubkey()
    bonding_curve = reader.pubkey()
    user = reader.pubkey()
    creator = reader.pubkey()

    def u64() -> int:
        return int.from_bytes(reader.take(8), "little")

    timestamp = u64()
    virtual_token_reserves = u64()
    virtual_sol_reserves = u64()
    real_token_reserves = u64()
    token_total_supply = u64()

    return CreateEvent(
        name=name,
        symbol=symbol,
        uri=uri,
        mint=mint,
        bonding_curve=bonding_curve,
        user=user,
        creator=creator,
        timestamp=timestamp,
        virtual_token_reserves=virtual_token_reserves,
        virtual_sol_reserves=virtual_sol_reserves,
        real_token_reserves=real_token_reserves,
        token_total_supply=token_total_supply,
        trailing=reader.remaining,
    )


def decode_trade_event(raw: bytes) -> TradeEvent:
    """Decodifica un `TradeEvent`. Layout verificado contra mainnet."""
    if raw[:8] != DISCRIMINATOR_TRADE_EVENT:
        msg = f"discriminador {raw[:8].hex()} no es TradeEvent"
        raise DecodeError(msg)

    reader = BorshReader(raw[8:])

    def u64() -> int:
        return int.from_bytes(reader.take(8), "little")

    mint = reader.pubkey()
    sol_amount = u64()
    token_amount = u64()
    is_buy = reader.take(1)[0] == 1
    user = reader.pubkey()
    timestamp = int.from_bytes(reader.take(8), "little", signed=True)
    return TradeEvent(
        mint=mint,
        sol_amount=sol_amount,
        token_amount=token_amount,
        is_buy=is_buy,
        user=user,
        timestamp=timestamp,
        virtual_sol_reserves=u64(),
        virtual_token_reserves=u64(),
    )


def find_trade_events(logs: list[str]) -> list[TradeEvent]:
    """Todas las operaciones de unos logs. Lista vacia si no hay ninguna."""
    events: list[TradeEvent] = []
    for raw in iter_program_data(logs):
        if raw[:8] == DISCRIMINATOR_TRADE_EVENT:
            events.append(decode_trade_event(raw))
    return events


def find_create_event(logs: list[str]) -> CreateEvent | None:
    """Busca el `CreateEvent` en unos logs. `None` si no hay ninguno.

    Devolver `None` es el caso NORMAL: se midieron ~361 eventos por segundo en el programa,
    de los que solo ~25 por minuto son creaciones. El 99,9% del trafico no es una creacion,
    asi que este camino tiene que ser barato y silencioso.
    """
    for raw in iter_program_data(logs):
        if raw[:8] == DISCRIMINATOR_CREATE_EVENT:
            return decode_create_event(raw)
    return None


def looks_like_creation(logs: list[str]) -> bool:
    """Filtro barato previo a decodificar nada.

    Comparar cadenas es varios ordenes de magnitud mas barato que decodificar base64. Con el
    99,9% del trafico siendo compras y ventas, filtrar antes de decodificar es la diferencia
    entre seguir el ritmo del programa y acumular retraso.
    """
    return any(line.startswith("Program log: Instruction: Create") for line in logs)


def encode_pubkey(raw: bytes) -> str:
    """Codifica 32 bytes crudos como direccion base58."""
    return based58.b58encode(raw).decode("ascii")
