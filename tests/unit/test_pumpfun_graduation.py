"""Deteccion de graduaciones, contra migraciones REALES de mainnet.

La fixture son transacciones `migrate_v2` capturadas sin retocar. Si Pump.fun cambia el orden
de cuentas o el nombre de la instruccion, estos tests fallan antes de que el panel empiece a
bloquear compras de tokens vivos —que es exactamente el fallo que este modulo vino a corregir.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from mit_pumpfun.graduation import (
    DISCRIMINATOR_MIGRATE,
    DISCRIMINATOR_MIGRATE_V2,
    MIGRATE_LOG_PREFIX,
    find_graduations,
    mentions_graduation,
)

FIXTURE = Path(__file__).parents[1] / "fixtures" / "pumpfun_graduations.json"


@pytest.fixture(scope="module")
def graduaciones() -> list[dict[str, Any]]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["graduaciones"]


def test_los_discriminadores_se_derivan_del_nombre() -> None:
    """No se copian a mano: se derivan igual que lo hace Anchor."""
    assert DISCRIMINATOR_MIGRATE.hex() == "9beae792ec9ea21e"
    assert DISCRIMINATOR_MIGRATE_V2.hex() == "bbcb121fceedfe29"


def test_encuentra_el_mint_que_graduo_en_cada_transaccion_real(
    graduaciones: list[dict[str, Any]],
) -> None:
    """El dato que importa: QUE token graduo. Se compara con el observado en la cadena."""
    assert graduaciones, "la fixture no tiene graduaciones"
    for caso in graduaciones:
        encontradas = find_graduations(caso["transaction"])
        assert encontradas, f"no se detecto la graduacion de {caso['signature'][:16]}"
        assert encontradas[0].mint == caso["esperado_mint"]
        assert encontradas[0].instruction == caso["instruccion"]


def test_la_graduacion_trae_su_slot_y_su_firma(graduaciones: list[dict[str, Any]]) -> None:
    """Sin slot no se puede ordenar en el tiempo, y sin firma no se puede ir a verla."""
    for caso in graduaciones:
        g = find_graduations(caso["transaction"])[0]
        assert g.slot > 0
        assert g.signature == caso["signature"]
        assert g.is_v2 == (caso["instruccion"] == "migrate_v2")


def test_los_logs_reales_delatan_la_graduacion(graduaciones: list[dict[str, Any]]) -> None:
    """El filtro barato tiene que dispararse con los logs de verdad."""
    for caso in graduaciones:
        logs = (caso["transaction"].get("meta") or {}).get("logMessages") or []
        assert mentions_graduation(logs)


def test_el_prefijo_de_log_cubre_las_dos_versiones() -> None:
    """Se compara por prefijo para que una `MigrateV3` futura no pase desapercibida."""
    assert mentions_graduation(["Program log: Instruction: Migrate"])
    assert mentions_graduation(["Program log: Instruction: MigrateV2"])
    assert mentions_graduation(["Program log: Instruction: MigrateV3"])
    assert MIGRATE_LOG_PREFIX in "Program log: Instruction: MigrateV2"


def test_una_compra_normal_no_se_toma_por_graduacion(
    graduaciones: list[dict[str, Any]],
) -> None:
    """El falso positivo es el error caro: bloquea la compra de un token que esta vivo."""
    compras = json.loads(
        (FIXTURE.parent / "pumpfun_trade_instructions.json").read_text(encoding="utf-8")
    )["buy"]
    assert compras
    for _ in compras:
        logs = ["Program log: Instruction: Buy", "Program log: sol_received: 1"]
        assert not mentions_graduation(logs)
    # Y una transaccion sin instrucciones de Pump.fun tampoco.
    assert find_graduations({"slot": 1, "transaction": {"message": {"instructions": []}}}) == []


def test_una_transaccion_vacia_no_revienta() -> None:
    """Lo que llega del RPC puede venir incompleto; eso no puede tumbar al detector."""
    assert find_graduations({}) == []
    assert find_graduations({"transaction": {}}) == []
    assert mentions_graduation([]) is False
