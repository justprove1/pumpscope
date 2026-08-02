"""Tipos base del dominio.

Dos decisiones que se aplican en todo el sistema:

1. **El dinero nunca es `float`.** Los importes en SOL son `Decimal`; las cantidades exactas
   de la cadena son `int` de lamports. Un `float` acumula error de redondeo y en una
   reconciliacion contra el estado on-chain eso significa descuadre.

2. **Las direcciones son tipos distintos entre si.** `MintAddress` y `WalletAddress` son
   ambos cadenas base58, pero confundirlos es un error real y frecuente. Con `NewType`, mypy
   lo detecta en tiempo de analisis y no cuesta nada en ejecucion.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Final, NewType

# --- Identificadores de Solana --------------------------------------------------------------
MintAddress = NewType("MintAddress", str)
WalletAddress = NewType("WalletAddress", str)
ProgramId = NewType("ProgramId", str)
TxSignature = NewType("TxSignature", str)
Slot = NewType("Slot", int)

# --- Dinero ---------------------------------------------------------------------------------
Lamports = NewType("Lamports", int)
BasisPoints = NewType("BasisPoints", int)

LAMPORTS_PER_SOL: Final[int] = 1_000_000_000
_LAMPORTS_PER_SOL_DECIMAL: Final[Decimal] = Decimal(LAMPORTS_PER_SOL)


def lamports_to_sol(lamports: Lamports) -> Decimal:
    """Convierte lamports a SOL sin perder precision."""
    return Decimal(int(lamports)) / _LAMPORTS_PER_SOL_DECIMAL


def sol_to_lamports(sol: Decimal) -> Lamports:
    """Convierte SOL a lamports.

    Trunca hacia cero: nunca se redondea al alza un importe a gastar.
    """
    return Lamports(int(sol * _LAMPORTS_PER_SOL_DECIMAL))
