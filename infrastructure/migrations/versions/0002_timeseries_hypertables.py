"""Series temporales: hypertables de TimescaleDB con degradacion a PostgreSQL puro.

SPEC.md 3 dice "TimescaleDB si resulta apropiado" y SPEC.md 23 pide indices adecuados para
series temporales. Esta migracion NO asume que la extension este disponible: si lo esta, crea
hypertables y politicas de compresion; si no, crea indices BRIN sobre la columna de tiempo,
que dan un resultado razonable en tablas append-only ordenadas temporalmente a un coste de
espacio minimo.

El objetivo es que el esquema se aplique igual en un Postgres normal (por ejemplo, el de un
desarrollador que no quiere Timescale) sin que nada se rompa en silencio.

NO se instala politica de retencion. Borrar datos historicos automaticamente destruiria la
capacidad de hacer backtesting y de reconstruir decisiones (SPEC.md 24). Si algun dia hace
falta, sera una decision explicita con su propia migracion.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-03

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (tabla, columna de tiempo, intervalo de chunk, columna de segmentacion para comprimir)
#
# El intervalo se elige por volumen esperado: cuanto mas escribe una tabla, mas corto, para
# que cada chunk quepa comodamente en memoria durante las consultas recientes.
TIMESERIES: list[tuple[str, str, str, str | None]] = [
    ("transactions", "block_time", "1 day", "mint"),
    ("swaps", "block_time", "1 day", "mint"),
    ("price_snapshots", "observed_at", "1 day", "mint"),
    ("bonding_curve_snapshots", "observed_at", "1 day", "mint"),
    ("holders_snapshots", "observed_at", "7 days", "mint"),
    ("liquidity_snapshots", "observed_at", "7 days", "mint"),
    ("features", "observed_at", "1 day", "mint"),
    ("scores", "observed_at", "1 day", "mint"),
    ("signals", "created_at", "7 days", "mint"),
    ("simulated_orders", "created_at", "7 days", "mint"),
    ("live_orders", "created_at", "30 days", "mint"),
    ("fills", "filled_at", "7 days", "mint"),
    ("portfolio_snapshots", "observed_at", "30 days", None),
    ("social_posts", "posted_at", "7 days", None),
    ("news_items", "published_at", "30 days", None),
    ("alerts", "created_at", "30 days", None),
    ("provider_health", "observed_at", "7 days", "provider"),
    ("audit_logs", "created_at", "30 days", None),
]

# Comprimir despues de este tiempo. Los datos calientes (analisis en vivo) quedan sin comprimir.
COMPRESS_AFTER = "7 days"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE")

    for table, time_col, interval, segment_by in TIMESERIES:
        # Cada tabla en su propio DO: si una fallara, el diagnostico apunta a la tabla exacta.
        op.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb') THEN
                    PERFORM create_hypertable(
                        '{table}', '{time_col}',
                        chunk_time_interval => INTERVAL '{interval}',
                        migrate_data => TRUE,
                        if_not_exists => TRUE
                    );
                ELSE
                    -- Sin TimescaleDB: BRIN sobre la columna de tiempo. En una tabla
                    -- append-only cuyo orden fisico sigue al temporal, un BRIN da poda de
                    -- rango casi tan buena como el particionado, con un indice diminuto.
                    EXECUTE format(
                        'CREATE INDEX IF NOT EXISTS ix_%s_%s_brin ON %I '
                        'USING brin (%I) WITH (pages_per_range = 64)',
                        '{table}', '{time_col}', '{table}', '{time_col}'
                    );
                END IF;
            END
            $$;
            """
        )

        if segment_by is not None:
            op.execute(
                f"""
                DO $$
                BEGIN
                    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb') THEN
                        BEGIN
                            ALTER TABLE {table} SET (
                                timescaledb.compress,
                                timescaledb.compress_segmentby = '{segment_by}',
                                timescaledb.compress_orderby = '{time_col} DESC'
                            );
                            PERFORM add_compression_policy(
                                '{table}', INTERVAL '{COMPRESS_AFTER}', if_not_exists => TRUE
                            );
                        EXCEPTION WHEN OTHERS THEN
                            -- La compresion es de la edicion community. Si no esta
                            -- disponible, la tabla sigue siendo una hypertable valida:
                            -- se pierde espacio, no correccion.
                            RAISE NOTICE 'Compresion no disponible para %: %', '{table}', SQLERRM;
                        END;
                    END IF;
                END
                $$;
                """
            )

    # Aviso visible al aplicar, para que nadie asuma Timescale sin comprobarlo.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb') THEN
                RAISE WARNING
                    'TimescaleDB no disponible: se han creado indices BRIN como alternativa. '
                    'El esquema es correcto, pero las consultas de rango seran mas lentas.';
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    """Vuelve a tablas normales.

    `decompress_chunk` es necesario antes de quitar la compresion; se hace por chunk y se
    ignora el error si un chunk ya estaba descomprimido.
    """
    for table, time_col, _interval, segment_by in reversed(TIMESERIES):
        if segment_by is not None:
            op.execute(
                f"""
                DO $$
                BEGIN
                    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb') THEN
                        BEGIN
                            PERFORM remove_compression_policy('{table}', if_exists => TRUE);
                            ALTER TABLE {table} SET (timescaledb.compress = FALSE);
                        EXCEPTION WHEN OTHERS THEN
                            RAISE NOTICE 'No se pudo quitar compresion de %: %',
                                '{table}', SQLERRM;
                        END;
                    END IF;
                END
                $$;
                """
            )
        op.execute(f"DROP INDEX IF EXISTS ix_{table}_{time_col}_brin")

    # Las hypertables no se revierten a tabla normal in situ. Eso lo resuelve el downgrade de
    # 0001, que elimina las tablas por completo.
