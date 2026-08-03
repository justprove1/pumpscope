"""Enriquecimiento on-chain: cierra el bucle RPC -> contexto -> detectores.

El RPC se sustituye por un doble, asi que los tests son deterministas y no gastan cuota. El
comportamiento contra el endpoint real ya se verifico en vivo: enumero holders, calculo Gini
y degrado con `partial=True` al toparse con el rate limit, sin abortar.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from mit_solana.rpc import RpcRateLimitedError
from mit_strategies.manipulation import analyze
from mit_worker.enrichment import EnrichmentLimits, enrich, summarize

LAUNCH = datetime(2026, 8, 3, 12, 0, 0, tzinfo=UTC)
MINT = "TestMint11111111111111111111111111111111111"
CREATOR = "TestCreator1111111111111111111111111111111"


class FakeRpc:
    """Doble del cliente RPC. Cada metodo puede configurarse para fallar."""

    def __init__(
        self,
        holders: dict[str, int] | None = None,
        supply: int = 1_000_000_000_000_000,
        fail: set[str] | None = None,
    ) -> None:
        self._holders = holders or {}
        self._supply = supply
        self._fail = fail or set()

    def _maybe_fail(self, name: str) -> None:
        if name in self._fail:
            msg = f"{name}: rate limit agotado"
            raise RpcRateLimitedError(msg)

    async def get_token_holders(self, mint: str, token_program: str) -> dict[str, int]:
        self._maybe_fail("holders")
        return dict(self._holders)

    async def get_token_supply(self, mint: str) -> int:
        self._maybe_fail("supply")
        return self._supply

    async def get_signatures(self, address: str, limit: int = 100) -> list[dict[str, Any]]:
        self._maybe_fail("signatures")
        return []

    async def get_transaction(self, signature: str) -> dict[str, Any] | None:
        self._maybe_fail("transaction")
        return None


async def test_enrichment_builds_a_usable_context() -> None:
    rpc = FakeRpc(holders={"a": 700, "b": 200, "c": 100})
    result = await enrich(rpc, MINT, CREATOR, LAUNCH, symbol="TEST")  # type: ignore[arg-type]

    assert result.context.mint == MINT
    assert result.context.holders == {"a": 700, "b": 200, "c": 100}
    assert result.concentration_metrics is not None
    assert result.concentration_metrics.holder_count == 3
    assert not result.partial


async def test_a_rate_limit_degrades_instead_of_aborting() -> None:
    """Un analisis incompleto y DECLARADO es util; una excepcion que vacia la cola, no."""
    rpc = FakeRpc(holders={"a": 1}, fail={"signatures"})
    result = await enrich(rpc, MINT, CREATOR, LAUNCH)  # type: ignore[arg-type]

    assert result.partial
    assert any("operaciones incompletas" in note for note in result.notes)
    # Lo que si se pudo obtener sigue ahi.
    assert result.context.holders == {"a": 1}


async def test_every_rpc_failure_is_reported_not_swallowed() -> None:
    rpc = FakeRpc(fail={"holders", "supply", "signatures", "transaction"})
    result = await enrich(rpc, MINT, CREATOR, LAUNCH)  # type: ignore[arg-type]

    assert result.partial
    assert len(result.notes) >= 3
    assert result.concentration_metrics is None


async def test_context_feeds_the_detectors_without_false_accusations() -> None:
    """Sin trades ni historial, los detectores no deben inventarse nada.

    Se pasa `uri` a proposito: sin ella dispara `metadata_missing`, que es correcto pero
    ajeno a lo que este test comprueba.
    """
    rpc = FakeRpc(holders=dict.fromkeys((f"h{i}" for i in range(40)), 100))
    result = await enrich(
        rpc,  # type: ignore[arg-type]
        MINT,
        CREATOR,
        LAUNCH,
        symbol="PERRO",
        uri="https://ipfs.io/ipfs/x",
    )

    report = analyze(result.context)
    assert report.score == 0
    assert report.findings == ()


async def test_concentrated_holders_are_flagged_through_the_pipeline() -> None:
    """El bucle completo: holders del RPC -> concentracion -> detector -> score."""
    holders = {"ballena": 900_000, **{f"h{i}": 100 for i in range(30)}}
    rpc = FakeRpc(holders=holders)
    result = await enrich(rpc, MINT, CREATOR, LAUNCH)  # type: ignore[arg-type]

    report = analyze(result.context)
    assert "supply_concentration" in {f.detector for f in report.findings}
    assert report.score > 0


async def test_summary_is_serializable_for_logs_and_api() -> None:
    rpc = FakeRpc(holders={"a": 5, "b": 5})
    result = await enrich(rpc, MINT, CREATOR, LAUNCH)  # type: ignore[arg-type]
    payload = summarize(result, analyze(result.context).findings)

    for key in ("mint", "holders", "top10_pct", "gini", "trades", "rpc_calls", "partial"):
        assert key in payload


async def test_limits_are_respected() -> None:
    """El coste por token esta acotado: el endpoint publico no perdona."""
    limits = EnrichmentLimits(max_transactions=3, max_creator_transactions=2)
    rpc = FakeRpc(holders={"a": 1})
    result = await enrich(rpc, MINT, CREATOR, LAUNCH, limits=limits)  # type: ignore[arg-type]
    assert result.rpc_calls <= 20


@pytest.mark.parametrize("supply", [0, 1_000_000_000_000_000])
async def test_supply_values_do_not_break_the_pipeline(supply: int) -> None:
    rpc = FakeRpc(holders={"a": 10}, supply=supply)
    result = await enrich(rpc, MINT, CREATOR, LAUNCH)  # type: ignore[arg-type]
    assert result.context.total_supply == supply
    assert 0 <= analyze(result.context).score <= 100
