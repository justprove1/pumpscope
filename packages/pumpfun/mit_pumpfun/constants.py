"""Constantes del programa Pump.fun, todas verificadas contra mainnet.

Ninguna de estas direcciones esta escrita de memoria. Todas salen de transacciones reales
capturadas con `infrastructure/scripts/record_pumpfun_fixtures.py`, y hay tests que las
comprueban contra las fixtures.

Los discriminadores NO se escriben a mano: se derivan igual que lo hace Anchor,
`sha256("global:<nombre>")[:8]`. Asi no puede haber una errata en una constante hexadecimal,
y anadir una instruccion nueva es escribir su nombre.
"""

from __future__ import annotations

import hashlib
from typing import Final

# Verificado on-chain: cuenta ejecutable, propiedad de BPFLoaderUpgradeable.
PUMPFUN_PROGRAM_ID: Final = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

# Pump.fun usa Token-2022 con extension MetadataPointer, NO el SPL Token clasico.
# Confundirlos hace que el parseo del mint falle en silencio.
TOKEN_2022_PROGRAM_ID: Final = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"  # noqa: S105
ASSOCIATED_TOKEN_PROGRAM_ID: Final = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"  # noqa: S105
SYSTEM_PROGRAM_ID: Final = "11111111111111111111111111111111"

# Cuentas constantes en toda instruccion create_v2 (observado en el 100% de las fixtures).
MINT_AUTHORITY: Final = "TSLvdd1pWpHVjahSpsvCXUbgwsL3JAcvokwaKt1eokM"
GLOBAL_CONFIG: Final = "4wTV1YmiEkRvAtNtsSGPtUrqRYQMe5SKy2uB4Jjaxnjf"


def anchor_discriminator(instruction_name: str) -> bytes:
    """Discriminador Anchor de una instruccion: `sha256("global:<nombre>")[:8]`."""
    return hashlib.sha256(f"global:{instruction_name}".encode()).digest()[:8]


# La instruccion de creacion vigente es `create_v2`, no `create`. Se comprobo contra
# transacciones reales: el discriminador observado es d6904cec5f8b31b4, que coincide
# exactamente con sha256("global:create_v2")[:8].
DISCRIMINATOR_CREATE_V2: Final = anchor_discriminator("create_v2")
DISCRIMINATOR_CREATE: Final = anchor_discriminator("create")
DISCRIMINATOR_BUY: Final = anchor_discriminator("buy")
DISCRIMINATOR_SELL: Final = anchor_discriminator("sell")

# Se aceptan ambas versiones de la creacion: la v1 puede seguir apareciendo en historico, y
# tratarla como desconocida perderia tokens antiguos al reconstruir el pasado.
CREATE_DISCRIMINATORS: Final = frozenset({DISCRIMINATOR_CREATE_V2, DISCRIMINATOR_CREATE})

# Posiciones de cuenta dentro de create_v2, verificadas sobre fixtures reales.
ACCOUNT_INDEX_MINT: Final = 0
ACCOUNT_INDEX_MINT_AUTHORITY: Final = 1
ACCOUNT_INDEX_BONDING_CURVE: Final = 2
ACCOUNT_INDEX_ASSOCIATED_BONDING_CURVE: Final = 3
ACCOUNT_INDEX_GLOBAL: Final = 4
ACCOUNT_INDEX_USER: Final = 5
MIN_CREATE_ACCOUNTS: Final = 6

# Prefijo con el que el programa anuncia la instruccion en los logs. Se compara por prefijo a
# proposito: hoy emite `CreateV2` y una version futura podria emitir `CreateV3`. Perder
# creaciones en silencio es peor que capturar de mas y filtrar por discriminador despues.
CREATE_LOG_PREFIX: Final = "Program log: Instruction: Create"


# Cuentas del PROPIO protocolo, observadas como constantes en toda instruccion create_v2.
# NO son traders y hay que excluirlas del analisis de wallets: si no, el detector de
# self-trading acusa a una cuenta de sistema de concentrar la mitad del volumen. Es el
# mismo error que incluir el pool al medir concentracion de holders, y produce el peor
# tipo de falso positivo: uno que aparece en casi todos los tokens.
PROTOCOL_ACCOUNTS: Final[frozenset[str]] = frozenset(
    {
        PUMPFUN_PROGRAM_ID,
        MINT_AUTHORITY,
        GLOBAL_CONFIG,
        SYSTEM_PROGRAM_ID,
        "MAyhSmzXzV1pTf7LsNkrNwkWKTo4ougAJ1PPg47MD4e",
        "13ec7XdrjF3h3YcqBTFDSReRcUFwbCnJaAQspM4j6DDJ",
        "BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s",
        "Ce6TQqeHC9p8KetsN6JsjHK7UTZk7nasjjnr7XxXp9F1",
    }
)
