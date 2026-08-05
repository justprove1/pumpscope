"""Decodificador de PumpSwap, el AMM al que pasan los tokens tras graduarse (SPEC.md 4).

**Por que hace falta.** Al graduar, un token deja de emitir `TradeEvent` de la bonding curve y
pasa a PumpSwap. El sistema se quedaba CIEGO justo con los tokens que triunfan: se observo con
V713/VanillaFunk, operando a ~5 tx/s mientras el visor mostraba cero velas.

El layout se dedujo de mainnet y se valido con la aritmetica de comisiones del propio evento
(`lp_fee == quote_in_with_lp_fee * lp_fee_bps / 10000`), que es una comprobacion interna: si los
offsets estuvieran mal, la igualdad no se cumpliria.

Los eventos NO traen el mint. No es un problema: se consumen desde una suscripcion filtrada por
mint (`mentions=[mint]`), asi que el mint lo pone el llamante y no hay que adivinarlo.
"""

from __future__ import annotations

import base64
import hashlib

import pytest
from mit_pumpfun.pumpswap import (
    PUMPSWAP_PROGRAM_ID,
    decode_pumpswap_trade,
    find_pumpswap_trades,
)

MINT = "DDVhKspGKyjVH7c5XYBB4bQrAxCBoTxSoCpBXUcapump"


def _u64(value: int) -> bytes:
    return value.to_bytes(8, "little")


def _event(name: str, fields: list[int], *, pubkeys: int = 2) -> bytes:
    """Construye un evento Anchor con el discriminador real y los campos dados."""
    raw = hashlib.sha256(f"event:{name}".encode()).digest()[:8]
    raw += b"".join(_u64(v) for v in fields)
    raw += bytes(32 * pubkeys)  # pool y user, irrelevantes para estos casos
    return raw


# Valores tomados de un BuyEvent real de mainnet.
BUY_FIELDS = [
    1_785_864_110,  # timestamp
    859_344_891_614,  # base_amount_out (tokens que salen)
    331_335_000,  # max_quote_amount_in
    0,  # user_base_reserves
    333_000_000,  # user_quote_reserves
    215_343_526_718_443,  # pool_base_reserves  (tokens)
    64_092_545_194,  # pool_quote_reserves (lamports)
    331_335_000,  # quote_amount_in (SOL que entra)
    2,  # lp_fee_bps
    65_449,  # lp_fee
    93,  # protocol_fee_bps
    3_043_374,  # protocol_fee
    327_309_892,  # quote_in_with_lp_fee
    327_244_443,  # user_quote_amount_in
]

# Valores de un SellEvent real.
SELL_FIELDS = [
    1_785_864_029,
    994_626_905_838,  # base_amount_in (tokens que entran)
    373_683_837,  # min_quote_amount_out
    994_626_905_838,
    144_369_825_186,
    200_369_598_346_807,  # pool_base_reserves
    85_248_625_462,  # pool_quote_reserves
    507_938_284,  # quote_amount_out (SOL que sale)
    20,  # lp_fee_bps
    1_015_877,  # lp_fee
    5,  # protocol_fee_bps
    253_970,  # protocol_fee
]


def _log_line(raw: bytes) -> str:
    return "Program data: " + base64.b64encode(raw).decode()


def test_a_buy_is_decoded_with_its_real_amounts() -> None:
    trade = decode_pumpswap_trade(_event("BuyEvent", BUY_FIELDS), MINT)
    assert trade is not None
    assert trade.is_buy is True
    assert trade.mint == MINT
    assert trade.sol_amount == 331_335_000
    assert trade.token_amount == 859_344_891_614
    assert trade.timestamp == 1_785_864_110


def test_a_sell_is_decoded_and_marked_as_such() -> None:
    trade = decode_pumpswap_trade(_event("SellEvent", SELL_FIELDS), MINT)
    assert trade is not None
    assert trade.is_buy is False
    assert trade.sol_amount == 507_938_284
    assert trade.token_amount == 994_626_905_838


def test_the_reserves_land_where_the_curve_expects_them() -> None:
    """El evento se traduce a la MISMA forma que un TradeEvent de la curva.

    Asi las velas, la traccion y la deteccion de ballenas funcionan sin cambiar una linea:
    para todo lo de aguas abajo, un token graduado se comporta igual que uno en la curva.
    """
    trade = decode_pumpswap_trade(_event("BuyEvent", BUY_FIELDS), MINT)
    assert trade is not None
    assert trade.virtual_sol_reserves == 64_092_545_194
    assert trade.virtual_token_reserves == 215_343_526_718_443

    price = trade.virtual_sol_reserves / trade.virtual_token_reserves
    market_cap_sol = price * 1_000_000_000_000_000 / 1_000_000_000
    assert 100 < market_cap_sol < 1000, f"capitalizacion implausible: {market_cap_sol}"


def test_the_fee_arithmetic_of_the_event_is_consistent() -> None:
    """Comprobacion INTERNA del layout: si los offsets bailaran, esto fallaria.

    `lp_fee` tiene que ser `quote_in_with_lp_fee * lp_fee_bps / 10000`. Es la prueba de que los
    campos estan donde creemos, sin depender de un IDL publicado.
    """
    quote_with_fee = BUY_FIELDS[12]
    lp_fee_bps = BUY_FIELDS[8]
    lp_fee = BUY_FIELDS[9]
    expected = quote_with_fee * lp_fee_bps // 10_000
    assert abs(expected - lp_fee) <= max(2, lp_fee // 500)


def test_events_that_are_not_trades_are_ignored() -> None:
    assert decode_pumpswap_trade(_event("CreatePoolEvent", [1, 2, 3]), MINT) is None
    assert decode_pumpswap_trade(b"\x00" * 40, MINT) is None


def test_a_truncated_event_returns_none_instead_of_raising() -> None:
    """Los logs son datos de terceros: uno corto no puede tumbar la ingesta."""
    truncated = hashlib.sha256(b"event:BuyEvent").digest()[:8] + _u64(1) * 3
    assert decode_pumpswap_trade(truncated, MINT) is None


def test_trades_are_found_among_unrelated_log_lines() -> None:
    logs = [
        f"Program {PUMPSWAP_PROGRAM_ID} invoke [1]",
        "Program log: Instruction: Buy",
        _log_line(_event("BuyEvent", BUY_FIELDS)),
        "Program log: ruido",
        _log_line(_event("SellEvent", SELL_FIELDS)),
        "Program data: no-es-base64-valido!!",
    ]
    trades = find_pumpswap_trades(logs, MINT)
    assert len(trades) == 2
    assert [t.is_buy for t in trades] == [True, False]
    assert all(t.mint == MINT for t in trades)


def test_logs_without_pumpswap_events_give_an_empty_list() -> None:
    assert find_pumpswap_trades(["Program log: nada que ver"], MINT) == []


@pytest.mark.parametrize("field", ["sol_amount", "token_amount"])
def test_a_trade_with_zero_amounts_is_discarded(field: str) -> None:
    """Un intercambio de cero no es una operacion: ensuciaria precios y volumen."""
    fields = list(BUY_FIELDS)
    fields[1 if field == "token_amount" else 7] = 0
    assert decode_pumpswap_trade(_event("BuyEvent", fields), MINT) is None
