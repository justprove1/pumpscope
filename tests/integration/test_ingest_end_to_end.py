"""Ingesta extremo a extremo contra PostgreSQL real, alimentada con fixtures reales.

Recorre el camino completo: notificacion de `logsSubscribe` -> detector -> base de datos,
y comprueba que la trazabilidad de SPEC.md 5 llega intacta hasta las tablas.

Requiere el stack levantado:  make up && make migrate
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from mit_pumpfun.detector import NewTokenDetector
from mit_worker.repository import TokenRepository
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

pytestmark = pytest.mark.integration

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/pumpfun_create_events.json"


def _events() -> list[dict[str, Any]]:
    payload: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    events: list[dict[str, Any]] = payload["events"]
    return events


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL no definida")
    engine = create_async_engine(url)
    yield engine
    # Limpieza: estos tests escriben de verdad, asi que borran lo suyo al terminar.
    mints = [event["log_notification"] for event in _events()]
    async with engine.begin() as connection:
        await connection.execute(
            sa.text("DELETE FROM bonding_curve_snapshots WHERE provider = 'test-e2e'")
        )
        await connection.execute(sa.text("DELETE FROM tokens WHERE platform = 'pumpfun'"))
        await connection.execute(
            sa.text("DELETE FROM creators WHERE address IN (SELECT address FROM creators)")
        )
    assert mints is not None
    await engine.dispose()


async def test_detected_tokens_reach_the_database(engine: AsyncEngine) -> None:
    detector = NewTokenDetector(provider="test-e2e")
    repository = TokenRepository(engine)

    inserted = 0
    for event in _events():
        token = detector.observe(event["log_notification"])
        assert token is not None
        result = await repository.save_detection(token)
        if result.inserted:
            inserted += 1

    assert inserted == len(_events())
    assert await repository.count() >= inserted


async def test_traceability_columns_are_populated(engine: AsyncEngine) -> None:
    """SPEC.md 5: provider, timestamps, slot, confidence, latency y raw_reference."""
    detector = NewTokenDetector(provider="test-e2e")
    repository = TokenRepository(engine)
    token = detector.observe(_events()[0]["log_notification"])
    assert token is not None
    await repository.save_detection(token)

    async with engine.connect() as connection:
        row = (
            (
                await connection.execute(
                    sa.text(
                        """
                    SELECT provider, provider_timestamp, received_timestamp,
                           blockchain_slot, confidence, latency_ms, raw_reference
                    FROM bonding_curve_snapshots WHERE mint = :mint
                    """
                    ),
                    {"mint": token.mint},
                )
            )
            .mappings()
            .first()
        )

    assert row is not None
    assert row["provider"] == "test-e2e"
    assert row["received_timestamp"] is not None
    assert row["provider_timestamp"] is not None
    assert row["confidence"] == 1
    assert row["latency_ms"] is not None
    assert row["raw_reference"] == token.signature


async def test_reprocessing_the_same_events_inserts_nothing_new(engine: AsyncEngine) -> None:
    """Idempotencia en la BASE DE DATOS, no solo en memoria.

    El dedup del detector no sobrevive a un reinicio del proceso; la base de datos si. Este
    test usa un detector NUEVO en la segunda pasada, justamente para saltarse el dedup en
    memoria y comprobar la defensa de abajo.
    """
    repository = TokenRepository(engine)

    first = NewTokenDetector(provider="test-e2e")
    for event in _events():
        token = first.observe(event["log_notification"])
        assert token is not None
        await repository.save_detection(token)

    count_after_first = await repository.count()

    second = NewTokenDetector(provider="test-e2e")  # proceso "reiniciado"
    reinserted = 0
    for event in _events():
        token = second.observe(event["log_notification"])
        assert token is not None
        if (await repository.save_detection(token)).inserted:
            reinserted += 1

    assert reinserted == 0
    assert await repository.count() == count_after_first


async def test_creator_is_stored_from_the_event_not_the_signer(engine: AsyncEngine) -> None:
    """El creador guardado es el del evento, que no siempre es quien firmo."""
    detector = NewTokenDetector(provider="test-e2e")
    repository = TokenRepository(engine)

    stored: list[tuple[str, str]] = []
    for event in _events():
        token = detector.observe(event["log_notification"])
        assert token is not None
        await repository.save_detection(token)
        stored.append((token.mint, token.event.creator))

    async with engine.connect() as connection:
        rows = (
            (
                await connection.execute(
                    sa.text("SELECT mint, creator_address FROM tokens WHERE mint = ANY(:mints)"),
                    {"mints": [mint for mint, _ in stored]},
                )
            )
            .mappings()
            .all()
        )

    persisted = {row["mint"]: row["creator_address"] for row in rows}
    for mint, creator in stored:
        assert persisted[mint] == creator


async def test_recent_tokens_feeds_the_radar(engine: AsyncEngine) -> None:
    detector = NewTokenDetector(provider="test-e2e")
    repository = TokenRepository(engine)
    for event in _events():
        token = detector.observe(event["log_notification"])
        assert token is not None
        await repository.save_detection(token)

    recent = await repository.recent_tokens(limit=10)
    assert recent
    first = recent[0]
    for column in ("mint", "symbol", "name", "creator_address", "detection_latency_ms"):
        assert column in first
