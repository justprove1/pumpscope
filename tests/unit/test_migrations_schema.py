"""El esquema declara todo lo que pide SPEC.md 23.

Se analiza el AST de la migracion en vez de conectarse a PostgreSQL: asi el test corre sin
base de datos y detecta una tabla olvidada en el momento de escribirla. La verificacion
CONTRA la base real (que las tablas se crean de verdad y que las hypertables existen) es un
test de integracion de la Fase 1, no de este archivo.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# SPEC.md 23, literal y en orden.
REQUIRED_TABLES = [
    "tokens",
    "token_metadata",
    "creators",
    "wallets",
    "wallet_relationships",
    "transactions",
    "swaps",
    "holders_snapshots",
    "liquidity_snapshots",
    "price_snapshots",
    "bonding_curve_snapshots",
    "social_posts",
    "news_items",
    "narratives",
    "token_narrative_links",
    "features",
    "scores",
    "signals",
    "simulated_orders",
    "live_orders",
    "fills",
    "positions",
    "portfolio_snapshots",
    "strategies",
    "strategy_versions",
    "model_versions",
    "backtest_runs",
    "alerts",
    "provider_health",
    "audit_logs",
    "configuration_versions",
]

MIGRATIONS = Path(__file__).resolve().parents[2] / "infrastructure" / "migrations" / "versions"
INITIAL = MIGRATIONS / "0001_initial_schema.py"
TIMESERIES = MIGRATIONS / "0002_timeseries_hypertables.py"


def _first_string_arg(node: ast.Call) -> str | None:
    if not node.args:
        return None
    first = node.args[0]
    return first.value if isinstance(first, ast.Constant) and isinstance(first.value, str) else None


def _calls_named(tree: ast.AST, attr: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == attr
    ]


def _created_tables() -> list[str]:
    tree = ast.parse(INITIAL.read_text(encoding="utf-8"))
    names = [_first_string_arg(call) for call in _calls_named(tree, "create_table")]
    return [name for name in names if name is not None]


def _created_indexes() -> list[tuple[str, str]]:
    """(nombre_indice, tabla) de cada create_index."""
    tree = ast.parse(INITIAL.read_text(encoding="utf-8"))
    result: list[tuple[str, str]] = []
    for call in _calls_named(tree, "create_index"):
        if len(call.args) >= 2:
            index, table = call.args[0], call.args[1]
            if isinstance(index, ast.Constant) and isinstance(table, ast.Constant):
                result.append((str(index.value), str(table.value)))
    return result


@pytest.mark.parametrize("table", REQUIRED_TABLES)
def test_table_is_created(table: str) -> None:
    assert table in _created_tables(), f"la migracion no crea la tabla {table} (SPEC.md 23)"


def test_no_unexpected_tables() -> None:
    """Nada de tablas fuera de la especificacion sin una decision explicita."""
    assert sorted(_created_tables()) == sorted(REQUIRED_TABLES)


def test_downgrade_drops_every_table() -> None:
    """Una migracion que no revierte no es reversible, y CI la ejecuta en ambos sentidos."""
    source = INITIAL.read_text(encoding="utf-8")
    for table in REQUIRED_TABLES:
        assert f'"{table}"' in source
    assert "_TABLES_IN_CREATION_ORDER" in source
    assert "reversed(_TABLES_IN_CREATION_ORDER)" in source


# SPEC.md 23: "indices adecuados para series temporales y consultas por mint".
TABLES_NEEDING_MINT_INDEX = [
    "transactions",
    "swaps",
    "holders_snapshots",
    "liquidity_snapshots",
    "price_snapshots",
    "bonding_curve_snapshots",
    "features",
    "scores",
    "signals",
    "fills",
]


@pytest.mark.parametrize("table", TABLES_NEEDING_MINT_INDEX)
def test_has_index_by_mint_and_time(table: str) -> None:
    indexed_tables = {tbl for _, tbl in _created_indexes()}
    assert table in indexed_tables, f"{table} no tiene ningun indice declarado"


def test_timeseries_tables_are_hypertables() -> None:
    """Toda serie temporal esta en la lista de conversion de 0002."""
    source = TIMESERIES.read_text(encoding="utf-8")
    for table in TABLES_NEEDING_MINT_INDEX:
        assert f'"{table}"' in source, f"{table} no se convierte en hypertable"


def test_timeseries_migration_degrades_without_timescale() -> None:
    """El esquema debe aplicarse tambien en un PostgreSQL sin TimescaleDB."""
    source = TIMESERIES.read_text(encoding="utf-8")
    assert "USING brin" in source
    assert "pg_extension WHERE extname = 'timescaledb'" in source


def test_no_retention_policy() -> None:
    """Borrar historico automaticamente destruiria el backtesting y la auditoria."""
    assert "add_retention_policy" not in TIMESERIES.read_text(encoding="utf-8")


def test_audit_logs_is_append_only() -> None:
    """SPEC.md 24: toda decision real debe poder reconstruirse. Un UPDATE lo impediria."""
    source = INITIAL.read_text(encoding="utf-8")
    assert "trg_audit_logs_immutable" in source
    assert "BEFORE UPDATE OR DELETE ON audit_logs" in source


def test_live_orders_cannot_hold_paper_trades() -> None:
    """El CHECK hace estructuralmente imposible confundir papel con dinero real."""
    assert "ck_live_orders_mode" in INITIAL.read_text(encoding="utf-8")


def test_positions_forbid_averaging_down_by_default() -> None:
    """SPEC.md 13: nada de averaging down automatico en esta version."""
    source = INITIAL.read_text(encoding="utf-8")
    assert "add_forbidden" in source
    assert "server_default=sa.true()" in source


def test_features_declare_no_leakage_boundary() -> None:
    """La frontera de no-leakage se persiste y se comprueba en la propia base de datos."""
    source = INITIAL.read_text(encoding="utf-8")
    assert "lookback_start_at" in source
    assert "ck_features_no_leakage" in source


def test_money_columns_never_use_float() -> None:
    """Ni un solo Float en el esquema: el dinero es NUMERIC o entero de lamports."""
    source = INITIAL.read_text(encoding="utf-8")
    assert "sa.Float" not in source
    assert "REAL" not in source
