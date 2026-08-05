"""Deteccion de graduaciones de Pump.fun (la instruccion `migrate`).

**Que es una graduacion.** Cuando la bonding curve se completa, el token deja de operar en
ella y pasa a un pool de PumpSwap. Eso ocurre en una instruccion concreta —`migrate` o
`migrate_v2`— y no es un proceso gradual: hay un slot exacto antes del cual el token se
compra en la curva y despues del cual ya no.

**Por que hace falta detectarlo bien.** Se intento deducir por la forma de los logs, viendo
si aparecian operaciones con pinta de PumpSwap. Comparado contra el estado real de la curva,
eso fallaba en 6 de cada 10 tokens, y los falsos positivos son los caros: el panel se negaba
a operar tokens perfectamente vivos.

Aqui hay dos vias, y se usan para cosas distintas:

- `find_graduations`: decodifica la instruccion de una transaccion. Es exacta y dice QUE
  token graduo y en que slot. Sirve para enterarse en el momento.
- `mentions_graduation`: mira solo los logs. Es barata y sirve de primer filtro sobre un
  flujo en vivo, pero un log no identifica el mint por si solo.

Nada de esto sustituye a mirar el campo `complete` de la cuenta de la curva, que es la
verdad de referencia. Esto sirve para saberlo ANTES, no en lugar de.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from mit_pumpfun.constants import PUMPFUN_PROGRAM_ID, anchor_discriminator
from mit_pumpfun.decoder import DecodeError, iter_instructions

DISCRIMINATOR_MIGRATE: Final = anchor_discriminator("migrate")
DISCRIMINATOR_MIGRATE_V2: Final = anchor_discriminator("migrate_v2")

MIGRATE_DISCRIMINATORS: Final = {
    DISCRIMINATOR_MIGRATE: "migrate",
    DISCRIMINATOR_MIGRATE_V2: "migrate_v2",
}

# El programa anuncia la instruccion asi. Se compara por PREFIJO a proposito: hoy emite
# `Migrate` y `MigrateV2`, y una version futura podria emitir `MigrateV3`. Perder
# graduaciones en silencio es peor que capturar de mas y filtrar por discriminador despues.
MIGRATE_LOG_PREFIX: Final = "Program log: Instruction: Migrate"

# Posicion del mint que graduo dentro de la instruccion. Es la 2 tanto en `migrate` (donde el
# campo se llama `mint`) como en `migrate_v2` (donde se llama `base_mint`), verificado contra
# el IDL on-chain y contra graduaciones reales de mainnet.
ACCOUNT_INDEX_MIGRATED_MINT: Final = 2

# Minimo de cuentas para que la instruccion tenga sentido. El programa desplegado manda mas
# de las que declara su IDL —29 observadas frente a 27— asi que se comprueba un minimo, no
# una cifra exacta: exigir el numero justo romperia en cuanto añadan una cuenta.
_MIN_MIGRATE_ACCOUNTS: Final = ACCOUNT_INDEX_MIGRATED_MINT + 1


@dataclass(frozen=True, slots=True)
class Graduation:
    """Un token que ha completado su curva y pasa a operar en PumpSwap."""

    mint: str
    instruction: str
    slot: int
    signature: str
    block_time: int | None

    @property
    def is_v2(self) -> bool:
        return self.instruction == "migrate_v2"


def mentions_graduation(logs: list[str]) -> bool:
    """¿Hay una graduacion en estos logs?

    Filtro barato para un flujo en vivo. Dice que ALGO graduo, no cual: los logs no traen el
    mint. Para saberlo hay que decodificar la transaccion con `find_graduations`.
    """
    return any(line.startswith(MIGRATE_LOG_PREFIX) for line in logs)


def find_graduations(transaction: dict[str, Any]) -> list[Graduation]:
    """Graduaciones dentro de una transaccion ya obtenida del RPC.

    Se recorren tambien las instrucciones internas: una migracion puede llegar invocada desde
    otro programa, y mirar solo el nivel superior la perderia.
    """
    slot = int(transaction.get("slot") or 0)
    block_time = transaction.get("blockTime")
    signature = ""
    firmas = (transaction.get("transaction") or {}).get("signatures") or []
    if firmas:
        signature = str(firmas[0])

    try:
        instrucciones = list(iter_instructions(transaction, PUMPFUN_PROGRAM_ID))
    except DecodeError:
        # Una instruccion con datos ilegibles no puede invalidar el resto de la transaccion.
        return []

    salida: list[Graduation] = []
    for parsed in instrucciones:
        nombre = MIGRATE_DISCRIMINATORS.get(parsed.discriminator)
        if nombre is None or len(parsed.accounts) < _MIN_MIGRATE_ACCOUNTS:
            continue
        salida.append(
            Graduation(
                mint=parsed.accounts[ACCOUNT_INDEX_MIGRATED_MINT],
                instruction=nombre,
                slot=slot,
                signature=signature,
                block_time=int(block_time) if block_time is not None else None,
            )
        )
    return salida
