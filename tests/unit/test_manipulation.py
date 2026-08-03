"""Detectores de manipulacion (SPEC.md 8).

Los escenarios de este archivo estan CONSTRUIDOS, no capturados. Se dice aqui de forma
explicita para no confundirlos con las fixtures reales de `tests/fixtures/`, que si vienen de
mainnet. Cada escenario reproduce un patron documentado en SPEC.md 8 con los datos minimos
que lo caracterizan.

Un escenario construido vale para demostrar que el detector dispara cuando debe y calla
cuando no debe. Lo que NO demuestra es que el patron exista tal cual en la cadena: eso exige
una captura real, y queda pendiente (ver reporte de fase).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from mit_strategies.manipulation import (
    DETECTORS,
    Finding,
    Severity,
    TokenContext,
    TradeRecord,
    WalletInfo,
    analyze,
)

LAUNCH = datetime(2026, 8, 3, 12, 0, 0, tzinfo=UTC)
SOL = 1_000_000_000


def _trade(wallet: str, side: str, sol: float, *, slot: int, seconds: int) -> TradeRecord:
    return TradeRecord(
        signature=f"sig-{wallet}-{slot}-{side}-{seconds}",
        slot=slot,
        block_time=LAUNCH + timedelta(seconds=seconds),
        wallet=wallet,
        side=side,
        sol_amount=int(sol * SOL),
        token_amount=int(sol * 1_000_000),
    )


def _clean_token() -> TokenContext:
    """Un token sin nada raro: compradores distintos, slots distintos, importes distintos."""
    return TokenContext(
        mint="CleanMint1111111111111111111111111111111111",
        creator="Creator11111111111111111111111111111111111",
        created_at=LAUNCH,
        total_supply=1_000_000_000_000_000,
        name="Perro Feliz",
        symbol="PERRO",
        uri="https://ipfs.io/ipfs/abc",
        trades=tuple(
            _trade(f"buyer{i}", "buy", 0.1 + i * 0.037, slot=1000 + i * 7, seconds=i * 30)
            for i in range(12)
        ),
        holders={f"buyer{i}": 1000 + i * 313 for i in range(12)},
        wallets={
            f"buyer{i}": WalletInfo(f"buyer{i}", first_seen_at=LAUNCH - timedelta(days=90 + i))
            for i in range(12)
        },
    )


def _rug_token() -> TokenContext:
    """Rug clasico: el creador compra al lanzar y vende todo a los 6 minutos."""
    trades = [
        _trade("Creator11111111111111111111111111111111111", "buy", 2.0, slot=1000, seconds=1)
    ]
    trades += [
        _trade(f"victima{i}", "buy", 0.2 + i * 0.05, slot=1010 + i * 3, seconds=30 + i * 20)
        for i in range(8)
    ]
    trades += [
        _trade("Creator11111111111111111111111111111111111", "sell", 3.4, slot=1200, seconds=360),
        _trade("Creator11111111111111111111111111111111111", "sell", 1.1, slot=1201, seconds=365),
    ]
    return TokenContext(
        mint="RugMint111111111111111111111111111111111111",
        creator="Creator11111111111111111111111111111111111",
        created_at=LAUNCH,
        total_supply=1_000_000_000_000_000,
        name="Official Verified Coin",
        symbol="VERIFIED",
        uri="https://ipfs.io/ipfs/xyz",
        trades=tuple(trades),
        holders={f"victima{i}": 5000 for i in range(8)},
        wallets={f"victima{i}": WalletInfo(f"victima{i}") for i in range(8)},
        creator_previous_tokens=7,
        creator_previous_dumps=5,
    )


def _insider_cluster_token() -> TokenContext:
    """Insider cluster: seis wallets financiadas por la misma fuente, mismo slot, mismo importe."""
    insiders = [f"insider{i}" for i in range(6)]
    trades = [_trade(w, "buy", 0.5, slot=1000, seconds=1) for w in insiders]
    trades += [
        _trade(f"publico{i}", "buy", 0.11 + i * 0.03, slot=1100 + i * 5, seconds=120 + i * 40)
        for i in range(5)
    ]
    return TokenContext(
        mint="InsiderMint11111111111111111111111111111111",
        creator="Creator22222222222222222222222222222222222",
        created_at=LAUNCH,
        total_supply=1_000_000_000_000_000,
        name="Gato Cohete",
        symbol="GATO",
        uri="https://ipfs.io/ipfs/def",
        trades=tuple(trades),
        holders={**dict.fromkeys(insiders, 100000), **{f"publico{i}": 6_000 for i in range(5)}},
        wallets={
            **{
                w: WalletInfo(
                    w,
                    funded_by="Financiador333333333333333333333333333333",
                    first_seen_at=LAUNCH - timedelta(minutes=12),
                )
                for w in insiders
            },
            **{
                f"publico{i}": WalletInfo(f"publico{i}", first_seen_at=LAUNCH - timedelta(days=200))
                for i in range(5)
            },
        },
    )


# --- Casos que DEBEN marcarse -------------------------------------------------------------


def test_rug_is_flagged_with_high_score() -> None:
    report = analyze(_rug_token())
    assert report.score >= 60, f"score demasiado bajo para un rug: {report.score}"
    assert "creator_dumping" in {f.detector for f in report.findings}
    assert report.worst_severity == Severity.CRITICAL


def test_rug_reasons_contain_concrete_numbers() -> None:
    """SPEC.md 8 exige razones con cifras, no etiquetas."""
    report = analyze(_rug_token())
    dumping = next(f for f in report.findings if f.detector == "creator_dumping")
    assert "SOL" in dumping.reason
    assert "minutos" in dumping.reason
    assert dumping.evidence["sell_count"] == 2
    assert dumping.evidence["minutes_after_launch"] == 6.0

    history = next(f for f in report.findings if f.detector == "creator_history")
    assert "5 de sus ultimos 7 tokens" in history.reason


def test_insider_cluster_is_flagged() -> None:
    report = analyze(_insider_cluster_token())
    detectors = {f.detector for f in report.findings}
    assert "same_slot_bundle" in detectors
    assert "identical_amounts" in detectors
    assert "common_funding_cluster" in detectors
    assert report.score >= 60


def test_insider_cluster_reason_matches_the_spec_example() -> None:
    """La frase debe parecerse a la del ejemplo de SPEC.md 8."""
    report = analyze(_insider_cluster_token())
    funding = next(f for f in report.findings if f.detector == "common_funding_cluster")
    assert "% del supply pertenece a" in funding.reason
    assert "financiadas por la misma direccion" in funding.reason
    assert funding.evidence["wallets"] == 6


def test_creator_funded_buyers_are_flagged() -> None:
    context = _insider_cluster_token()
    poisoned = TokenContext(
        **{
            **{k: getattr(context, k) for k in context.__slots__},
            "wallets": {
                w: WalletInfo(w, funded_by=context.creator) for w in ("insider0", "insider1")
            },
        }
    )
    report = analyze(poisoned)
    assert "creator_funded_buyers" in {f.detector for f in report.findings}


def test_impersonation_is_detected_but_is_not_by_itself_conclusive() -> None:
    report = analyze(_rug_token())
    impersonation = next(f for f in report.findings if f.detector == "impersonation")
    # "Official"/"Verified": sospechoso, pero no critico por si solo.
    assert impersonation.severity in (Severity.LOW, Severity.MEDIUM)


# --- Casos que NO deben marcarse ----------------------------------------------------------


def test_clean_token_scores_low() -> None:
    report = analyze(_clean_token())
    assert report.score <= 20, f"falso positivo: {report.as_dict()}"
    assert report.worst_severity in (None, Severity.LOW, Severity.INFO)


def test_clean_token_has_no_critical_findings() -> None:
    report = analyze(_clean_token())
    assert not [f for f in report.findings if f.severity == Severity.CRITICAL]


def test_empty_token_does_not_crash_or_accuse() -> None:
    """Sin datos no se acusa a nadie: ausencia de evidencia no es evidencia."""
    empty = TokenContext(
        mint="EmptyMint111111111111111111111111111111111",
        creator="Nobody11111111111111111111111111111111111",
        created_at=LAUNCH,
        total_supply=0,
        uri="https://x",
    )
    report = analyze(empty)
    assert report.score == 0
    assert report.detectors_run == len(DETECTORS)


# --- Property tests ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "context",
    [_clean_token(), _rug_token(), _insider_cluster_token()],
    ids=["clean", "rug", "insider"],
)
def test_score_is_within_range(context: TokenContext) -> None:
    """Property test: el score SIEMPRE queda en 0-100."""
    report = analyze(context)
    assert 0 <= report.score <= 100


@pytest.mark.parametrize(
    "context",
    [_clean_token(), _rug_token(), _insider_cluster_token()],
    ids=["clean", "rug", "insider"],
)
def test_score_is_deterministic(context: TokenContext) -> None:
    """Property test: mismo contexto, mismo informe, siempre. Incluidas las razones."""
    first = analyze(context).as_dict()
    for _ in range(25):
        assert analyze(context).as_dict() == first


def test_score_cannot_exceed_100_even_with_everything_wrong() -> None:
    """Un token con todos los patrones a la vez sigue acotado a 100."""
    worst = _rug_token()
    combined = TokenContext(
        **{
            **{k: getattr(worst, k) for k in worst.__slots__},
            "trades": worst.trades + _insider_cluster_token().trades,
            "wallets": _insider_cluster_token().wallets,
            "holders": _insider_cluster_token().holders,
        }
    )
    assert analyze(combined).score == 100


def test_every_finding_carries_evidence() -> None:
    """Un hallazgo sin evidencia no es auditable y no deberia existir."""
    for context in (_rug_token(), _insider_cluster_token()):
        for finding in analyze(context).findings:
            assert finding.reason
            assert finding.evidence, f"{finding.detector} no aporta evidencia"


def test_a_broken_detector_does_not_take_down_the_rest() -> None:
    """Perder una comprobacion es malo; perder las doce por un bug en una, peor."""

    def broken(_: TokenContext) -> list[Finding]:
        msg = "detector roto a proposito"
        raise RuntimeError(msg)

    report = analyze(_rug_token(), detectors=[broken, *DETECTORS])
    assert any(f.severity == Severity.INFO and "fallo" in f.reason for f in report.findings)
    assert "creator_dumping" in {f.detector for f in report.findings}


def test_report_serializes_for_persistence_and_api() -> None:
    payload = analyze(_rug_token()).as_dict()
    assert set(payload) == {"mint", "score", "detectors_run", "worst_severity", "findings"}
    assert all("reason" in f and "evidence" in f for f in payload["findings"])
