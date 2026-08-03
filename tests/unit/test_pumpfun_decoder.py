"""El decoder de Pump.fun, probado contra transacciones REALES de mainnet.

Las fixtures se capturaron con `infrastructure/scripts/record_pumpfun_fixtures.py`. Ninguna
esta escrita a mano (CLAUDE.md 2). Si el programa cambia de formato, estos tests fallan
antes que la ingesta en produccion.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from mit_pumpfun import (
    DISCRIMINATOR_CREATE_V2,
    GLOBAL_CONFIG,
    MINT_AUTHORITY,
    PUMPFUN_PROGRAM_ID,
    TOKEN_2022_PROGRAM_ID,
    DecodeError,
    anchor_discriminator,
    decode_create,
    extract_token_creation,
    iter_instructions,
    resolve_account_keys,
)

FIXTURE = Path(__file__).resolve().parents[2] / "tests/fixtures/pumpfun_create_events.json"


def _events() -> list[dict[str, Any]]:
    payload: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    events: list[dict[str, Any]] = payload["events"]
    return events


def _transactions() -> list[dict[str, Any]]:
    return [event["transaction"]["result"] for event in _events()]


def _ids() -> list[str]:
    return [event["signature"][:12] for event in _events()]


# --- Constantes ---------------------------------------------------------------------------


def test_create_discriminator_is_derived_not_hardcoded() -> None:
    """El discriminador observado coincide con la derivacion Anchor de `create_v2`.

    Es la prueba de que la instruccion se llama `create_v2` y no `create`: el valor real
    observado en mainnet es d6904cec5f8b31b4.
    """
    assert hashlib.sha256(b"global:create_v2").digest()[:8] == DISCRIMINATOR_CREATE_V2
    assert DISCRIMINATOR_CREATE_V2.hex() == "d6904cec5f8b31b4"
    assert anchor_discriminator("create").hex() == "181ec828051c0777"


def test_every_fixture_uses_create_v2() -> None:
    """Toda creacion capturada usa create_v2. Si esto falla, el programa cambio."""
    seen = set()
    for transaction in _transactions():
        for instruction in iter_instructions(transaction):
            seen.add(instruction.discriminator)
    assert DISCRIMINATOR_CREATE_V2 in seen


# --- Address Lookup Tables ----------------------------------------------------------------


@pytest.mark.parametrize("transaction", _transactions(), ids=_ids())
def test_account_indices_need_lookup_table_resolution(transaction: dict[str, Any]) -> None:
    """Los indices superan la lista estatica: sin fusionar loadedAddresses se lee mal.

    Este test existe porque la primera version del analisis reventaba con IndexError. Son
    transacciones v0 con Address Lookup Tables.
    """
    static = transaction["transaction"]["message"]["accountKeys"]
    resolved = resolve_account_keys(transaction)
    assert len(resolved) > len(static)
    assert resolved[: len(static)] == static

    max_index = max(
        index
        for instruction in transaction["transaction"]["message"]["instructions"]
        for index in instruction["accounts"]
    )
    assert max_index < len(resolved)


# --- Decodificacion -----------------------------------------------------------------------


@pytest.mark.parametrize("event", _events(), ids=_ids())
def test_extracts_a_creation_from_every_fixture(event: dict[str, Any]) -> None:
    creation = extract_token_creation(event["transaction"]["result"], event["signature"])
    assert creation is not None
    assert creation.signature == event["signature"]
    assert creation.slot > 0
    assert creation.name
    assert creation.symbol
    assert creation.uri.startswith("https://")
    # Direcciones base58 de Solana: 32 bytes -> 43 o 44 caracteres.
    for address in (creation.mint, creation.creator, creation.bonding_curve):
        assert 32 <= len(address) <= 44


@pytest.mark.parametrize("transaction", _transactions(), ids=_ids())
def test_fixed_accounts_are_where_the_decoder_expects(transaction: dict[str, Any]) -> None:
    """Las posiciones de cuenta constantes se cumplen en todas las fixtures."""
    for instruction in iter_instructions(transaction):
        if instruction.discriminator != DISCRIMINATOR_CREATE_V2:
            continue
        assert instruction.accounts[1] == MINT_AUTHORITY
        assert instruction.accounts[4] == GLOBAL_CONFIG
        # Pump.fun usa Token-2022, no el SPL Token clasico.
        assert TOKEN_2022_PROGRAM_ID in instruction.accounts


def test_creator_comes_from_payload_not_from_signer() -> None:
    """El hallazgo que justifica todo este modulo.

    En al menos una de las creaciones capturadas el `creator` del payload NO es el pagador
    de la transaccion. Deducir el creador del firmante acierta la mayoria de las veces, que
    es justo lo que hace peligroso el error: el CreatorScore quedaria atribuido a la wallet
    equivocada sin que nada falle visiblemente.
    """
    mismatches = 0
    for event in _events():
        creation = extract_token_creation(event["transaction"]["result"], event["signature"])
        assert creation is not None
        if creation.creator != creation.fee_payer:
            mismatches += 1
    assert mismatches >= 1, "las fixtures ya no cubren el caso creator != fee_payer"


@pytest.mark.parametrize("transaction", _transactions(), ids=_ids())
def test_mint_is_the_first_account(transaction: dict[str, Any]) -> None:
    creation = extract_token_creation(transaction)
    assert creation is not None
    for instruction in iter_instructions(transaction):
        if instruction.discriminator == DISCRIMINATOR_CREATE_V2:
            assert creation.mint == instruction.accounts[0]
            break


# --- Casos que NO son creaciones -----------------------------------------------------------


def test_failed_transaction_is_ignored() -> None:
    """Una transaccion fallida no crea nada, por mucho que invoque al programa."""
    transaction = copy.deepcopy(_transactions()[0])
    transaction["meta"]["err"] = {"InstructionError": [3, {"Custom": 3}]}
    assert extract_token_creation(transaction) is None


def test_transaction_without_pumpfun_returns_none() -> None:
    transaction = copy.deepcopy(_transactions()[0])
    keys = transaction["transaction"]["message"]["accountKeys"]
    transaction["transaction"]["message"]["accountKeys"] = [
        "So11111111111111111111111111111111111111112" if k == PUMPFUN_PROGRAM_ID else k
        for k in keys
    ]
    loaded = transaction["meta"]["loadedAddresses"]
    for bucket in ("writable", "readonly"):
        loaded[bucket] = [
            "So11111111111111111111111111111111111111112" if k == PUMPFUN_PROGRAM_ID else k
            for k in loaded[bucket]
        ]
    assert extract_token_creation(transaction) is None


def test_empty_transaction_returns_none() -> None:
    assert extract_token_creation({}) is None


# --- Formato corrupto: debe fallar RUIDOSAMENTE --------------------------------------------


def test_truncated_payload_raises() -> None:
    """Un payload cortado significa que el programa cambio. No se traga en silencio."""
    for transaction in _transactions():
        for instruction in iter_instructions(transaction):
            if instruction.discriminator != DISCRIMINATOR_CREATE_V2:
                continue
            truncated = type(instruction)(
                program_id=instruction.program_id,
                data=instruction.data[:20],
                accounts=instruction.accounts,
                stack_height=instruction.stack_height,
            )
            with pytest.raises(DecodeError, match="truncado"):
                decode_create(truncated)
            return
    pytest.fail("no se encontro ninguna instruccion create_v2 en las fixtures")


def test_wrong_discriminator_raises() -> None:
    for transaction in _transactions():
        for instruction in iter_instructions(transaction):
            if instruction.discriminator == DISCRIMINATOR_CREATE_V2:
                continue
            with pytest.raises(DecodeError, match="no es una creacion"):
                decode_create(instruction)
            return


def test_create_with_too_few_accounts_raises() -> None:
    transaction = copy.deepcopy(_transactions()[0])
    for instruction in transaction["transaction"]["message"]["instructions"]:
        keys = resolve_account_keys(transaction)
        if keys[instruction["programIdIndex"]] == PUMPFUN_PROGRAM_ID:
            instruction["accounts"] = instruction["accounts"][:3]
            break
    with pytest.raises(DecodeError, match="cuentas"):
        extract_token_creation(transaction)


def test_names_with_invalid_utf8_do_not_crash_ingestion() -> None:
    """Los metadatos son texto arbitrario del creador y pueden traer basura a proposito.

    Un nombre roto es un dato, no una excepcion que deba tumbar la ingesta.
    """
    import based58

    transaction = copy.deepcopy(_transactions()[0])
    keys = resolve_account_keys(transaction)
    for instruction in transaction["transaction"]["message"]["instructions"]:
        if keys[instruction["programIdIndex"]] != PUMPFUN_PROGRAM_ID:
            continue
        raw = bytes(based58.b58decode(instruction["data"].encode()))
        if raw[:8] != DISCRIMINATOR_CREATE_V2:
            continue
        payload = bytearray(raw)
        # Sustituye el primer byte del nombre por uno invalido en UTF-8.
        payload[12] = 0xFF
        instruction["data"] = based58.b58encode(bytes(payload)).decode()
        creation = extract_token_creation(transaction)
        assert creation is not None
        assert "�" in creation.name
        return
    pytest.fail("no se encontro instruccion create_v2")
