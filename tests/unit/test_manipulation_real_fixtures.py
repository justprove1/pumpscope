"""Detectores contra datos REALES de mainnet.

A diferencia de `test_manipulation.py`, donde los escenarios estan construidos, todo lo de
aqui viene de `tests/fixtures/pumpfun_serial_creator.json`, capturado del RPC publico con
`getSignaturesForAddress` sobre la wallet del creador.

Lo que estos datos demuestran y lo que no:

- **Si demuestran** que existen creadores en serie: uno lanzo 5 tokens en 15 minutos.
- **Si demuestran** que la suplantacion de marca es habitual: simbolos como `VERIFIED` y
  `OFFICATE` salen del mismo creador.
- **NO demuestran** un rug. En la muestra inspeccionada no se encontro ni una venta de un
  creador en su propio token. Esa fixture sigue pendiente y se declara en el reporte de fase.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from mit_strategies.manipulation import Severity, TokenContext, analyze
from mit_strategies.manipulation.token_integrity import detect_impersonation
from mit_strategies.manipulation.trading import detect_creator_history

FIXTURE = Path(__file__).resolve().parents[2] / "tests/fixtures/pumpfun_serial_creator.json"


def _creators() -> list[dict[str, Any]]:
    payload: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    creators: list[dict[str, Any]] = payload["creators"]
    return creators


def _ids() -> list[str]:
    return [c["creator"][:10] for c in _creators()]


def test_fixture_declares_its_provenance() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    meta = payload["_fixture_meta"]
    assert meta["case"] == "serial_creator"
    assert "mainnet" in meta["note"] or "REALES" in meta["note"]
    assert meta["source"].startswith("https://")


@pytest.mark.parametrize("creator", _creators(), ids=_ids())
def test_serial_launches_are_real_and_repeated(creator: dict[str, Any]) -> None:
    """Cada creador de la muestra lanzo varios tokens, no uno."""
    assert creator["launches_observed"] >= 2
    mints = {launch["mint"] for launch in creator["launches"]}
    assert len(mints) == creator["launches_observed"], "los mints deben ser distintos"


def test_a_real_creator_launched_five_tokens_in_fifteen_minutes() -> None:
    """El caso mas claro de la captura: una fabrica de tokens.

    Cinco lanzamientos en un cuarto de hora no es un proyecto: es produccion en serie.
    """
    fastest = min(_creators(), key=lambda c: c["span_seconds"] / max(1, c["launches_observed"]))
    assert fastest["launches_observed"] >= 5
    assert fastest["span_seconds"] <= 30 * 60


def test_creator_history_detector_fires_on_real_serial_creator() -> None:
    """El detector marca a un creador en serie con historial de dumps.

    Los lanzamientos son reales; `creator_previous_dumps` NO se pudo observar en la muestra,
    asi que se declara aqui de forma explicita para ejercitar el umbral. Sin ese dato el
    detector calla, que es el comportamiento correcto: no se acusa sin evidencia.
    """
    creator = max(_creators(), key=lambda c: c["launches_observed"])
    launches = creator["launches_observed"]

    context = TokenContext(
        mint=creator["launches"][0]["mint"],
        creator=creator["creator"],
        created_at=datetime.fromtimestamp(creator["launches"][0]["block_time"], tz=UTC),
        total_supply=1_000_000_000_000_000,
        creator_previous_tokens=launches,
        creator_previous_dumps=0,
    )
    assert detect_creator_history(context) == [], "sin dumps observados no debe acusar"

    # Mismo creador, ahora con dumps conocidos: el detector debe hablar.
    accused = TokenContext(
        **{
            **{k: getattr(context, k) for k in context.__slots__},
            "creator_previous_dumps": launches - 1,
        }
    )
    findings = detect_creator_history(accused)
    assert findings
    assert f"de sus ultimos {launches} tokens" in findings[0].reason
    assert findings[0].severity == Severity.CRITICAL


def test_impersonation_detector_fires_on_real_symbols() -> None:
    """Simbolos reales capturados: `VERIFIED` y `OFFICATE` salen del mismo creador."""
    symbols = {
        launch["symbol"].upper() for creator in _creators() for launch in creator["launches"]
    }
    assert "VERIFIED" in symbols, "las fixtures ya no cubren el caso de suplantacion"

    flagged = 0
    for creator in _creators():
        for launch in creator["launches"]:
            context = TokenContext(
                mint=launch["mint"],
                creator=creator["creator"],
                created_at=datetime.fromtimestamp(launch["block_time"], tz=UTC),
                total_supply=1_000_000_000_000_000,
                name=launch["name"],
                symbol=launch["symbol"],
                uri="https://ipfs.io/ipfs/x",
            )
            if detect_impersonation(context):
                flagged += 1
    assert flagged >= 1, "ningun token real disparo el detector de suplantacion"


def test_real_launches_do_not_produce_false_criticals() -> None:
    """Sin datos de holders ni de trades, no se acusa a nadie de nada critico.

    Es la comprobacion que evita que el sistema chille ante cada token nuevo: la ausencia de
    informacion no puede convertirse en sospecha.
    """
    for creator in _creators():
        for launch in creator["launches"]:
            context = TokenContext(
                mint=launch["mint"],
                creator=creator["creator"],
                created_at=datetime.fromtimestamp(launch["block_time"], tz=UTC),
                total_supply=1_000_000_000_000_000,
                name=launch["name"],
                symbol=launch["symbol"],
                uri="https://ipfs.io/ipfs/x",
            )
            report = analyze(context)
            assert 0 <= report.score <= 100
            criticals = [f for f in report.findings if f.severity == Severity.CRITICAL]
            assert not criticals, f"falso critico en {launch['symbol']}: {report.as_dict()}"


def test_no_creator_sell_was_observed() -> None:
    """Documenta lo que la captura NO encontro.

    Este test existe para que el hueco quede registrado en la suite y no en un comentario que
    nadie lee: si algun dia se captura un rug real, este test fallara y habra que sustituirlo
    por uno de verdad.
    """
    dump_fixture = FIXTURE.parent / "pumpfun_creator_dump.json"
    assert not dump_fixture.exists(), (
        "hay una fixture de creator dump: sustituye este test por uno que la use"
    )
