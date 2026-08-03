"""ExecutionEngine y signer aislado (SPEC.md 15, 16, 30).

**Escrito ANTES de la implementacion** (CLAUDE.md 0.4). Es el componente que mueve dinero
real: su contrato lo dictan los tests, no lo que acabe saliendo del codigo.

Los tres tests obligatorios de la fase estan marcados con su seccion.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from mit_execution import (
    ExecutionMode,
    ExecutionSettings,
    LiveActivationChecklist,
    OrderIntent,
    OrderStatus,
    SignerPolicy,
    SignerRejection,
    TransactionPlan,
    can_enable_live,
    evaluate_signing_request,
)

NOW = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
SOL = 1_000_000_000

PUMPFUN = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
SYSTEM = "11111111111111111111111111111111"
EVIL = "EviL1111111111111111111111111111111111111111"

OUR_WALLET = "TradingWallet11111111111111111111111111111"
BONDING_CURVE = "BondingCurve1111111111111111111111111111111"


def _policy(**over: object) -> SignerPolicy:
    base: dict[str, object] = {
        "program_allowlist": frozenset({PUMPFUN, SYSTEM}),
        "destination_allowlist": frozenset({OUR_WALLET, BONDING_CURVE}),
        "max_order_lamports": 50_000_000,
        "max_daily_lamports": 500_000_000,
        "owner_wallet": OUR_WALLET,
    }
    base.update(over)
    return SignerPolicy(**base)  # type: ignore[arg-type]


def _plan(**over: object) -> TransactionPlan:
    base: dict[str, object] = {
        "program_ids": (PUMPFUN, SYSTEM),
        "destinations": (BONDING_CURVE,),
        "lamports_out": 10_000_000,
        "creates_authority": False,
        "closes_accounts": False,
        "unknown_instructions": 0,
        "recent_blockhash": "hash-1",
        "idempotency_key": "intent-1",
    }
    base.update(over)
    return TransactionPlan(**base)  # type: ignore[arg-type]


# =============================================================================================
# Signer: test obligatorio 2
# =============================================================================================


def test_a_valid_transaction_is_signed() -> None:
    decision = evaluate_signing_request(_plan(), _policy(), spent_today_lamports=0)
    assert decision.approved
    assert decision.rejections == ()


def test_a_program_outside_the_allowlist_is_rejected() -> None:
    """SPEC.md 16: solo programas explicitamente permitidos."""
    decision = evaluate_signing_request(
        _plan(program_ids=(PUMPFUN, EVIL)), _policy(), spent_today_lamports=0
    )
    assert not decision.approved
    assert SignerRejection.PROGRAM_NOT_ALLOWED in decision.rejections


def test_a_destination_outside_the_allowlist_is_rejected() -> None:
    """No se transfiere a direcciones arbitrarias, ni aunque el programa sea legitimo."""
    decision = evaluate_signing_request(
        _plan(destinations=(EVIL,)), _policy(), spent_today_lamports=0
    )
    assert not decision.approved
    assert SignerRejection.DESTINATION_NOT_ALLOWED in decision.rejections


def test_an_order_above_the_per_order_limit_is_rejected() -> None:
    decision = evaluate_signing_request(
        _plan(lamports_out=999_000_000), _policy(), spent_today_lamports=0
    )
    assert not decision.approved
    assert SignerRejection.ORDER_LIMIT in decision.rejections


def test_the_daily_limit_is_enforced_by_the_signer_itself() -> None:
    """El contador diario es del SIGNER, no del backend.

    Si el backend se compromete, el limite sigue en pie. Por eso el signer recibe lo gastado
    y lo comprueba el mismo en vez de fiarse de que el llamante ya lo hizo.
    """
    decision = evaluate_signing_request(
        _plan(lamports_out=40_000_000), _policy(), spent_today_lamports=480_000_000
    )
    assert not decision.approved
    assert SignerRejection.DAILY_LIMIT in decision.rejections


def test_creating_authorities_is_rejected() -> None:
    """Crear o delegar autoridades es como se pierde un token entero."""
    decision = evaluate_signing_request(
        _plan(creates_authority=True), _policy(), spent_today_lamports=0
    )
    assert not decision.approved
    assert SignerRejection.AUTHORITY_CHANGE in decision.rejections


def test_unknown_instructions_are_rejected() -> None:
    """Lo que no se puede decodificar no se firma."""
    decision = evaluate_signing_request(
        _plan(unknown_instructions=1), _policy(), spent_today_lamports=0
    )
    assert not decision.approved
    assert SignerRejection.UNKNOWN_INSTRUCTION in decision.rejections


def test_a_replayed_blockhash_is_rejected() -> None:
    """Idempotencia en el signer: la misma transaccion no se firma dos veces."""
    policy = _policy()
    plan = _plan()
    first = evaluate_signing_request(plan, policy, spent_today_lamports=0, already_signed=())
    second = evaluate_signing_request(
        plan, policy, spent_today_lamports=0, already_signed=(plan.idempotency_key,)
    )
    assert first.approved
    assert not second.approved
    assert SignerRejection.ALREADY_SIGNED in second.rejections


def test_every_rejection_reason_is_reported_not_just_the_first() -> None:
    decision = evaluate_signing_request(
        _plan(program_ids=(EVIL,), destinations=(EVIL,), lamports_out=999_000_000),
        _policy(),
        spent_today_lamports=0,
    )
    assert len(decision.rejections) >= 3


def test_the_signer_contract_never_mentions_a_private_key() -> None:
    """SECURITY.md 1: ninguna firma de este modulo expone material criptografico."""
    import inspect

    import mit_execution.signing as signing

    source = inspect.getsource(signing).lower()
    for forbidden in ("seed_phrase", "mnemonic", "secret_key", "private_key"):
        assert forbidden not in source, f"el contrato menciona {forbidden}"


def test_every_request_is_auditable() -> None:
    """SPEC.md 16: se registra CADA solicitud de firma, aprobada o no."""
    decision = evaluate_signing_request(_plan(), _policy(), spent_today_lamports=0)
    record = decision.as_dict()
    for field in ("approved", "rejections", "idempotency_key", "lamports_out"):
        assert field in record


# =============================================================================================
# Idempotencia: test obligatorio 1
# =============================================================================================


def _intent(key: str = "decision-1") -> OrderIntent:
    return OrderIntent(
        idempotency_key=key,
        mint="Mint1111111111111111111111111111111111111",
        side="buy",
        lamports=10_000_000,
        created_at=NOW,
    )


def test_the_same_intent_never_produces_two_orders() -> None:
    """El caso que cuesta dinero: timeout y reintento del llamante."""
    from mit_execution import OrderLedger

    ledger = OrderLedger()
    intent = _intent()

    first = ledger.reserve(intent)
    second = ledger.reserve(intent)

    assert first is not None
    assert second is None, "un reintento de la MISMA decision no puede abrir otra orden"
    assert ledger.count() == 1


def test_a_timeout_does_not_release_the_key() -> None:
    """Ante timeout NO se sabe si la transaccion entro. Reintentar a ciegas es doble gasto."""
    from mit_execution import OrderLedger

    ledger = OrderLedger()
    intent = _intent()
    ledger.reserve(intent)
    ledger.mark(intent.idempotency_key, OrderStatus.TIMEOUT)

    assert ledger.reserve(intent) is None
    assert ledger.status(intent.idempotency_key) is OrderStatus.TIMEOUT


def test_a_confirmed_order_stays_closed() -> None:
    from mit_execution import OrderLedger

    ledger = OrderLedger()
    intent = _intent()
    ledger.reserve(intent)
    ledger.mark(intent.idempotency_key, OrderStatus.CONFIRMED)
    assert ledger.reserve(intent) is None


def test_a_failed_order_can_be_retried_a_bounded_number_of_times() -> None:
    """Un fallo CONFIRMADO si es reintentable, pero con limite: no se persigue el precio."""
    from mit_execution import OrderLedger

    ledger = OrderLedger(max_retries=2)
    intent = _intent()
    for _ in range(2):
        ledger.reserve(intent)
        ledger.mark(intent.idempotency_key, OrderStatus.FAILED)
    assert ledger.reserve(intent) is None, "se agotaron los reintentos"


def test_different_decisions_get_different_orders() -> None:
    from mit_execution import OrderLedger

    ledger = OrderLedger()
    assert ledger.reserve(_intent("a")) is not None
    assert ledger.reserve(_intent("b")) is not None
    assert ledger.count() == 2


# =============================================================================================
# LIVE bloqueado: test obligatorio 3
# =============================================================================================


def _checklist(**over: object) -> LiveActivationChecklist:
    base: dict[str, object] = {
        "env_enabled": True,
        "simulated_trades": 1500,
        "has_out_of_sample": True,
        "costs_included": True,
        "profit_factor": 1.6,
        "max_drawdown": 0.06,
        "survives_without_outliers": True,
        "stress_tests_passed": True,
        "reconciliation_verified": True,
        "kill_switches_verified": True,
        "wallet_is_dedicated": True,
        "wallet_capital_limited": True,
        "signer_mode": "local_encrypted",
        "ui_confirmed": True,
        "pin_verified": True,
        "operator": "matteo",
    }
    base.update(over)
    return LiveActivationChecklist(**base)  # type: ignore[arg-type]


def test_the_env_flag_alone_does_not_enable_live() -> None:
    """El test que da sentido a toda esta fase.

    `ENABLE_LIVE_TRADING=true` es UNA condicion de quince, no un interruptor. Si bastara,
    un despiste en un `.env` moveria dinero real.
    """
    only_env = LiveActivationChecklist(env_enabled=True)
    verdict = can_enable_live(only_env)
    assert not verdict.allowed
    assert len(verdict.missing) >= 10


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("env_enabled", False),
        ("simulated_trades", 999),
        ("has_out_of_sample", False),
        ("costs_included", False),
        ("profit_factor", 0.9),
        ("max_drawdown", 0.5),
        ("survives_without_outliers", False),
        ("stress_tests_passed", False),
        ("reconciliation_verified", False),
        ("kill_switches_verified", False),
        ("wallet_is_dedicated", False),
        ("wallet_capital_limited", False),
        ("signer_mode", "disabled"),
        ("ui_confirmed", False),
        ("pin_verified", False),
        ("operator", ""),
    ],
    ids=[
        "env",
        "operaciones",
        "fuera_muestra",
        "costes",
        "profit_factor",
        "drawdown",
        "outliers",
        "stress",
        "reconciliacion",
        "kill_switches",
        "wallet_dedicada",
        "capital_limitado",
        "signer",
        "confirmacion_ui",
        "pin",
        "operador",
    ],
)
def test_any_missing_requirement_blocks_live(field: str, value: object) -> None:
    """SPEC.md 30: falta UNA y no se activa. Sin excepciones."""
    verdict = can_enable_live(_checklist(**{field: value}))
    assert not verdict.allowed
    assert verdict.missing


def test_live_is_allowed_only_with_everything_satisfied() -> None:
    verdict = can_enable_live(_checklist())
    assert verdict.allowed
    assert verdict.missing == ()


def test_the_verdict_lists_exactly_what_is_missing() -> None:
    """Para que el operador sepa que le falta, no solo que no puede."""
    verdict = can_enable_live(LiveActivationChecklist(env_enabled=True, simulated_trades=10))
    assert any("1000" in item or "1.000" in item for item in verdict.missing)


def test_settings_default_to_the_safest_mode() -> None:
    """CLAUDE.md 1: se arranca en DRY_RUN, siempre."""
    settings = ExecutionSettings()
    assert settings.mode is ExecutionMode.DRY_RUN
    assert not settings.live_enabled


def test_execution_settings_carry_every_limit_of_spec_15() -> None:
    settings = ExecutionSettings()
    for field in (
        "max_quote_age_ms",
        "max_price_impact_bps",
        "max_slippage_bps",
        "max_priority_fee_lamports",
        "max_retries",
        "transaction_timeout_ms",
    ):
        assert hasattr(settings, field), f"falta {field} de SPEC.md 15"


def test_min_expected_output_is_required_for_every_order() -> None:
    """Sin salida minima garantizada, un sandwich se lleva la operacion entera."""
    with pytest.raises(ValueError, match="min_expected_output"):
        OrderIntent(
            idempotency_key="k",
            mint="M",
            side="buy",
            lamports=10_000_000,
            created_at=NOW,
            min_expected_output=0,
            require_min_output=True,
        )


def test_quote_age_is_checked_against_the_limit() -> None:
    from mit_execution import quote_is_fresh

    settings = ExecutionSettings(max_quote_age_ms=1500)
    assert quote_is_fresh(NOW, NOW + timedelta(milliseconds=900), settings)
    assert not quote_is_fresh(NOW, NOW + timedelta(milliseconds=2500), settings)
