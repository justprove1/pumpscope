"""Utilidades transversales: tipos, errores y constantes.

Es el unico paquete del que puede depender cualquier otro. No depende de nada del monorepo.
"""

from __future__ import annotations

from mit_shared.errors import ConfigurationError, MitError, ValidationError
from mit_shared.types import (
    LAMPORTS_PER_SOL,
    Lamports,
    MintAddress,
    Slot,
    TxSignature,
    WalletAddress,
    lamports_to_sol,
    sol_to_lamports,
)

__all__ = [
    "LAMPORTS_PER_SOL",
    "ConfigurationError",
    "Lamports",
    "MintAddress",
    "MitError",
    "Slot",
    "TxSignature",
    "ValidationError",
    "WalletAddress",
    "lamports_to_sol",
    "sol_to_lamports",
]

__version__ = "0.1.0"
