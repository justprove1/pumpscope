"""Construccion de las instrucciones `buy` y `sell` de Pump.fun.

**Nada aqui esta escrito de memoria.** El orden de cuentas, las semillas de cada PDA y la
forma de los argumentos salen del IDL Anchor publicado on-chain por el propio programa
(cuenta `AYgC53tU5BbP2NAnv5nConJxAdpQZctvmZK88pu69xRs`, derivada como
`create_with_seed(find_program_address([], programa), "anchor:idl", programa)`).

La derivacion se valido ademas contra 8 compras reales de mainnet: las 16 cuentas de `buy`
recalculadas desde el mint y el usuario coincidieron una a una con las que la transaccion
llevaba de verdad. El test `tests/unit/test_pumpfun_trade.py` lo comprueba contra la fixture.

El unico dato que NO se deriva es `fee_recipient`: hay que leerlo de la cuenta `Global`,
porque el programa lo elige y cambia con el tiempo.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from solders.instruction import AccountMeta, Instruction
from solders.pubkey import Pubkey

from mit_pumpfun.constants import (
    ASSOCIATED_TOKEN_PROGRAM_ID,
    PUMPFUN_PROGRAM_ID,
    SYSTEM_PROGRAM_ID,
    anchor_discriminator,
)

# Del IDL: `fee_program` es una direccion fija declarada en la propia definicion de la
# instruccion, no un PDA.
FEE_PROGRAM_ID: Final = "pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ"

# Mint envuelto de SOL. La curva puede cotizar contra otro `quote_mint`; este modulo solo
# sabe operar contra SOL y lo comprueba antes de construir nada.
WRAPPED_SOL_MINT: Final = "So11111111111111111111111111111111111111112"

PUMPFUN_PROGRAM: Final = Pubkey.from_string(PUMPFUN_PROGRAM_ID)
FEE_PROGRAM: Final = Pubkey.from_string(FEE_PROGRAM_ID)
SYSTEM_PROGRAM: Final = Pubkey.from_string(SYSTEM_PROGRAM_ID)
ATA_PROGRAM: Final = Pubkey.from_string(ASSOCIATED_TOKEN_PROGRAM_ID)

DISC_BUY: Final = anchor_discriminator("buy")
DISC_SELL: Final = anchor_discriminator("sell")

# Anchor: 8 bytes de discriminador delante de los campos de toda cuenta.
_DISCRIMINATOR_LEN: Final = 8

# Tamano minimo de la cuenta de curva para poder leer hasta `quote_mint` incluido:
# discriminador + 5 u64 + `complete` + `creator` + 2 bool + `quote_mint`.
_CURVE_MIN_LEN: Final = _DISCRIMINATOR_LEN + 8 * 5 + 1 + 32 + 1 + 1 + 32


class TradeError(RuntimeError):
    """No se puede construir la operacion con los datos disponibles."""


# ---------------------------------------------------------------------------
# PDAs — semillas tomadas literalmente del IDL
# ---------------------------------------------------------------------------


def _pda(seeds: list[bytes], program: Pubkey = PUMPFUN_PROGRAM) -> Pubkey:
    return Pubkey.find_program_address(seeds, program)[0]


def global_pda() -> Pubkey:
    return _pda([b"global"])


def event_authority_pda() -> Pubkey:
    return _pda([b"__event_authority"])


def bonding_curve_pda(mint: Pubkey) -> Pubkey:
    return _pda([b"bonding-curve", bytes(mint)])


def creator_vault_pda(creator: Pubkey) -> Pubkey:
    return _pda([b"creator-vault", bytes(creator)])


def global_volume_accumulator_pda() -> Pubkey:
    return _pda([b"global_volume_accumulator"])


def user_volume_accumulator_pda(user: Pubkey) -> Pubkey:
    return _pda([b"user_volume_accumulator", bytes(user)])


def fee_config_pda() -> Pubkey:
    """La segunda semilla es la direccion del programa Pump.fun, segun el IDL."""
    return _pda([b"fee_config", bytes(PUMPFUN_PROGRAM)], FEE_PROGRAM)


def associated_token_address(owner: Pubkey, mint: Pubkey, token_program: Pubkey) -> Pubkey:
    """ATA estandar. `token_program` importa: Pump.fun usa Token-2022, no el SPL clasico."""
    return Pubkey.find_program_address(
        [bytes(owner), bytes(token_program), bytes(mint)], ATA_PROGRAM
    )[0]


# ---------------------------------------------------------------------------
# Cuentas on-chain que hay que leer
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BondingCurveAccount:
    """Estado de la curva tal como lo guarda el programa.

    Offsets derivados del orden de campos del IDL, detras del discriminador de 8 bytes:
    `virtual_token_reserves`, `virtual_quote_reserves`, `real_token_reserves`,
    `real_quote_reserves`, `token_total_supply` (u64 cada uno), `complete` (bool),
    `creator` (pubkey), `is_mayhem_mode`, `is_cashback_coin` (bool), `quote_mint` (pubkey).
    """

    virtual_token_reserves: int
    virtual_quote_reserves: int
    real_token_reserves: int
    real_quote_reserves: int
    token_total_supply: int
    complete: bool
    creator: Pubkey
    quote_mint: Pubkey
    is_mayhem_mode: bool
    is_cashback_coin: bool

    @classmethod
    def decode(cls, data: bytes) -> BondingCurveAccount:
        if len(data) < _CURVE_MIN_LEN:
            msg = f"cuenta de curva demasiado corta: {len(data)} bytes, minimo {_CURVE_MIN_LEN}"
            raise TradeError(msg)

        def u64(offset: int) -> int:
            return int.from_bytes(data[offset : offset + 8], "little")

        base = _DISCRIMINATOR_LEN
        creator_at = base + 8 * 5 + 1
        quote_mint_at = creator_at + 32 + 1 + 1
        return cls(
            virtual_token_reserves=u64(base),
            virtual_quote_reserves=u64(base + 8),
            real_token_reserves=u64(base + 16),
            real_quote_reserves=u64(base + 24),
            token_total_supply=u64(base + 32),
            complete=bool(data[base + 40]),
            creator=Pubkey(data[creator_at : creator_at + 32]),
            quote_mint=Pubkey(data[quote_mint_at : quote_mint_at + 32]),
            is_mayhem_mode=bool(data[creator_at + 32]),
            is_cashback_coin=bool(data[creator_at + 33]),
        )

    @property
    def quotes_in_sol(self) -> bool:
        """La curva cotiza en SOL. Un `quote_mint` a cero significa SOL nativo."""
        return str(self.quote_mint) in (WRAPPED_SOL_MINT, str(Pubkey.default()))


def decode_global_fee_recipient(data: bytes) -> Pubkey:
    """`fee_recipient` de la cuenta `Global`: discriminador (8) + `initialized` (1) +
    `authority` (32). No se deriva; el programa lo fija y puede cambiar."""
    offset = _DISCRIMINATOR_LEN + 1 + 32
    if len(data) < offset + 32:
        msg = f"cuenta global demasiado corta: {len(data)} bytes"
        raise TradeError(msg)
    return Pubkey(data[offset : offset + 32])


# Offset de `buyback_fee_recipients` dentro de `Global`, sumando los campos previos del IDL:
# disc + initialized + authority + fee_recipient + 5 u64 + withdraw_authority + enable_migrate
# + pool_migration_fee + creator_fee_basis_points + fee_recipients[7] + set_creator_authority
# + admin_set_creator_authority + create_v2_enabled + whitelist_pda + reserved_fee_recipient
# + mayhem_mode_enabled + reserved_fee_recipients[7] + is_cashback_enabled.
_OFFSET_BUYBACK_RECIPIENTS: Final = (
    _DISCRIMINATOR_LEN + 1 + 32 + 32 + 8 * 5 + 32 + 1 + 8 + 8 + 32 * 7 + 32 + 32 + 1 + 32 + 32
    + 1 + 32 * 7 + 1
)
_BUYBACK_RECIPIENT_COUNT: Final = 8


# `reserved_fee_recipient` va detras de `whitelist_pda`, contando los campos previos del IDL.
_OFFSET_RESERVED_FEE_RECIPIENT: Final = (
    _DISCRIMINATOR_LEN + 1 + 32 + 32 + 8 * 5 + 32 + 1 + 8 + 8 + 32 * 7 + 32 + 32 + 1 + 32
)


def decode_global_reserved_fee_recipient(data: bytes) -> Pubkey:
    """Destinatario de comisiones para los tokens en *mayhem mode*.

    Un token con `is_mayhem_mode` NO acepta el `fee_recipient` normal: el programa revierte
    con `NotAuthorized` desde `fee_recipient.rs`. Se detecto probando la misma compra sobre
    todos los tokens del radar: fallo exactamente en el unico que tenia la bandera puesta.
    """
    if len(data) < _OFFSET_RESERVED_FEE_RECIPIENT + 32:
        msg = f"cuenta global demasiado corta para el destinatario reservado: {len(data)} bytes"
        raise TradeError(msg)
    return Pubkey(
        data[_OFFSET_RESERVED_FEE_RECIPIENT : _OFFSET_RESERVED_FEE_RECIPIENT + 32]
    )


# Offsets de las comisiones dentro de `Global`, contando los campos previos del IDL.
_OFFSET_FEE_BPS: Final = _DISCRIMINATOR_LEN + 1 + 32 + 32 + 8 * 3 + 8
_OFFSET_CREATOR_FEE_BPS: Final = _OFFSET_FEE_BPS + 8 + 32 + 1 + 8


def decode_global_fee_bps(data: bytes) -> int:
    """Comision total en puntos basicos: la del protocolo mas la del creador.

    **No es opcional contarla.** El programa la cobra ENCIMA del SOL que se mete, asi que
    quien pide 0,1 SOL gasta 0,1 mas comision. Ignorarla hace dos danos: se gasta mas de lo
    que el usuario escribio, y con un slippage ajustado la compra revierte por un tope que
    parecia suficiente.
    """
    end = _OFFSET_CREATOR_FEE_BPS + 8
    if len(data) < end:
        msg = f"cuenta global demasiado corta para las comisiones: {len(data)} bytes"
        raise TradeError(msg)
    protocol = int.from_bytes(data[_OFFSET_FEE_BPS : _OFFSET_FEE_BPS + 8], "little")
    creator = int.from_bytes(
        data[_OFFSET_CREATOR_FEE_BPS : _OFFSET_CREATOR_FEE_BPS + 8], "little"
    )
    total = protocol + creator
    # Una comision del 100% no existe: si sale eso, el offset esta mal y hay que fallar aqui,
    # no dimensionar una orden con un numero inventado.
    if not 0 < total < 2_000:
        msg = (
            f"comision total fuera de rango: {total} bps "
            f"(protocolo {protocol}, creador {creator})"
        )
        raise TradeError(msg)
    return total


def net_lamports_after_fee(lamports_in: int, fee_bps: int) -> int:
    """Parte del importe que llega de verdad a la curva, una vez apartada la comision.

    Se reparte al reves de como se suele hacer: en vez de calcular los tokens sobre el
    importe entero y descubrir despues que se gasto de mas, se aparta primero la comision.
    Asi 0,1 SOL son 0,1 SOL de gasto total, que es lo que el usuario cree que esta haciendo.
    """
    return (lamports_in * 10_000) // (10_000 + fee_bps)


def decode_global_buyback_recipients(data: bytes) -> tuple[Pubkey, ...]:
    """Los ocho `buyback_fee_recipients` de `Global`.

    El programa exige que la compra lleve uno de ellos —si falta, revierte con
    `BuybackFeeRecipientMissing`— pero acepta cualquiera de los ocho: se comprobo simulando
    la misma compra contra cada uno.
    """
    end = _OFFSET_BUYBACK_RECIPIENTS + 32 * _BUYBACK_RECIPIENT_COUNT
    if len(data) < end:
        msg = f"cuenta global demasiado corta para los buyback recipients: {len(data)} bytes"
        raise TradeError(msg)
    starts = (_OFFSET_BUYBACK_RECIPIENTS + 32 * i for i in range(_BUYBACK_RECIPIENT_COUNT))
    recipients = tuple(Pubkey(data[start : start + 32]) for start in starts)
    # El propio programa exige que sean ocho, no nulos y distintos entre si (errores
    # `AllBuybackFeeRecipientsShouldBeNonZero` y `NotUniqueBuybackFeeRecipients`). Comprobarlo
    # aqui convierte un offset equivocado en un fallo ruidoso en vez de en una cuenta basura
    # metida en una transaccion real.
    zero = Pubkey.default()
    if any(r == zero for r in recipients) or len(set(recipients)) != len(recipients):
        msg = "los buyback recipients leidos no son validos: el offset en `Global` no cuadra"
        raise TradeError(msg)
    return recipients


# ---------------------------------------------------------------------------
# Precio y deslizamiento
# ---------------------------------------------------------------------------


def tokens_out_for_lamports(curve: BondingCurveAccount, lamports_in: int) -> int:
    """Tokens que devuelve la curva por `lamports_in`, con la invariante `x·y = k`.

    Aritmetica entera en todo el camino: un `float` aqui se propaga al tamano de la orden.
    """
    if lamports_in <= 0:
        return 0
    out = (lamports_in * curve.virtual_token_reserves) // (
        curve.virtual_quote_reserves + lamports_in
    )
    # No se puede comprar mas de lo que queda realmente en la curva.
    return min(out, curve.real_token_reserves)


def lamports_out_for_tokens(curve: BondingCurveAccount, tokens_in: int) -> int:
    """SOL que devuelve la curva por `tokens_in`, antes de comisiones."""
    if tokens_in <= 0:
        return 0
    return (tokens_in * curve.virtual_quote_reserves) // (
        curve.virtual_token_reserves + tokens_in
    )


def apply_slippage_up(lamports: int, slippage_bps: int) -> int:
    """Tope superior de gasto: lo que como mucho se acepta pagar."""
    return (lamports * (10_000 + slippage_bps)) // 10_000


def apply_slippage_down(lamports: int, slippage_bps: int) -> int:
    """Suelo de ingreso: por debajo de esto la venta se revierte en vez de ejecutarse mal."""
    return (lamports * (10_000 - slippage_bps)) // 10_000


# ---------------------------------------------------------------------------
# Instrucciones
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TradeAccounts:
    """Todo lo que la instruccion necesita, ya resuelto."""

    mint: Pubkey
    user: Pubkey
    token_program: Pubkey
    fee_recipient: Pubkey
    creator: Pubkey

    @property
    def bonding_curve(self) -> Pubkey:
        return bonding_curve_pda(self.mint)

    @property
    def associated_bonding_curve(self) -> Pubkey:
        return associated_token_address(self.bonding_curve, self.mint, self.token_program)

    @property
    def associated_user(self) -> Pubkey:
        return associated_token_address(self.user, self.mint, self.token_program)


def _meta(pubkey: Pubkey, *, writable: bool = False, signer: bool = False) -> AccountMeta:
    return AccountMeta(pubkey=pubkey, is_signer=signer, is_writable=writable)


def build_buy_instruction(
    accounts: TradeAccounts,
    *,
    token_amount: int,
    max_lamports_cost: int,
    track_volume: bool = True,
    extra_accounts: tuple[Pubkey, ...] = (),
) -> Instruction:
    """Instruccion `buy`. Las 16 primeras cuentas, en el orden exacto del IDL.

    `token_amount` es lo que se quiere recibir y `max_lamports_cost` el tope de gasto: si el
    precio se mueve por encima, el programa revierte en vez de ejecutar a cualquier precio.

    **`extra_accounts` no es opcional en la practica.** El programa desplegado va por delante
    de su propio IDL y exige dos cuentas mas: una ligada al token y, en ultimo lugar, uno de
    los `buyback_fee_recipients` de `Global`. Sin ellas revierte con
    `BuybackFeeRecipientMissing`; con una cuenta equivocada en la primera posicion extra,
    revierte con el error 6074. Se comprobo simulando cada variante contra mainnet.
    """
    if token_amount <= 0:
        msg = "token_amount debe ser positivo"
        raise TradeError(msg)
    if max_lamports_cost <= 0:
        msg = "max_lamports_cost debe ser positivo: sin tope, un sandwich se lleva la orden"
        raise TradeError(msg)

    metas = [
        _meta(global_pda()),
        _meta(accounts.fee_recipient, writable=True),
        _meta(accounts.mint),
        _meta(accounts.bonding_curve, writable=True),
        _meta(accounts.associated_bonding_curve, writable=True),
        _meta(accounts.associated_user, writable=True),
        _meta(accounts.user, writable=True, signer=True),
        _meta(SYSTEM_PROGRAM),
        _meta(accounts.token_program),
        _meta(creator_vault_pda(accounts.creator), writable=True),
        _meta(event_authority_pda()),
        _meta(PUMPFUN_PROGRAM),
        _meta(global_volume_accumulator_pda()),
        _meta(user_volume_accumulator_pda(accounts.user), writable=True),
        _meta(fee_config_pda()),
        _meta(FEE_PROGRAM),
        *(_meta(extra, writable=True) for extra in extra_accounts),
    ]
    # `track_volume` es un `OptionBool` del IDL: un struct de un solo bool, o sea un byte.
    data = (
        DISC_BUY
        + token_amount.to_bytes(8, "little")
        + max_lamports_cost.to_bytes(8, "little")
        + bytes([1 if track_volume else 0])
    )
    return Instruction(program_id=PUMPFUN_PROGRAM, data=data, accounts=metas)


def build_sell_instruction(
    accounts: TradeAccounts,
    *,
    token_amount: int,
    min_lamports_output: int,
    extra_accounts: tuple[Pubkey, ...] = (),
) -> Instruction:
    """Instruccion `sell`. 14 cuentas, y en distinto orden que `buy`: aqui `creator_vault`
    va antes que `token_program`, y no hay acumuladores de volumen. Copiar el orden de `buy`
    produce una transaccion que falla."""
    if token_amount <= 0:
        msg = "token_amount debe ser positivo"
        raise TradeError(msg)

    metas = [
        _meta(global_pda()),
        _meta(accounts.fee_recipient, writable=True),
        _meta(accounts.mint),
        _meta(accounts.bonding_curve, writable=True),
        _meta(accounts.associated_bonding_curve, writable=True),
        _meta(accounts.associated_user, writable=True),
        _meta(accounts.user, writable=True, signer=True),
        _meta(SYSTEM_PROGRAM),
        _meta(creator_vault_pda(accounts.creator), writable=True),
        _meta(accounts.token_program),
        _meta(event_authority_pda()),
        _meta(PUMPFUN_PROGRAM),
        _meta(fee_config_pda()),
        _meta(FEE_PROGRAM),
        *(_meta(extra, writable=True) for extra in extra_accounts),
    ]
    data = (
        DISC_SELL
        + token_amount.to_bytes(8, "little")
        + min_lamports_output.to_bytes(8, "little")
    )
    return Instruction(program_id=PUMPFUN_PROGRAM, data=data, accounts=metas)


def build_create_ata_idempotent_instruction(
    payer: Pubkey, owner: Pubkey, mint: Pubkey, token_program: Pubkey
) -> Instruction:
    """`CreateIdempotent` del programa de ATAs (variante 1).

    Idempotente a proposito: si la cuenta ya existe no falla. La alternativa —consultar antes
    si existe— deja una ventana entre la consulta y el envio en la que otra transaccion puede
    crearla, y entonces la compra revierte por una cuenta que si estaba.
    """
    metas = [
        _meta(payer, writable=True, signer=True),
        _meta(associated_token_address(owner, mint, token_program), writable=True),
        _meta(owner),
        _meta(mint),
        _meta(SYSTEM_PROGRAM),
        _meta(token_program),
    ]
    return Instruction(program_id=ATA_PROGRAM, data=bytes([1]), accounts=metas)
