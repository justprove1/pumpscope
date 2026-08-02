"""Extrae el mint address de casi cualquier link que pegues."""

import re

# Las direcciones Solana son base58 (sin 0, O, I, l) de 32-44 chars.
_B58 = re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,44}")

# Palabras que aparecen en las URLs y que casualmente pasan el filtro base58.
_STOP = {
    "coin", "board", "advanced", "profile", "solana", "token", "tokens",
    "pump", "pumpswap", "swap", "chart", "account", "address", "latest",
}


def extract_mint(text):
    """Devuelve el mint de un link de pump.fun / dexscreener / solscan / birdeye,
    o del propio mint pegado en crudo.

    Prioriza las direcciones terminadas en 'pump' (marca de los mints de
    pump.fun); si no hay ninguna, coge el candidato base58 mas largo.
    """
    if not text:
        raise ValueError("no se recibio ningun link ni mint")

    text = text.strip().strip('"').strip("'")
    candidates = [c for c in _B58.findall(text) if c.lower() not in _STOP]

    if not candidates:
        raise ValueError(
            "no encontre ninguna direccion valida en: %s\n"
            "   Pega el link del token (https://pump.fun/coin/<mint>) o el mint suelto."
            % text[:120]
        )

    # Un mint acuñado por pump.fun casi siempre termina en 'pump'.
    for c in candidates:
        if c.endswith("pump"):
            return c

    return max(candidates, key=len)
