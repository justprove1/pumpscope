"""Entorno de Alembic.

La URL de conexion se lee SIEMPRE de `DATABASE_URL`. Nunca se escribe en `alembic.ini`
(CLAUDE.md 1: ningun secreto versionado).

`target_metadata` es None a proposito: en Fase 0 no hay modelos ORM todavia, asi que las
migraciones se escriben a mano y `--autogenerate` no esta disponible. Pasara a apuntar al
`MetaData` de `mit_data_models` cuando existan las tablas declarativas (Fase 1).
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None


def _database_url() -> str:
    """URL sincrona para Alembic.

    La aplicacion usa asyncpg; Alembic corre en sincrono, asi que se normaliza el driver.
    """
    url = os.environ.get("DATABASE_URL")
    if not url:
        msg = "DATABASE_URL no esta definida. Copia .env.example a .env y rellenala."
        raise RuntimeError(msg)
    return url.replace("+asyncpg", "").replace("postgresql://", "postgresql+psycopg://", 1)


def run_migrations_offline() -> None:
    """Genera el SQL sin conectarse a la base de datos."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Aplica las migraciones contra la base de datos."""
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _database_url()

    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            transaction_per_migration=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
