"""Las interfaces de proveedor son abstractas de verdad.

Requieren Python 3.12 (SPEC.md 3) porque los contratos usan `StrEnum`. En un interprete
anterior se saltan en vez de fallar: el codigo objetivo es 3.12, no el del portatil.
"""

from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.version_info < (3, 12),
    reason="El proyecto requiere Python 3.12 (SPEC.md 3); este interprete es anterior.",
)

ABSTRACT_PROVIDERS = [
    "OnChainReadProvider",
    "EventStreamProvider",
    "TokenDiscoveryProvider",
    "BondingCurveProvider",
    "MarketDataProvider",
    "QuoteProvider",
    "HolderProvider",
    "WalletGraphProvider",
    "SocialProvider",
    "NewsProvider",
    "TokenRiskProvider",
    "SigningService",
]


@pytest.mark.parametrize("name", ABSTRACT_PROVIDERS)
def test_provider_cannot_be_instantiated(name: str) -> None:
    """Instanciar una interfaz debe fallar. Si no falla, no es una interfaz."""
    import mit_providers

    cls = getattr(mit_providers, name, None) or getattr(
        __import__("mit_providers.base", fromlist=[name]), name
    )
    with pytest.raises(TypeError):
        cls()


def test_narrative_assessment_rejects_unknown_fields() -> None:
    """La salida del LLM se valida entera o se rechaza entera (CLAUDE.md 1)."""
    from mit_data_models import NarrativeAssessment
    from pydantic import ValidationError

    # `model_validate` y no el constructor: asi es como entrara de verdad la salida del LLM,
    # como JSON deserializado, no como kwargs escritos a mano.
    valid: dict[str, object] = {
        "narrative": "robotica humanoide",
        "state": "ACCELERATING",
        "score": 87.0,
        "confidence": 0.76,
        "reasons": ["menciones unicas +320% en 20 minutos"],
    }
    NarrativeAssessment.model_validate(valid)

    # Un LLM que intente colar un campo que decide dinero debe hacer fallar la validacion
    # ENTERA, no que el campo se ignore en silencio.
    with pytest.raises(ValidationError):
        NarrativeAssessment.model_validate({**valid, "recommended_size_sol": 5.0})


def test_signing_contract_never_exposes_private_key() -> None:
    """SECURITY.md 1: ninguna firma del contrato de firma menciona una clave privada."""
    import inspect

    from mit_providers.base import signing

    source = inspect.getsource(signing).lower()
    for forbidden in ("private_key", "secret_key", "seed_phrase", "mnemonic", "keypair"):
        assert forbidden not in source, f"el contrato del signer menciona {forbidden}"


def test_enums_match_migration_checks() -> None:
    """Los enums de Python y los CHECK de la migracion no pueden divergir."""
    from pathlib import Path

    from mit_data_models import NARRATIVE_STATE_VALUES, SIGNAL_TYPE_VALUES

    migration = (
        Path(__file__).resolve().parents[2]
        / "infrastructure/migrations/versions/0001_initial_schema.py"
    ).read_text(encoding="utf-8")

    for value in SIGNAL_TYPE_VALUES:
        assert f"'{value}'" in migration, f"{value} no esta en ck_signals_type"
    for value in NARRATIVE_STATE_VALUES:
        assert f"'{value}'" in migration, f"{value} no esta en ck_narratives_state"
