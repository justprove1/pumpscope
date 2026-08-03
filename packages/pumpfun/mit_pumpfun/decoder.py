"""Decodificacion de instrucciones y cuentas de Pump.fun.

Escrito contra transacciones reales de mainnet, no contra documentacion recordada. Cada
afirmacion de este modulo esta respaldada por una fixture en `tests/fixtures/`.

Dos cosas que se descubrieron mirando datos reales y que un decoder escrito de memoria se
habria comido:

1. **Las transacciones son v0 con Address Lookup Tables.** Los indices de cuenta de una
   instruccion apuntan a una lista que NO es `message.accountKeys`, sino esa concatenada con
   `meta.loadedAddresses`. Sin fusionarlas se lee la cuenta equivocada, o se sale de rango.

2. **El creador viene en el payload, no es el firmante.** En 1 de cada 5 creaciones
   observadas el campo `creator` de la instruccion es distinto del pagador de la
   transaccion. Deducir el creador del firmante es correcto el 80% de las veces, que en un
   sistema que puntua el historial del creador es la peor clase de error: el que casi
   siempre acierta.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Final

import based58

from mit_pumpfun.constants import (
    ACCOUNT_INDEX_ASSOCIATED_BONDING_CURVE,
    ACCOUNT_INDEX_BONDING_CURVE,
    ACCOUNT_INDEX_MINT,
    ACCOUNT_INDEX_USER,
    CREATE_DISCRIMINATORS,
    MIN_CREATE_ACCOUNTS,
    PUMPFUN_PROGRAM_ID,
)

DISCRIMINATOR_LENGTH: Final = 8
PUBKEY_LENGTH: Final = 32
_MAX_BORSH_STRING = 4096  # ni un nombre ni una URI legitimos se acercan


class DecodeError(ValueError):
    """Los bytes no cumplen el formato esperado.

    NO es reintentable: volver a decodificar los mismos bytes da lo mismo. Significa que el
    programa cambio de formato, y eso debe llegar al operador, no perderse en un log.
    """


@dataclass(frozen=True, slots=True)
class ParsedInstruction:
    """Una instruccion ya resuelta contra la lista completa de cuentas."""

    program_id: str
    data: bytes
    accounts: tuple[str, ...]
    stack_height: int

    @property
    def discriminator(self) -> bytes:
        return self.data[:DISCRIMINATOR_LENGTH]


@dataclass(frozen=True, slots=True)
class TokenCreation:
    """Una creacion de token decodificada.

    `creator` sale SIEMPRE del payload de la instruccion. `fee_payer` se conserva aparte
    porque son cosas distintas y confundirlas falsea el historial del creador.
    """

    mint: str
    creator: str
    fee_payer: str
    name: str
    symbol: str
    uri: str
    bonding_curve: str
    associated_bonding_curve: str
    signature: str
    slot: int
    block_time: int | None
    # Los dos ultimos bytes del payload. Se observan valores 0x0100 y 0x0000, pero su
    # significado no esta documentado ni es deducible con la muestra disponible. Se
    # conservan crudos en vez de inventarles un nombre.
    trailing_flags: bytes


class _Reader:
    """Lector Borsh minimo. Solo lo que hace falta: strings y pubkeys."""

    __slots__ = ("_buf", "_pos")

    def __init__(self, buf: bytes) -> None:
        self._buf = buf
        self._pos = 0

    @property
    def remaining(self) -> bytes:
        return self._buf[self._pos :]

    def take(self, count: int) -> bytes:
        end = self._pos + count
        if end > len(self._buf):
            msg = f"payload truncado: se pedian {count} bytes en {self._pos}, hay {len(self._buf)}"
            raise DecodeError(msg)
        chunk = self._buf[self._pos : end]
        self._pos = end
        return chunk

    def string(self) -> str:
        """String Borsh: longitud u32 little-endian + bytes UTF-8."""
        length = int.from_bytes(self.take(4), "little")
        if length > _MAX_BORSH_STRING:
            msg = f"longitud de string implausible: {length}"
            raise DecodeError(msg)
        # `errors="replace"`: los metadatos de un memecoin son texto arbitrario del creador y
        # pueden traer bytes invalidos a proposito. Un token con nombre roto es un dato, no
        # una excepcion que deba tumbar la ingesta.
        return self.take(length).decode("utf-8", errors="replace")

    def pubkey(self) -> str:
        return based58.b58encode(self.take(PUBKEY_LENGTH)).decode("ascii")


def resolve_account_keys(transaction: dict[str, Any]) -> list[str]:
    """Lista COMPLETA de cuentas: las estaticas mas las cargadas por lookup table.

    El orden lo fija el runtime de Solana y no es negociable: estaticas, luego las
    `writable` de la tabla, luego las `readonly`.
    """
    message = transaction.get("transaction", {}).get("message", {})
    static = message.get("accountKeys") or []
    loaded = (transaction.get("meta") or {}).get("loadedAddresses") or {}
    return [*static, *(loaded.get("writable") or []), *(loaded.get("readonly") or [])]


def _decode_data(raw: str) -> bytes:
    try:
        return bytes(based58.b58decode(raw.encode("ascii")))
    except Exception as exc:  # based58 no expone un tipo de excepcion estable
        msg = f"datos de instruccion no son base58 valido: {exc}"
        raise DecodeError(msg) from exc


def iter_instructions(
    transaction: dict[str, Any], program_id: str = PUMPFUN_PROGRAM_ID
) -> Iterator[ParsedInstruction]:
    """Recorre las instrucciones de un programa, de nivel superior y anidadas.

    Se recorren tambien las internas (`innerInstructions`) porque una creacion puede llegar
    invocada desde otro programa —un router o un agregador— y solo mirar el nivel superior
    la perderia.
    """
    keys = resolve_account_keys(transaction)
    message = transaction.get("transaction", {}).get("message", {})

    def build(instruction: dict[str, Any], stack_height: int) -> ParsedInstruction | None:
        index = instruction.get("programIdIndex")
        if index is None or index >= len(keys) or keys[index] != program_id:
            return None
        accounts = tuple(keys[i] for i in instruction.get("accounts", []) if i < len(keys))
        return ParsedInstruction(
            program_id=program_id,
            data=_decode_data(instruction.get("data", "")),
            accounts=accounts,
            stack_height=stack_height,
        )

    for instruction in message.get("instructions") or []:
        parsed = build(instruction, 1)
        if parsed is not None:
            yield parsed

    for group in (transaction.get("meta") or {}).get("innerInstructions") or []:
        for instruction in group.get("instructions") or []:
            parsed = build(instruction, instruction.get("stackHeight") or 2)
            if parsed is not None:
                yield parsed


def decode_create(instruction: ParsedInstruction) -> tuple[str, str, str, str, bytes]:
    """Decodifica el payload de `create_v2`: name, symbol, uri, creator y bytes de cola."""
    if instruction.discriminator not in CREATE_DISCRIMINATORS:
        msg = f"discriminador {instruction.discriminator.hex()} no es una creacion"
        raise DecodeError(msg)

    reader = _Reader(instruction.data[DISCRIMINATOR_LENGTH:])
    name = reader.string()
    symbol = reader.string()
    uri = reader.string()
    creator = reader.pubkey()
    return name, symbol, uri, creator, reader.remaining


def extract_token_creation(
    transaction: dict[str, Any], signature: str | None = None
) -> TokenCreation | None:
    """Extrae la creacion de token de una transaccion, o `None` si no hay ninguna.

    Devuelve `None` en vez de lanzar cuando la transaccion simplemente no es una creacion:
    el detector procesa miles de transacciones por segundo y la inmensa mayoria son compras
    y ventas. Solo se lanza `DecodeError` cuando SI es una creacion pero su formato no
    cuadra, que es la senal de que el programa ha cambiado.
    """
    if (transaction.get("meta") or {}).get("err") is not None:
        return None

    for instruction in iter_instructions(transaction):
        if instruction.discriminator not in CREATE_DISCRIMINATORS:
            continue
        if len(instruction.accounts) < MIN_CREATE_ACCOUNTS:
            msg = (
                f"create con {len(instruction.accounts)} cuentas, "
                f"se esperaban al menos {MIN_CREATE_ACCOUNTS}"
            )
            raise DecodeError(msg)

        name, symbol, uri, creator, trailing = decode_create(instruction)
        message = transaction.get("transaction", {}).get("message", {})
        static_keys = message.get("accountKeys") or []

        return TokenCreation(
            mint=instruction.accounts[ACCOUNT_INDEX_MINT],
            creator=creator,
            fee_payer=static_keys[0] if static_keys else "",
            name=name,
            symbol=symbol,
            uri=uri,
            bonding_curve=instruction.accounts[ACCOUNT_INDEX_BONDING_CURVE],
            associated_bonding_curve=instruction.accounts[ACCOUNT_INDEX_ASSOCIATED_BONDING_CURVE],
            signature=signature
            or (transaction.get("transaction", {}).get("signatures") or [""])[0],
            slot=int(transaction.get("slot", 0)),
            block_time=transaction.get("blockTime"),
            trailing_flags=trailing,
        )
    return None


def user_account(instruction: ParsedInstruction) -> str | None:
    """Cuenta `user` de la instruccion, si esta presente."""
    if len(instruction.accounts) > ACCOUNT_INDEX_USER:
        return instruction.accounts[ACCOUNT_INDEX_USER]
    return None
