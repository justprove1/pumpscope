"""Los valores por defecto son seguros y no hay codigo escrito a ciegas.

Estos tests protegen los invariables de CLAUDE.md 1. Son baratos y detectan la clase de
error mas cara que existe en este proyecto: que algo arranque operando con dinero real.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = REPO / ".env.example"

# Variables cuyo valor por defecto NO puede ser otro.
MANDATORY_DEFAULTS = {
    "ENABLE_LIVE_TRADING": "false",
    "SIGNER_MODE": "disabled",
    "APP_ENV": "local",
}

# SPEC.md 28: minimo obligatorio.
REQUIRED_ENV_VARS = [
    "APP_ENV",
    "DATABASE_URL",
    "REDIS_URL",
    "HELIUS_API_KEY",
    "HELIUS_RPC_URL",
    "HELIUS_WSS_URL",
    "JUPITER_API_KEY",
    "DEXSCREENER_BASE_URL",
    "X_API_KEY",
    "GDELT_ENABLED",
    "TELEGRAM_BOT_TOKEN",
    "DISCORD_WEBHOOK_URL",
    "ENABLE_LIVE_TRADING",
    "LIVE_TRADING_MAX_DAILY_SOL",
    "LIVE_TRADING_MAX_ORDER_SOL",
    "LIVE_TRADING_MAX_TOTAL_EXPOSURE_SOL",
    "SIGNER_MODE",
    "ENCRYPTED_KEY_PATH",
    "KEY_ENCRYPTION_PASSWORD_FILE",
    "MAX_SLIPPAGE_BPS",
    "MAX_PRICE_IMPACT_PERCENT",
    "MAX_DAILY_LOSS_PERCENT",
    "MAX_DRAWDOWN_PERCENT",
]


def _env_pairs() -> dict[str, str]:
    pairs: dict[str, str] = {}
    for raw in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        pairs[key.strip()] = value.split("#")[0].strip()
    return pairs


def test_every_required_variable_is_documented() -> None:
    missing = [name for name in REQUIRED_ENV_VARS if name not in _env_pairs()]
    assert not missing, f"faltan en .env.example (SPEC.md 28): {missing}"


def test_dangerous_defaults_are_off() -> None:
    pairs = _env_pairs()
    for key, expected in MANDATORY_DEFAULTS.items():
        assert pairs[key] == expected, f"{key} deberia ser {expected!r}, es {pairs[key]!r}"


def test_no_real_credentials_in_example() -> None:
    """Ninguna variable de credencial trae valor. Si trae, es un secreto filtrado."""
    suspicious = ("_KEY", "_TOKEN", "_SECRET", "_DSN", "_PASSWORD")
    leaked = [
        key
        for key, value in _env_pairs().items()
        if any(key.endswith(s) for s in suspicious) and value and not value.startswith("${")
    ]
    assert not leaked, f"posibles secretos en .env.example: {leaked}"


def test_env_is_gitignored() -> None:
    gitignore = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert re.search(r"^\.env$", gitignore, re.MULTILINE)
    assert "!.env.example" in gitignore


def test_signer_port_is_not_published() -> None:
    """SECURITY.md 2: el signer solo es alcanzable desde la red interna de Docker."""
    compose = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    signer_block = compose.split("signer:", 1)[1].split("\n  web:", 1)[0]
    assert "ports:" not in signer_block, "el signer NO puede publicar puertos al host"
    assert "mit-public" not in signer_block, "el signer no puede estar en la red publica"


def test_no_provider_adapter_written_without_verified_endpoints() -> None:
    """SPEC.md 32: ningun adaptador antes de verificar su documentacion real."""
    adapters = REPO / "packages" / "providers" / "mit_providers" / "adapters"
    modules = [p.name for p in adapters.glob("*.py") if p.name != "__init__.py"]
    assert not modules, f"hay adaptadores escritos en Fase 0: {modules}"


def test_every_fixture_declares_its_provenance() -> None:
    """CLAUDE.md 2: las fixtures se capturan de respuestas reales, no se escriben.

    No se puede comprobar por inspeccion que un JSON venga de una API de verdad, asi que se
    exige lo siguiente mejor: que cada fixture declare de donde y cuando salio. Una fixture
    inventada tendria que mentir explicitamente en `_fixture_meta`, y eso ya no es un
    descuido.
    """
    import json

    for path in (REPO / "tests" / "fixtures").glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        meta = payload.get("_fixture_meta")
        assert isinstance(meta, dict), f"{path.name} no declara _fixture_meta"
        for required in ("captured_at_utc", "source", "method"):
            assert meta.get(required), f"{path.name}: _fixture_meta.{required} vacio"
