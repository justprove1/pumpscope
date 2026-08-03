"""Conocimiento de los programas Pump.fun y PumpSwap.

Decodificacion de instrucciones y cuentas a partir de lectura directa on-chain. NO se basa
en scraping de HTML ni en ninguna API no oficial.

Todo lo de este paquete esta verificado contra transacciones reales de mainnet capturadas
con `infrastructure/scripts/record_pumpfun_fixtures.py`. Si el programa cambia de formato,
los tests fallan contra las fixtures antes de que falle la ingesta en produccion.

El umbral de graduacion se derivara de la invariante de la curva del propio token, en SOL,
no de una constante en dolares (pendiente: Fase 2).
"""

from __future__ import annotations

from mit_pumpfun.constants import (
    CREATE_DISCRIMINATORS,
    CREATE_LOG_PREFIX,
    DISCRIMINATOR_BUY,
    DISCRIMINATOR_CREATE_V2,
    DISCRIMINATOR_SELL,
    GLOBAL_CONFIG,
    MINT_AUTHORITY,
    PUMPFUN_PROGRAM_ID,
    TOKEN_2022_PROGRAM_ID,
    anchor_discriminator,
)
from mit_pumpfun.decoder import (
    DecodeError,
    ParsedInstruction,
    TokenCreation,
    decode_create,
    extract_token_creation,
    iter_instructions,
    resolve_account_keys,
)

__all__ = [
    "CREATE_DISCRIMINATORS",
    "CREATE_LOG_PREFIX",
    "DISCRIMINATOR_BUY",
    "DISCRIMINATOR_CREATE_V2",
    "DISCRIMINATOR_SELL",
    "GLOBAL_CONFIG",
    "MINT_AUTHORITY",
    "PUMPFUN_PROGRAM_ID",
    "TOKEN_2022_PROGRAM_ID",
    "DecodeError",
    "ParsedInstruction",
    "TokenCreation",
    "anchor_discriminator",
    "decode_create",
    "extract_token_creation",
    "iter_instructions",
    "resolve_account_keys",
]
