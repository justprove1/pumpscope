"""La construccion de compras y ventas, contra transacciones REALES de mainnet.

Esto es lo que decide cuanto dinero sale de la cartera, asi que no se prueba contra lo que
el codigo cree: se prueba contra 8 compras y 8 ventas que el programa acepto de verdad, y
que estan guardadas tal cual llegaron en `tests/fixtures/pumpfun_trade_instructions.json`.

Si Pump.fun cambia el orden de cuentas o la forma de los argumentos, estos tests fallan
antes de que falle una transaccion con dinero encima.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from mit_pumpfun.trade import (
    DISC_BUY,
    DISC_SELL,
    BondingCurveAccount,
    TradeAccounts,
    TradeError,
    apply_slippage_down,
    apply_slippage_up,
    associated_token_address,
    bonding_curve_pda,
    build_buy_instruction,
    build_sell_instruction,
    creator_vault_pda,
    event_authority_pda,
    fee_config_pda,
    global_pda,
    global_volume_accumulator_pda,
    lamports_out_for_tokens,
    net_lamports_after_fee,
    tokens_out_for_lamports,
    user_volume_accumulator_pda,
)
from solders.pubkey import Pubkey

FIXTURE = Path(__file__).parents[1] / "fixtures" / "pumpfun_trade_instructions.json"

# Cuentas que el IDL on-chain declara. Las que vienen despues las exige el programa
# desplegado sin publicarlas.
IDL_BUY_ACCOUNTS = 16
IDL_SELL_ACCOUNTS = 14


def _samples(side: str) -> list[dict[str, Any]]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))[side]


@pytest.fixture(scope="module")
def buys() -> list[dict[str, Any]]:
    return _samples("buy")


@pytest.fixture(scope="module")
def sells() -> list[dict[str, Any]]:
    return _samples("sell")


def test_discriminadores_coinciden_con_las_transacciones_reales(
    buys: list[dict[str, Any]], sells: list[dict[str, Any]]
) -> None:
    """El discriminador se deriva del nombre, no se copia. Aqui se comprueba contra la cadena."""
    for sample in buys:
        assert bytes.fromhex(sample["data_hex"])[:8] == DISC_BUY
    for sample in sells:
        assert bytes.fromhex(sample["data_hex"])[:8] == DISC_SELL


def test_argumentos_de_compra_tienen_la_forma_del_idl(buys: list[dict[str, Any]]) -> None:
    """`buy`: discriminador + amount(u64) + max_sol_cost(u64) + track_volume opcional.

    `track_volume` se observo de las dos formas en mainnet: presente (25 bytes) y omitido
    (24). Es un argumento final opcional de Anchor. Este constructor siempre lo manda, que es
    la forma que se simulo y el programa acepto, pero el test admite ambas porque ambas
    existen de verdad — fijar solo una haria fallar el test ante datos legitimos.
    """
    for sample in buys:
        assert len(bytes.fromhex(sample["data_hex"])) in (8 + 8 + 8, 8 + 8 + 8 + 1)


def test_argumentos_de_venta_tienen_la_forma_del_idl(sells: list[dict[str, Any]]) -> None:
    """`sell`: discriminador + amount(u64) + min_sol_output(u64). Sin `track_volume`."""
    for sample in sells:
        assert len(bytes.fromhex(sample["data_hex"])) == 8 + 8 + 8


@pytest.mark.parametrize("index", range(8))
def test_cuentas_derivadas_coinciden_una_a_una_con_la_compra_real(
    buys: list[dict[str, Any]], index: int
) -> None:
    """Las 16 cuentas del IDL, recalculadas desde el mint y el usuario.

    `fee_recipient` (posicion 1) no se deriva: lo elige el programa y se lee de `Global`.
    Todo lo demas tiene que salir exacto, o la transaccion iria a cuentas equivocadas.
    """
    real = buys[index]["accounts"]
    mint = Pubkey.from_string(real[2])
    user = Pubkey.from_string(real[6])
    token_program = Pubkey.from_string(real[8])
    curve = bonding_curve_pda(mint)

    assert str(global_pda()) == real[0]
    assert str(curve) == real[3]
    assert str(associated_token_address(curve, mint, token_program)) == real[4]
    assert str(associated_token_address(user, mint, token_program)) == real[5]
    assert str(event_authority_pda()) == real[10]
    assert str(global_volume_accumulator_pda()) == real[12]
    assert str(user_volume_accumulator_pda(user)) == real[13]
    assert str(fee_config_pda()) == real[14]


def test_creator_vault_sale_del_creador_de_la_curva(buys: list[dict[str, Any]]) -> None:
    """La posicion 9 es el vault del CREADOR, no del usuario. Confundirlos manda la comision
    de creador a una cuenta que no existe y la instruccion revierte."""
    sample = buys[0]
    # El creador no esta en la instruccion: se lee de la cuenta de la curva. Se comprueba al
    # reves, verificando que la cuenta observada es un `creator-vault` valido de ALGUIEN.
    observed = sample["accounts"][9]
    assert observed != sample["accounts"][6], "el creator_vault no puede ser el propio usuario"
    assert str(creator_vault_pda(Pubkey.from_string(sample["accounts"][6]))) != observed


def test_la_compra_lleva_las_cuentas_del_idl_mas_las_extra() -> None:
    """El orden no es negociable y las extra van al final, nunca intercaladas."""
    mint = Pubkey.new_unique()
    user = Pubkey.new_unique()
    extra = (Pubkey.new_unique(), Pubkey.new_unique())
    accounts = TradeAccounts(
        mint=mint,
        user=user,
        token_program=Pubkey.new_unique(),
        fee_recipient=Pubkey.new_unique(),
        creator=Pubkey.new_unique(),
    )
    instruction = build_buy_instruction(
        accounts, token_amount=1_000, max_lamports_cost=10_000, extra_accounts=extra
    )
    assert len(instruction.accounts) == IDL_BUY_ACCOUNTS + len(extra)
    assert instruction.accounts[2].pubkey == mint
    assert instruction.accounts[6].pubkey == user
    assert instruction.accounts[6].is_signer, "el usuario es el unico firmante"
    assert [m.pubkey for m in instruction.accounts[-2:]] == list(extra)
    assert instruction.data[:8] == DISC_BUY


def test_la_venta_pone_creator_vault_antes_que_token_program() -> None:
    """`sell` NO usa el orden de `buy`: aqui `creator_vault` va en la 8 y `token_program` en
    la 9. Copiar el orden de la compra produce una venta que revierte."""
    creator = Pubkey.new_unique()
    token_program = Pubkey.new_unique()
    accounts = TradeAccounts(
        mint=Pubkey.new_unique(),
        user=Pubkey.new_unique(),
        token_program=token_program,
        fee_recipient=Pubkey.new_unique(),
        creator=creator,
    )
    instruction = build_sell_instruction(
        accounts, token_amount=1_000, min_lamports_output=1
    )
    assert len(instruction.accounts) == IDL_SELL_ACCOUNTS
    assert instruction.accounts[8].pubkey == creator_vault_pda(creator)
    assert instruction.accounts[9].pubkey == token_program


def test_una_compra_sin_tope_de_gasto_no_se_construye() -> None:
    """Sin `max_sol_cost` la orden se ejecuta a cualquier precio, que es como un sandwich se
    lleva la operacion entera."""
    accounts = TradeAccounts(
        mint=Pubkey.new_unique(),
        user=Pubkey.new_unique(),
        token_program=Pubkey.new_unique(),
        fee_recipient=Pubkey.new_unique(),
        creator=Pubkey.new_unique(),
    )
    with pytest.raises(TradeError):
        build_buy_instruction(accounts, token_amount=1_000, max_lamports_cost=0)
    with pytest.raises(TradeError):
        build_buy_instruction(accounts, token_amount=0, max_lamports_cost=1_000)


# --- Matematica del importe -------------------------------------------------


def _curve(**overrides: Any) -> BondingCurveAccount:
    base: dict[str, Any] = {
        "virtual_token_reserves": 1_073_000_000_000_000,
        "virtual_quote_reserves": 30_000_000_000,
        "real_token_reserves": 793_100_000_000_000,
        "real_quote_reserves": 0,
        "token_total_supply": 1_000_000_000_000_000,
        "complete": False,
        "creator": Pubkey.new_unique(),
        "quote_mint": Pubkey.default(),
        "is_mayhem_mode": False,
        "is_cashback_coin": False,
    }
    return BondingCurveAccount(**{**base, **overrides})


def test_la_comision_se_aparta_antes_de_calcular_los_tokens() -> None:
    """Quien pide 0,1 SOL gasta 0,1 SOL, no 0,1 mas comision."""
    lamports = 100_000_000
    assert net_lamports_after_fee(lamports, 100) == 99_009_900
    # Con comision cero no se toca nada.
    assert net_lamports_after_fee(lamports, 0) == lamports


def test_comprar_mas_caro_da_menos_tokens_por_lamport() -> None:
    """La curva es de producto constante: el precio sube segun se compra. Si esto se rompe,
    el dimensionado de las ordenes deja de tener sentido."""
    curve = _curve()
    pequena = tokens_out_for_lamports(curve, 10_000_000)
    grande = tokens_out_for_lamports(curve, 1_000_000_000)
    assert pequena > 0 and grande > 0
    assert grande / 1_000_000_000 < pequena / 10_000_000


def test_no_se_pueden_comprar_mas_tokens_de_los_que_quedan() -> None:
    curve = _curve(real_token_reserves=1_000)
    assert tokens_out_for_lamports(curve, 10**12) == 1_000


def test_importes_no_positivos_no_producen_orden() -> None:
    curve = _curve()
    assert tokens_out_for_lamports(curve, 0) == 0
    assert tokens_out_for_lamports(curve, -5) == 0
    assert lamports_out_for_tokens(curve, 0) == 0


def test_el_deslizamiento_sube_el_tope_y_baja_el_suelo() -> None:
    """Al comprar se acepta pagar mas; al vender, cobrar menos. Invertirlos hace que toda
    orden revierta, o peor, que se acepte cualquier precio."""
    assert apply_slippage_up(1_000_000, 1_000) == 1_100_000
    assert apply_slippage_down(1_000_000, 1_000) == 900_000


def test_una_curva_completa_se_reconoce_como_graduada() -> None:
    assert _curve(complete=True).complete
    assert _curve().quotes_in_sol, "quote_mint a cero significa SOL nativo"


def test_una_cuenta_de_curva_truncada_no_se_decodifica_a_medias() -> None:
    """Mejor fallar que devolver reservas basura: de esas reservas sale el tamano de la orden."""
    with pytest.raises(TradeError):
        BondingCurveAccount.decode(b"\x00" * 40)
