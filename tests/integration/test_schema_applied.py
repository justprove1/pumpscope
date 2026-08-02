"""El esquema se aplica de verdad contra PostgreSQL, y sus invariantes se cumplen.

Los tests unitarios analizan el AST de la migracion; estos la EJECUTAN. Es la diferencia
entre "esta escrito" y "funciona": la primera version de 0001 declaraba una columna `window`,
que es palabra reservada en PostgreSQL, y ningun test de AST podia detectarlo.

Requiere el stack levantado:  make up && make migrate
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from sqlalchemy import Connection, create_engine

pytestmark = pytest.mark.integration

EXPECTED_TABLES = 31
EXPECTED_HYPERTABLES = 18


def _sync_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL no definida")
    return url.replace("+asyncpg", "").replace("postgresql://", "postgresql+psycopg://", 1)


@pytest.fixture
def conn() -> Iterator[Connection]:
    """Conexion nueva por test, siempre revertida.

    Por test y no por modulo a proposito: media docena de estos tests provocan un error de
    integridad deliberado, y en PostgreSQL eso aborta la transaccion entera. Compartir la
    conexion haria que el primer test que falla a proposito tumbase a todos los siguientes.
    """
    engine = create_engine(_sync_url())
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                yield connection
            finally:
                transaction.rollback()
    finally:
        engine.dispose()


def _scalar(conn: Connection, sql: str) -> object:
    return conn.execute(sa.text(sql)).scalar()


def test_all_tables_exist(conn: Connection) -> None:
    count = _scalar(
        conn,
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name <> 'alembic_version'",
    )
    assert count == EXPECTED_TABLES


def test_timeseries_are_hypertables(conn: Connection) -> None:
    count = _scalar(conn, "SELECT count(*) FROM timescaledb_information.hypertables")
    assert count == EXPECTED_HYPERTABLES


def test_no_reserved_word_columns(conn: Connection) -> None:
    """Ninguna columna usa una palabra reservada de SQL.

    Obligaria a entrecomillarla en cada consulta a mano, para siempre, y rompe los CHECK
    escritos como texto crudo.
    """
    reserved = ("window", "values", "order", "user", "table", "select", "from", "grant")
    rows = conn.execute(
        sa.text(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND lower(column_name) = ANY(:reserved)"
        ),
        {"reserved": list(reserved)},
    ).all()
    assert not rows, f"columnas con palabra reservada: {rows}"


def test_audit_logs_rejects_update(conn: Connection) -> None:
    """SPEC.md 24: toda decision debe poder reconstruirse. Un UPDATE lo haria imposible."""
    conn.execute(sa.text("INSERT INTO audit_logs (actor, action) VALUES ('system', 'pytest')"))
    with pytest.raises(sa.exc.ProgrammingError, match="append-only"):
        conn.execute(sa.text("UPDATE audit_logs SET action = 'x' WHERE action = 'pytest'"))


def test_features_reject_leaked_lookback(conn: Connection) -> None:
    """Una feature no puede mirar hacia adelante. Lo impide la base, no solo el codigo."""
    with pytest.raises(sa.exc.IntegrityError, match="ck_features_no_leakage"):
        conn.execute(
            sa.text(
                "INSERT INTO features (observed_at, mint, time_window, feature_set_version, "
                "feature_values, lookback_start_at) VALUES (now(), 'M', '1m', 'v1', "
                "'{}'::jsonb, now() + interval '1 hour')"
            )
        )


def test_live_orders_reject_paper_mode(conn: Connection) -> None:
    """Confundir una orden de papel con una real debe ser estructuralmente imposible."""
    with pytest.raises(sa.exc.IntegrityError, match="ck_live_orders_mode"):
        conn.execute(
            sa.text(
                "INSERT INTO live_orders (mint, side, mode, requested_sol, idempotency_key) "
                "VALUES ('M', 'buy', 'PAPER', 1, 'k')"
            )
        )


def test_active_config_requires_approval(conn: Connection) -> None:
    """RISK_POLICY.md 7: no se activa una configuracion sin aprobacion registrada."""
    with pytest.raises(sa.exc.IntegrityError, match="ck_config_active_requires_approval"):
        conn.execute(
            sa.text(
                "INSERT INTO configuration_versions (created_by, config, is_active) "
                "VALUES ('x', '{}'::jsonb, true)"
            )
        )


def test_money_columns_are_exact_numeric(conn: Connection) -> None:
    """Ni una sola columna de dinero en coma flotante."""
    rows = conn.execute(
        sa.text(
            "SELECT table_name, column_name, data_type FROM information_schema.columns "
            "WHERE table_schema='public' AND data_type IN "
            "('double precision', 'real', 'float')"
        )
    ).all()
    assert not rows, f"columnas en coma flotante: {rows}"
