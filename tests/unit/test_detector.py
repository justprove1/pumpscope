"""NewTokenDetector y deduplicacion, contra notificaciones REALES de logsSubscribe.

Las notificaciones salen de `tests/fixtures/pumpfun_create_events.json`, capturadas en vivo.
El detector no toca la red, asi que estos tests son deterministas y rapidos.
"""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from mit_pumpfun.detector import NewTokenDetector
from mit_pumpfun.events import (
    DISCRIMINATOR_CREATE_EVENT,
    decode_create_event,
    find_create_event,
    iter_program_data,
    looks_like_creation,
)
from mit_shared.dedup import BoundedDedup

FIXTURE = Path(__file__).resolve().parents[2] / "tests/fixtures/pumpfun_create_events.json"


def _events() -> list[dict[str, Any]]:
    payload: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    events: list[dict[str, Any]] = payload["events"]
    return events


def _notifications() -> list[dict[str, Any]]:
    return [event["log_notification"] for event in _events()]


def _ids() -> list[str]:
    return [event["signature"][:12] for event in _events()]


# --- Dedup acotado ------------------------------------------------------------------------


def test_dedup_reports_first_time_only() -> None:
    dedup = BoundedDedup(capacity=10)
    assert dedup.add("a") is True
    assert dedup.add("a") is False
    assert "a" in dedup


def test_dedup_never_grows_past_capacity() -> None:
    """Un set sin limite es una fuga de memoria con otro nombre en un proceso 24/7."""
    dedup = BoundedDedup(capacity=100)
    for i in range(10_000):
        dedup.add(f"sig-{i}")
    assert len(dedup) == 100


def test_dedup_evicts_oldest_first() -> None:
    dedup = BoundedDedup(capacity=3)
    for key in ("a", "b", "c"):
        dedup.add(key)
    dedup.add("d")
    assert "a" not in dedup
    assert "d" in dedup


def test_dedup_rejects_invalid_capacity() -> None:
    with pytest.raises(ValueError, match="positiva"):
        BoundedDedup(capacity=0)


# --- Decodificacion del evento desde el log ------------------------------------------------


def test_create_event_discriminator_is_derived() -> None:
    assert hashlib.sha256(b"event:CreateEvent").digest()[:8] == DISCRIMINATOR_CREATE_EVENT
    assert DISCRIMINATOR_CREATE_EVENT.hex() == "1b72a94ddeeb6376"


@pytest.mark.parametrize("notification", _notifications(), ids=_ids())
def test_create_event_travels_inside_the_log(notification: dict[str, Any]) -> None:
    """La notificacion ya trae todo: no hace falta llamar a getTransaction.

    Es la decision que hace alcanzable el objetivo de <1 s de SPEC.md 6.
    """
    logs = notification["params"]["result"]["value"]["logs"]
    event = find_create_event(logs)
    assert event is not None
    assert event.name
    assert event.symbol
    assert event.mint.endswith("pump")
    assert event.virtual_sol_reserves == 30_000_000_000  # 30 SOL en lamports
    assert event.token_total_supply == 1_000_000_000_000_000


def test_event_creator_can_differ_from_user() -> None:
    """`user` y `creator` son campos distintos y no siempre coinciden."""
    mismatches = 0
    for notification in _notifications():
        logs = notification["params"]["result"]["value"]["logs"]
        event = find_create_event(logs)
        assert event is not None
        if event.user != event.creator:
            mismatches += 1
    assert mismatches >= 1, "las fixtures ya no cubren el caso user != creator"


def test_cheap_filter_matches_real_creations() -> None:
    for notification in _notifications():
        assert looks_like_creation(notification["params"]["result"]["value"]["logs"])


def test_corrupt_program_data_line_is_skipped_not_fatal() -> None:
    """Los logs son datos de terceros: una linea rota no debe tumbar la ingesta."""
    logs = ["Program data: no-es-base64-valido!!!", "Program log: nada"]
    assert list(iter_program_data(logs)) == []
    assert find_create_event(logs) is None


def test_decoding_a_non_create_event_raises() -> None:
    with pytest.raises(Exception, match="no es CreateEvent"):
        decode_create_event(b"\x00" * 64)


# --- Detector -----------------------------------------------------------------------------


@pytest.mark.parametrize("event", _events(), ids=_ids())
def test_detects_every_real_creation(event: dict[str, Any]) -> None:
    detector = NewTokenDetector(provider="test")
    token = detector.observe(event["log_notification"])
    assert token is not None
    assert token.signature == event["signature"]
    assert token.mint.endswith("pump")
    assert token.provider == "test"
    assert token.pipeline_latency_ms >= 0


def test_pipeline_latency_is_far_below_budget() -> None:
    """El presupuesto de SPEC.md 6 es 1000 ms. Sin red, el pipeline propio es despreciable.

    El umbral es holgado (5 ms) porque un test no debe fallar por un pico del runner; lo
    que se comprueba es el orden de magnitud, no una cifra exacta.
    """
    detector = NewTokenDetector()
    for notification in _notifications():
        detector.observe(notification)
    worst = max(detector.stats.pipeline_latencies_ms)
    assert worst < 5.0, f"la decodificacion tardo {worst:.2f} ms"


def test_duplicate_notification_is_detected_once() -> None:
    """El caso que se da tras cada reconexion: el proveedor reenvia parte de la ventana."""
    detector = NewTokenDetector()
    notification = _notifications()[0]

    first = detector.observe(notification)
    second = detector.observe(notification)

    assert first is not None
    assert second is None
    assert detector.stats.detected == 1
    assert detector.stats.duplicates == 1


def test_replaying_the_whole_stream_detects_each_token_once() -> None:
    """Simula una reconexion que reenvia TODOS los eventos ya vistos.

    Ni duplicados ni perdidas: es el requisito de resiliencia de SPEC.md 25.
    """
    detector = NewTokenDetector()
    notifications = _notifications()

    first_pass = [detector.observe(n) for n in notifications]
    second_pass = [detector.observe(n) for n in notifications]

    assert all(token is not None for token in first_pass)
    assert all(token is None for token in second_pass)
    assert detector.stats.detected == len(notifications)
    assert detector.stats.duplicates == len(notifications)

    mints = {token.mint for token in first_pass if token is not None}
    assert len(mints) == len(notifications)


def test_failed_transaction_is_not_a_detection() -> None:
    detector = NewTokenDetector()
    notification = copy.deepcopy(_notifications()[0])
    notification["params"]["result"]["value"]["err"] = {"InstructionError": [3, {"Custom": 3}]}
    assert detector.observe(notification) is None
    assert detector.stats.detected == 0
    assert detector.stats.filtered_out == 1


def test_ordinary_trade_traffic_is_discarded_cheaply() -> None:
    """El 99,9% del trafico son compras y ventas: deben descartarse sin decodificar."""
    detector = NewTokenDetector()
    notification = {
        "params": {
            "result": {
                "context": {"slot": 1},
                "value": {
                    "signature": "sig-compra",
                    "err": None,
                    "logs": [
                        "Program 6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P invoke [1]",
                        "Program log: Instruction: Buy",
                    ],
                },
            }
        }
    }
    assert detector.observe(notification) is None
    assert detector.stats.filtered_out == 1
    assert detector.stats.duplicates == 0


def test_malformed_notification_does_not_crash() -> None:
    detector = NewTokenDetector()
    assert detector.observe({}) is None
    assert detector.observe({"params": {"result": {"value": None}}}) is None
    assert detector.stats.malformed >= 1


def test_notification_without_signature_is_malformed() -> None:
    detector = NewTokenDetector()
    notification = copy.deepcopy(_notifications()[0])
    del notification["params"]["result"]["value"]["signature"]
    assert detector.observe(notification) is None
    assert detector.stats.malformed == 1


def test_onchain_lag_is_measured_in_seconds() -> None:
    """El evento trae el tiempo en segundos: el retraso no puede fingir precision de ms."""
    detector = NewTokenDetector()
    event = _events()[0]
    logs = event["log_notification"]["params"]["result"]["value"]["logs"]
    create = find_create_event(logs)
    assert create is not None

    received = datetime.fromtimestamp(create.timestamp, tz=UTC) + timedelta(seconds=3)
    token = detector.observe(event["log_notification"], received_timestamp=received)
    assert token is not None
    assert token.onchain_lag_seconds == 3


def test_stats_account_for_every_observation() -> None:
    """Ningun evento se pierde de la contabilidad: entradas = suma de destinos."""
    detector = NewTokenDetector()
    for notification in _notifications():
        detector.observe(notification)
        detector.observe(notification)
    detector.observe({})

    stats = detector.stats
    total = (
        stats.detected
        + stats.duplicates
        + stats.filtered_out
        + stats.decode_errors
        + stats.malformed
    )
    assert total == stats.observed
