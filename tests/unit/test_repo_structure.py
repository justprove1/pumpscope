"""El arbol del monorepo cumple SPEC.md 26 y los archivos obligatorios de SPEC.md 27."""

from __future__ import annotations

from pathlib import Path

import pytest

# SPEC.md 26, literal.
REQUIRED_DIRS = [
    "apps/api",
    "apps/web",
    "apps/worker",
    "apps/signer",
    "packages/solana",
    "packages/pumpfun",
    "packages/providers",
    "packages/data-models",
    "packages/features",
    "packages/strategies",
    "packages/risk",
    "packages/execution",
    "packages/simulation",
    "packages/ml",
    "packages/narratives",
    "packages/notifications",
    "packages/observability",
    "packages/shared",
    "infrastructure/docker",
    "infrastructure/grafana",
    "infrastructure/prometheus",
    "infrastructure/migrations",
    "tests/unit",
    "tests/integration",
    "tests/e2e",
    "tests/load",
    "tests/fixtures",
    "tests/replay",
    "docs",
]

# SPEC.md 27, literal.
REQUIRED_FILES = [
    "README.md",
    "ARCHITECTURE.md",
    "SECURITY.md",
    "RISK_POLICY.md",
    "DATA_PROVIDERS.md",
    "LIVE_TRADING_CHECKLIST.md",
    "BACKTESTING.md",
    "SIMULATION.md",
    "API.md",
    ".env.example",
    "docker-compose.yml",
    "Makefile",
    "pyproject.toml",
    "package.json",
    ".github/workflows/ci.yml",
    "infrastructure/migrations/env.py",
    "config/settings.example.yaml",
]

# Cada directorio kebab-case de /packages contiene su modulo Python con prefijo mit_.
PYTHON_MODULES = [
    "packages/shared/mit_shared",
    "packages/data-models/mit_data_models",
    "packages/observability/mit_observability",
    "packages/solana/mit_solana",
    "packages/pumpfun/mit_pumpfun",
    "packages/providers/mit_providers",
    "packages/features/mit_features",
    "packages/narratives/mit_narratives",
    "packages/strategies/mit_strategies",
    "packages/risk/mit_risk",
    "packages/execution/mit_execution",
    "packages/simulation/mit_simulation",
    "packages/ml/mit_ml",
    "packages/notifications/mit_notifications",
    "apps/api/mit_api",
    "apps/worker/mit_worker",
    "apps/signer/mit_signer",
]


@pytest.mark.parametrize("relative", REQUIRED_DIRS)
def test_required_directory_exists(repo_root: Path, relative: str) -> None:
    assert (repo_root / relative).is_dir(), f"falta el directorio {relative} (SPEC.md 26)"


@pytest.mark.parametrize("relative", REQUIRED_FILES)
def test_required_file_exists(repo_root: Path, relative: str) -> None:
    path = repo_root / relative
    assert path.is_file(), f"falta el archivo {relative} (SPEC.md 27)"
    assert path.stat().st_size > 0, f"{relative} esta vacio"


@pytest.mark.parametrize("relative", PYTHON_MODULES)
def test_python_module_is_importable_package(repo_root: Path, relative: str) -> None:
    """Cada modulo declara __init__.py y py.typed.

    Sin `py.typed`, mypy trata el paquete como sin tipos al instalarlo y el modo strict se
    vuelve decorativo.
    """
    module = repo_root / relative
    assert (module / "__init__.py").is_file(), f"{relative} no es un paquete Python"
    assert (module / "py.typed").is_file(), f"{relative} no declara py.typed"


def test_pyproject_declares_every_module(repo_root: Path) -> None:
    """Un modulo que no este en pyproject.toml no se instala y falla solo en produccion."""
    content = (repo_root / "pyproject.toml").read_text(encoding="utf-8")
    for module in PYTHON_MODULES:
        assert f'"{module}"' in content, f"{module} no esta declarado en pyproject.toml"


def test_legacy_pumpscope_readme_preserved(repo_root: Path) -> None:
    """El README del proyecto anterior se conserva; no se sobrescribio."""
    assert (repo_root / "README.pumpscope.md").is_file()
