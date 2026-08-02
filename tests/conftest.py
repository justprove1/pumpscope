"""Configuracion comun de tests.

Los tests de la Fase 0 verifican el ANDAMIAJE, no logica de negocio: que el arbol existe,
que el esquema declara lo que SPEC.md 23 pide, y que los valores por defecto son seguros.
Estan escritos para correr con cualquier interprete, porque comprueban archivos, no codigo.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Raiz del monorepo."""
    return REPO_ROOT
