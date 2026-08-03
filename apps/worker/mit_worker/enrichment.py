"""Enriquecimiento on-chain de un token (SPEC.md 7).

Construye el `TokenContext` que consumen los doce detectores de manipulacion. Sin esto los
detectores son codigo que nadie puede ejecutar sobre un token real.

**Todo sale del RPC, sin una sola credencial** (DATA_PROVIDERS.md 4): holders y su
concentracion, historial de lanzamientos del creador, y las operaciones del token con la
wallet de cada una.

El coste esta acotado a proposito. Enumerar holders y decodificar N transacciones son muchas
llamadas, y el endpoint publico corta con 429 en cuanto se aprieta. Por eso hay limites
explicitos y por eso el enriquecimiento se ENCOLA: no se hace bajo demanda desde la interfaz.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import based58
from mit_features.concentration import ConcentrationMetrics, concentration
from mit_pumpfun.constants import PUMPFUN_PROGRAM_ID, TOKEN_2022_PROGRAM_ID, anchor_discriminator
from mit_pumpfun.decoder import resolve_account_keys
from mit_solana.rpc import RpcError, RpcRateLimitedError, SolanaRpc
from mit_strategies.manipulation import TokenContext, TradeRecord, WalletInfo
from mit_strategies.manipulation.types import Finding

LOGGER = logging.getLogger("mit.enrichment")

DISCRIMINATOR_BUY = anchor_discriminator("buy")
DISCRIMINATOR_SELL = anchor_discriminator("sell")
DISCRIMINATOR_CREATE_V2 = anchor_discriminator("create_v2")


@dataclass(frozen=True, slots=True)
class EnrichmentLimits:
    """Cuanto se permite gastar en un token. El RPC publico obliga a ser tacano."""

    max_token_signatures: int = 40
    max_transactions: int = 20
    max_creator_signatures: int = 40
    max_creator_transactions: int = 15


@dataclass(frozen=True, slots=True)
class EnrichmentResult:
    """Contexto listo para los detectores, mas lo que se pudo medir por el camino."""

    context: TokenContext
    concentration_metrics: ConcentrationMetrics | None
    rpc_calls: int
    partial: bool
    notes: tuple[str, ...] = ()


def _decode_trades(transaction: dict[str, Any]) -> list[TradeRecord]:
    """Extrae compras y ventas de una transaccion ya obtenida."""
    keys = resolve_account_keys(transaction)
    message = transaction.get("transaction", {}).get("message", {})
    signers = message.get("accountKeys") or []
    wallet = signers[0] if signers else ""
    block_time = transaction.get("blockTime")
    when = datetime.fromtimestamp(block_time, tz=UTC) if block_time else datetime.now(UTC)
    signature = (transaction.get("transaction", {}).get("signatures") or [""])[0]

    trades: list[TradeRecord] = []
    for instruction in message.get("instructions") or []:
        index = instruction.get("programIdIndex")
        if index is None or index >= len(keys) or keys[index] != PUMPFUN_PROGRAM_ID:
            continue
        try:
            data = bytes(based58.b58decode(instruction.get("data", "").encode()))
        except Exception:
            # Datos ilegibles: se ignora la instruccion, no la transaccion entera. Se
            # registra en DEBUG para poder detectar un cambio de formato del programa.
            LOGGER.debug("instruccion no decodificable en %s", signature, exc_info=True)
            continue
        side = (
            "buy"
            if data[:8] == DISCRIMINATOR_BUY
            else "sell"
            if data[:8] == DISCRIMINATOR_SELL
            else None
        )
        if side is None:
            continue
        # buy(amount, max_sol_cost) / sell(amount, min_sol_output): dos u64 tras el discriminador.
        token_amount = int.from_bytes(data[8:16], "little") if len(data) >= 16 else 0
        sol_amount = int.from_bytes(data[16:24], "little") if len(data) >= 24 else 0
        trades.append(
            TradeRecord(
                signature=signature,
                slot=int(transaction.get("slot", 0)),
                block_time=when,
                wallet=wallet,
                side=side,
                sol_amount=sol_amount,
                token_amount=token_amount,
            )
        )
    return trades


async def count_creator_launches(
    rpc: SolanaRpc, creator: str, limits: EnrichmentLimits
) -> tuple[int, int]:
    """Cuantos tokens lanzo el creador y en cuantos vendio, en la ventana inspeccionada.

    Devuelve `(lanzamientos, dumps)`. Si no se puede observar, devuelve ceros: el detector de
    historial calla sin datos, que es lo correcto — no se acusa por ausencia de evidencia.
    """
    launches = dumps = 0
    signatures = await rpc.get_signatures(creator, limit=limits.max_creator_signatures)
    for entry in signatures[: limits.max_creator_transactions]:
        if entry.get("err"):
            continue
        transaction = await rpc.get_transaction(entry["signature"])
        if not transaction:
            continue
        keys = resolve_account_keys(transaction)
        message = transaction.get("transaction", {}).get("message", {})
        for instruction in message.get("instructions") or []:
            index = instruction.get("programIdIndex")
            if index is None or index >= len(keys) or keys[index] != PUMPFUN_PROGRAM_ID:
                continue
            try:
                data = bytes(based58.b58decode(instruction.get("data", "").encode()))
            except Exception:
                LOGGER.debug("instruccion no decodificable en el historial", exc_info=True)
                continue
            if data[:8] == DISCRIMINATOR_CREATE_V2:
                launches += 1
            elif data[:8] == DISCRIMINATOR_SELL:
                dumps += 1
    return launches, dumps


async def enrich(
    rpc: SolanaRpc,
    mint: str,
    creator: str,
    created_at: datetime,
    *,
    name: str = "",
    symbol: str = "",
    uri: str = "",
    limits: EnrichmentLimits | None = None,
) -> EnrichmentResult:
    """Construye el contexto completo de un token a partir de la cadena.

    Nunca lanza por un fallo parcial del RPC: devuelve `partial=True` con lo que consiguio y
    una nota explicando que falta. Un analisis incompleto y declarado es util; una excepcion
    que aborta el enriquecimiento de todos los tokens de la cola, no.
    """
    limits = limits or EnrichmentLimits()
    notes: list[str] = []
    calls = 0
    partial = False

    holders: dict[str, int] = {}
    try:
        holders = await rpc.get_token_holders(mint, TOKEN_2022_PROGRAM_ID)
        calls += 1
    except (RpcError, RpcRateLimitedError) as error:
        partial = True
        notes.append(f"holders no disponibles: {error}")

    total_supply = 0
    try:
        total_supply = await rpc.get_token_supply(mint)
        calls += 1
    except (RpcError, RpcRateLimitedError) as error:
        partial = True
        notes.append(f"supply no disponible: {error}")

    trades: list[TradeRecord] = []
    try:
        signatures = await rpc.get_signatures(mint, limit=limits.max_token_signatures)
        calls += 1
        for entry in signatures[: limits.max_transactions]:
            if entry.get("err"):
                continue
            transaction = await rpc.get_transaction(entry["signature"])
            calls += 1
            if transaction:
                trades.extend(_decode_trades(transaction))
    except (RpcError, RpcRateLimitedError) as error:
        partial = True
        notes.append(f"operaciones incompletas: {error}")

    launches = dumps = 0
    try:
        launches, dumps = await count_creator_launches(rpc, creator, limits)
        calls += 1 + limits.max_creator_transactions
    except (RpcError, RpcRateLimitedError) as error:
        partial = True
        notes.append(f"historial del creador no disponible: {error}")

    wallets = {
        wallet: WalletInfo(address=wallet) for wallet in {t.wallet for t in trades} | set(holders)
    }

    context = TokenContext(
        mint=mint,
        creator=creator,
        created_at=created_at,
        total_supply=total_supply,
        trades=tuple(trades),
        holders=holders,
        wallets=wallets,
        name=name,
        symbol=symbol,
        uri=uri,
        creator_previous_tokens=launches,
        creator_previous_dumps=dumps,
    )

    metrics = concentration(list(holders.values())) if holders else None
    return EnrichmentResult(
        context=context,
        concentration_metrics=metrics,
        rpc_calls=calls,
        partial=partial,
        notes=tuple(notes),
    )


def summarize(result: EnrichmentResult, findings: tuple[Finding, ...] = ()) -> dict[str, object]:
    """Resumen legible para logs y para la API."""
    metrics = result.concentration_metrics
    return {
        "mint": result.context.mint,
        "holders": metrics.holder_count if metrics else 0,
        "top10_pct": float(metrics.top10_pct) if metrics else None,
        "gini": float(metrics.gini) if metrics else None,
        "trades": len(result.context.trades),
        "creator_launches": result.context.creator_previous_tokens,
        "creator_sells": result.context.creator_previous_dumps,
        "rpc_calls": result.rpc_calls,
        "partial": result.partial,
        "notes": list(result.notes),
        "findings": [f.reason for f in findings],
    }
