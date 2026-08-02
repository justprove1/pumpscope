"""Esquema inicial completo (SPEC.md 23): 31 tablas.

Decisiones de diseno:

1. NUNCA se usa coma flotante para dinero. SOL en NUMERIC(38, 9) (9 decimales = 1 lamport),
   cantidades de token en NUMERIC(38, 0) (unidades base) y precios en NUMERIC(38, 18).

2. Los valores enumerados se modelan como VARCHAR + CHECK en lugar de ENUM nativo de
   PostgreSQL. Un ENUM nativo obliga a ALTER TYPE para anadir un valor y complica el
   downgrade; un CHECK se modifica con una migracion trivial. El coste es unos bytes.

3. Las tablas de serie temporal llevan la columna de tiempo DENTRO de la clave primaria.
   TimescaleDB exige que toda restriccion unica incluya la columna de particionado, asi que
   esto es un requisito, no una preferencia. Consecuencia: la unicidad de `signature` en
   `transactions` no se puede declarar sola; la garantiza la propia cadena y se refuerza con
   la PK compuesta (block_time, signature).

4. Todo dato que provenga de un proveedor externo lleva el envelope de observacion de
   SPEC.md 5 (provider, timestamps, confidence, latency_ms, raw_reference).

5. Los indices siguen los dos patrones de consulta reales: por rango temporal (los crea
   TimescaleDB en 0002) y por mint + tiempo descendente (declarados aqui).

Revision ID: 0001
Revises:
Create Date: 2026-08-03

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# --- Tipos reutilizados -------------------------------------------------------------------
JSONB = postgresql.JSONB(astext_type=sa.Text())
ADDRESS = sa.String(64)  # direccion base58 de Solana
SIGNATURE = sa.String(128)
SOL = sa.Numeric(38, 9)  # 9 decimales = precision de lamport
TOKEN_AMOUNT = sa.Numeric(38, 0)  # unidades base, sin decimales
PRICE = sa.Numeric(38, 18)
PCT = sa.Numeric(9, 4)
SCORE = sa.Numeric(6, 2)  # 0.00 - 100.00
CONFIDENCE = sa.Numeric(5, 4)  # 0.0000 - 1.0000

NOW = sa.text("now()")
NEW_UUID = sa.text("gen_random_uuid()")


def envelope() -> list[sa.Column]:
    """Envelope de observacion de SPEC.md 5.

    Toda fila que venga de un proveedor externo debe poder responder: quien lo dijo, cuando lo
    dijo, cuando lo recibimos, cuanto tardo y cuanto nos fiamos.
    """
    return [
        sa.Column("provider", sa.String(48), nullable=False),
        sa.Column("provider_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_timestamp", sa.DateTime(timezone=True), nullable=False,
                  server_default=NOW),
        sa.Column("blockchain_slot", sa.BigInteger(), nullable=True),
        sa.Column("confidence", CONFIDENCE, nullable=False, server_default=sa.text("1.0")),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("raw_reference", sa.Text(), nullable=True),
    ]


# Orden inverso de borrado en downgrade (hijas primero).
_TABLES_IN_CREATION_ORDER = [
    "creators", "wallets", "tokens", "token_metadata", "wallet_relationships",
    "transactions", "swaps", "holders_snapshots", "liquidity_snapshots", "price_snapshots",
    "bonding_curve_snapshots", "narratives", "social_posts", "news_items",
    "token_narrative_links", "features", "strategies", "strategy_versions", "model_versions",
    "scores", "signals", "simulated_orders", "live_orders", "fills", "positions",
    "portfolio_snapshots", "backtest_runs", "alerts", "provider_health", "audit_logs",
    "configuration_versions",
]


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # =========================================================================================
    # IDENTIDADES (SPEC.md 7)
    # =========================================================================================

    op.create_table(
        "creators",
        sa.Column("address", ADDRESS, primary_key=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("funded_by", ADDRESS, nullable=True),
        sa.Column("funding_slot", sa.BigInteger(), nullable=True),
        sa.Column("tokens_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_graduated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_rugged", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_dumped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_sold_sol", SOL, nullable=False, server_default="0"),
        # NULL mientras no haya historial suficiente. No se inventa un valor neutro.
        sa.Column("reputation_score", SCORE, nullable=True),
        sa.Column("risk_flags", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
    )
    op.create_index("ix_creators_funded_by", "creators", ["funded_by"])
    op.create_index("ix_creators_reputation", "creators", ["reputation_score"])

    op.create_table(
        "wallets",
        sa.Column("address", ADDRESS, primary_key=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("first_tx_slot", sa.BigInteger(), nullable=True),
        sa.Column("first_tx_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("funded_by", ADDRESS, nullable=True),
        sa.Column("cluster_id", postgresql.UUID(as_uuid=True), nullable=True),
        # Cuentas identificadas: se EXCLUYEN del calculo de concentracion (SPEC.md 7).
        sa.Column("is_program", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_pool", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_exchange", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_known_bot", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("label", sa.String(128), nullable=True),
        sa.Column("risk_flags", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
    )
    op.create_index("ix_wallets_funded_by", "wallets", ["funded_by"])
    op.create_index("ix_wallets_cluster", "wallets", ["cluster_id"])
    op.create_index("ix_wallets_first_seen", "wallets", ["first_seen_at"])

    op.create_table(
        "tokens",
        sa.Column("mint", ADDRESS, primary_key=True),
        sa.Column("creator_address", ADDRESS, sa.ForeignKey("creators.address"), nullable=True),
        sa.Column("symbol", sa.String(32), nullable=True),
        sa.Column("name", sa.String(128), nullable=True),
        sa.Column("uri", sa.Text(), nullable=True),
        sa.Column("decimals", sa.SmallInteger(), nullable=True),
        sa.Column("total_supply", TOKEN_AMOUNT, nullable=True),
        sa.Column("platform", sa.String(24), nullable=False, server_default="pumpfun"),
        sa.Column("status", sa.String(24), nullable=False, server_default="new"),
        # created_at = block time on-chain.  first_seen_at = cuando LO VIMOS nosotros.
        # La diferencia es la latencia de deteccion, objetivo < 1s (SPEC.md 6).
        sa.Column("created_at_slot", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("detection_latency_ms", sa.Integer(), nullable=True),
        sa.Column("mint_authority", ADDRESS, nullable=True),
        sa.Column("freeze_authority", ADDRESS, nullable=True),
        sa.Column("graduated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("migrated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pool_address", ADDRESS, nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint(
            "platform IN ('pumpfun', 'pumpswap', 'raydium', 'other')",
            name="ck_tokens_platform",
        ),
        sa.CheckConstraint(
            "status IN ('new', 'bonding', 'graduated', 'migrated', 'dead', 'rugged', 'unknown')",
            name="ck_tokens_status",
        ),
    )
    op.create_index("ix_tokens_created_at", "tokens", [sa.text("created_at DESC")])
    op.create_index("ix_tokens_first_seen", "tokens", [sa.text("first_seen_at DESC")])
    op.create_index("ix_tokens_status_created", "tokens",
                    ["status", sa.text("created_at DESC")])
    op.create_index("ix_tokens_creator", "tokens", ["creator_address"])
    op.create_index("ix_tokens_symbol", "tokens", ["symbol"])

    op.create_table(
        "token_metadata",
        sa.Column("mint", ADDRESS, sa.ForeignKey("tokens.mint", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("image_uri", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("socials", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("raw_metadata", JSONB, nullable=True),
        # Hash del contenido: detecta metadata mutada despues del lanzamiento.
        sa.Column("metadata_hash", sa.String(64), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        # Suplantacion de marcas o figuras publicas (SPEC.md 8).
        sa.Column("impersonation_flags", JSONB, nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        *envelope(),
    )

    op.create_table(
        "wallet_relationships",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("source_address", ADDRESS, nullable=False),
        sa.Column("target_address", ADDRESS, nullable=False),
        sa.Column("relationship_type", sa.String(32), nullable=False),
        sa.Column("strength", CONFIDENCE, nullable=False, server_default=sa.text("1.0")),
        sa.Column("evidence", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=NOW),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=NOW),
        sa.CheckConstraint(
            "relationship_type IN ('funded', 'cofunded', 'transfer', 'same_bundle', "
            "'same_slot', 'same_cohort', 'identical_amount')",
            name="ck_wallet_rel_type",
        ),
        sa.CheckConstraint("source_address <> target_address", name="ck_wallet_rel_not_self"),
        sa.UniqueConstraint("source_address", "target_address", "relationship_type",
                            name="uq_wallet_rel"),
    )
    op.create_index("ix_wallet_rel_source", "wallet_relationships", ["source_address"])
    op.create_index("ix_wallet_rel_target", "wallet_relationships", ["target_address"])

    # =========================================================================================
    # SERIES ON-CHAIN (SPEC.md 4.A, 7)
    # =========================================================================================

    op.create_table(
        "transactions",
        sa.Column("block_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("signature", SIGNATURE, nullable=False),
        sa.Column("slot", sa.BigInteger(), nullable=False),
        sa.Column("mint", ADDRESS, nullable=True),
        sa.Column("signer", ADDRESS, nullable=True),
        sa.Column("fee_lamports", sa.BigInteger(), nullable=True),
        sa.Column("priority_fee_lamports", sa.BigInteger(), nullable=True),
        sa.Column("succeeded", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("program_ids", postgresql.ARRAY(sa.String(64)), nullable=True),
        sa.Column("instruction_types", postgresql.ARRAY(sa.String(48)), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        *envelope(),
        # La columna de tiempo va en la PK por exigencia de TimescaleDB (ver cabecera).
        sa.PrimaryKeyConstraint("block_time", "signature", name="pk_transactions"),
    )
    op.create_index("ix_tx_mint_time", "transactions", ["mint", sa.text("block_time DESC")])
    op.create_index("ix_tx_signer_time", "transactions", ["signer", sa.text("block_time DESC")])
    op.create_index("ix_tx_slot", "transactions", ["slot"])
    op.create_index("ix_tx_signature", "transactions", ["signature"])

    op.create_table(
        "swaps",
        sa.Column("block_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("signature", SIGNATURE, nullable=False),
        sa.Column("instruction_index", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("slot", sa.BigInteger(), nullable=False),
        sa.Column("mint", ADDRESS, nullable=False),
        sa.Column("wallet", ADDRESS, nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("venue", sa.String(24), nullable=False, server_default="bonding_curve"),
        sa.Column("sol_amount", SOL, nullable=False),
        sa.Column("token_amount", TOKEN_AMOUNT, nullable=False),
        sa.Column("price_sol", PRICE, nullable=True),
        sa.Column("is_creator", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_whale", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_first_cohort", sa.Boolean(), nullable=False, server_default=sa.false()),
        *envelope(),
        sa.CheckConstraint("side IN ('buy', 'sell')", name="ck_swaps_side"),
        sa.CheckConstraint(
            "venue IN ('bonding_curve', 'pumpswap', 'raydium', 'jupiter', 'other')",
            name="ck_swaps_venue",
        ),
        sa.CheckConstraint("sol_amount >= 0", name="ck_swaps_sol_positive"),
        sa.PrimaryKeyConstraint("block_time", "signature", "instruction_index", name="pk_swaps"),
    )
    op.create_index("ix_swaps_mint_time", "swaps", ["mint", sa.text("block_time DESC")])
    op.create_index("ix_swaps_wallet_time", "swaps", ["wallet", sa.text("block_time DESC")])
    op.create_index("ix_swaps_mint_side_time", "swaps",
                    ["mint", "side", sa.text("block_time DESC")])
    op.create_index("ix_swaps_slot", "swaps", ["slot"])

    op.create_table(
        "holders_snapshots",
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("mint", ADDRESS, nullable=False),
        sa.Column("holder_count", sa.Integer(), nullable=False),
        sa.Column("new_holders_1m", sa.Integer(), nullable=True),
        sa.Column("exited_holders_1m", sa.Integer(), nullable=True),
        sa.Column("top1_pct", PCT, nullable=True),
        sa.Column("top5_pct", PCT, nullable=True),
        sa.Column("top10_pct", PCT, nullable=True),
        sa.Column("top20_pct", PCT, nullable=True),
        # Excluye pools y cuentas identificadas: es el numero que importa.
        sa.Column("top10_pct_adjusted", PCT, nullable=True),
        sa.Column("hhi", sa.Numeric(12, 8), nullable=True),
        sa.Column("gini", sa.Numeric(6, 5), nullable=True),
        sa.Column("entropy", sa.Numeric(10, 6), nullable=True),
        sa.Column("clustered_pct", PCT, nullable=True),
        sa.Column("new_wallet_pct", PCT, nullable=True),
        *envelope(),
        sa.PrimaryKeyConstraint("observed_at", "mint", name="pk_holders_snapshots"),
    )
    op.create_index("ix_holders_mint_time", "holders_snapshots",
                    ["mint", sa.text("observed_at DESC")])

    op.create_table(
        "liquidity_snapshots",
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("mint", ADDRESS, nullable=False),
        sa.Column("venue", sa.String(24), nullable=False, server_default="bonding_curve"),
        sa.Column("liquidity_sol", SOL, nullable=True),
        sa.Column("liquidity_usd", sa.Numeric(24, 4), nullable=True),
        sa.Column("effective_depth_sol", SOL, nullable=True),
        # {"0.01": bps, "0.05": bps, ...} para los seis tamanos de SPEC.md 7.
        sa.Column("price_impact_bps", JSONB, nullable=True),
        sa.Column("liquidity_change_pct", PCT, nullable=True),
        sa.Column("exit_risk_score", SCORE, nullable=True),
        *envelope(),
        sa.PrimaryKeyConstraint("observed_at", "mint", "venue", name="pk_liquidity_snapshots"),
    )
    op.create_index("ix_liquidity_mint_time", "liquidity_snapshots",
                    ["mint", sa.text("observed_at DESC")])

    op.create_table(
        "price_snapshots",
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("mint", ADDRESS, nullable=False),
        sa.Column("source", sa.String(32), nullable=False, server_default="onchain"),
        sa.Column("price_sol", PRICE, nullable=False),
        sa.Column("price_usd", sa.Numeric(30, 12), nullable=True),
        sa.Column("market_cap_sol", SOL, nullable=True),
        sa.Column("market_cap_usd", sa.Numeric(24, 4), nullable=True),
        sa.Column("volume_sol_1m", SOL, nullable=True),
        sa.Column("volume_sol_5m", SOL, nullable=True),
        sa.Column("trades_1m", sa.Integer(), nullable=True),
        sa.Column("unique_buyers_1m", sa.Integer(), nullable=True),
        sa.Column("unique_sellers_1m", sa.Integer(), nullable=True),
        *envelope(),
        # `source` en la PK: dos fuentes pueden reportar el mismo instante. La divergencia
        # entre ellas es un dato, no un conflicto (SPEC.md 5).
        sa.PrimaryKeyConstraint("observed_at", "mint", "source", name="pk_price_snapshots"),
    )
    op.create_index("ix_price_mint_time", "price_snapshots",
                    ["mint", sa.text("observed_at DESC")])

    op.create_table(
        "bonding_curve_snapshots",
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("mint", ADDRESS, nullable=False),
        sa.Column("virtual_sol_reserves", sa.BigInteger(), nullable=True),
        sa.Column("virtual_token_reserves", TOKEN_AMOUNT, nullable=True),
        sa.Column("real_sol_reserves", sa.BigInteger(), nullable=True),
        sa.Column("real_token_reserves", TOKEN_AMOUNT, nullable=True),
        # Umbral DERIVADO de las reservas de este token, no una constante en dolares.
        sa.Column("graduation_threshold_sol", SOL, nullable=True),
        sa.Column("progress_pct", PCT, nullable=True),
        sa.Column("is_complete", sa.Boolean(), nullable=False, server_default=sa.false()),
        *envelope(),
        sa.PrimaryKeyConstraint("observed_at", "mint", name="pk_bonding_curve_snapshots"),
    )
    op.create_index("ix_curve_mint_time", "bonding_curve_snapshots",
                    ["mint", sa.text("observed_at DESC")])

    # =========================================================================================
    # NARRATIVAS Y SOCIAL (SPEC.md 4.F, 9)
    # =========================================================================================

    op.create_table(
        "narratives",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=NEW_UUID),
        sa.Column("slug", sa.String(160), nullable=False, unique=True),
        sa.Column("label", sa.String(256), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="NASCENT"),
        sa.Column("score", SCORE, nullable=True),
        sa.Column("confidence", CONFIDENCE, nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("mention_velocity", sa.Numeric(18, 6), nullable=True),
        sa.Column("mention_acceleration", sa.Numeric(18, 6), nullable=True),
        sa.Column("unique_author_growth", sa.Numeric(18, 6), nullable=True),
        sa.Column("influencer_score", SCORE, nullable=True),
        sa.Column("news_quality_score", SCORE, nullable=True),
        sa.Column("cross_platform_spread", SCORE, nullable=True),
        sa.Column("spam_probability", CONFIDENCE, nullable=True),
        sa.Column("half_life_minutes", sa.Integer(), nullable=True),
        sa.Column("entities", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        # Salida del LLM: SIEMPRE JSON validado contra esquema, nunca texto libre (SPEC.md 9).
        # Se guarda el modelo que la produjo para poder auditar y reproducir.
        sa.Column("llm_model", sa.String(64), nullable=True),
        sa.Column("llm_reasons", JSONB, nullable=True),
        sa.CheckConstraint(
            "state IN ('NASCENT', 'EMERGING', 'ACCELERATING', 'VIRAL', 'SATURATED', "
            "'DECELERATING', 'EXHAUSTED', 'REVIVING')",
            name="ck_narratives_state",
        ),
    )
    op.create_index("ix_narratives_state_score", "narratives",
                    ["state", sa.text("score DESC")])
    op.create_index("ix_narratives_updated", "narratives", [sa.text("updated_at DESC")])

    op.create_table(
        "social_posts",
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=NEW_UUID),
        sa.Column("platform", sa.String(24), nullable=False),
        sa.Column("external_id", sa.String(128), nullable=False),
        sa.Column("author_id", sa.String(128), nullable=True),
        sa.Column("author_followers", sa.BigInteger(), nullable=True),
        sa.Column("author_account_age_days", sa.Integer(), nullable=True),
        sa.Column("author_is_new", sa.Boolean(), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("lang", sa.String(8), nullable=True),
        sa.Column("country", sa.String(8), nullable=True),
        sa.Column("engagement", JSONB, nullable=True),
        sa.Column("sentiment", sa.Numeric(5, 4), nullable=True),
        sa.Column("bot_probability", CONFIDENCE, nullable=True),
        sa.Column("entities", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("mints", postgresql.ARRAY(ADDRESS), nullable=True),
        sa.Column("narrative_id", postgresql.UUID(as_uuid=True), nullable=True),
        *envelope(),
        sa.CheckConstraint(
            "platform IN ('x', 'reddit', 'telegram', 'discord', 'youtube', 'other')",
            name="ck_social_platform",
        ),
        sa.PrimaryKeyConstraint("posted_at", "id", name="pk_social_posts"),
        sa.UniqueConstraint("posted_at", "platform", "external_id", name="uq_social_external"),
    )
    op.create_index("ix_social_narrative_time", "social_posts",
                    ["narrative_id", sa.text("posted_at DESC")])
    op.create_index("ix_social_mints", "social_posts", ["mints"], postgresql_using="gin")

    op.create_table(
        "news_items",
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=NEW_UUID),
        sa.Column("source", sa.String(96), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("url_hash", sa.String(64), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("lang", sa.String(8), nullable=True),
        sa.Column("country", sa.String(8), nullable=True),
        sa.Column("entities", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("topics", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("quality_score", SCORE, nullable=True),
        sa.Column("narrative_id", postgresql.UUID(as_uuid=True), nullable=True),
        *envelope(),
        sa.PrimaryKeyConstraint("published_at", "id", name="pk_news_items"),
        sa.UniqueConstraint("published_at", "url_hash", name="uq_news_url"),
    )
    op.create_index("ix_news_narrative_time", "news_items",
                    ["narrative_id", sa.text("published_at DESC")])

    op.create_table(
        "token_narrative_links",
        sa.Column("mint", ADDRESS, sa.ForeignKey("tokens.mint", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("narrative_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("narratives.id", ondelete="CASCADE"), nullable=False),
        sa.Column("fit_score", SCORE, nullable=True),
        sa.Column("dominance", PCT, nullable=True),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.PrimaryKeyConstraint("mint", "narrative_id", name="pk_token_narrative_links"),
    )
    op.create_index("ix_tnl_narrative_dominance", "token_narrative_links",
                    ["narrative_id", sa.text("dominance DESC")])

    # =========================================================================================
    # FEATURES Y SCORES (SPEC.md 10, 11)
    # =========================================================================================

    op.create_table(
        "features",
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("mint", ADDRESS, nullable=False),
        sa.Column("time_window", sa.String(8), nullable=False),
        sa.Column("feature_set_version", sa.String(32), nullable=False),
        sa.Column("feature_values", JSONB, nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        # Frontera explicita de no-leakage: ningun dato posterior a `observed_at` entra en
        # `feature_values`. Se persiste para AUDITARLO, no solo confiar en el codigo.
        sa.Column("lookback_start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data_confidence", CONFIDENCE, nullable=True),
        sa.CheckConstraint(
            "time_window IN ('5s', '15s', '30s', '1m', '3m', '5m', '15m', '1h')",
            name="ck_features_window",
        ),
        sa.CheckConstraint("lookback_start_at <= observed_at", name="ck_features_no_leakage"),
        sa.PrimaryKeyConstraint("observed_at", "mint", "time_window", "feature_set_version",
                                name="pk_features"),
    )
    op.create_index("ix_features_mint_window_time", "features",
                    ["mint", "time_window", sa.text("observed_at DESC")])

    op.create_table(
        "scores",
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("mint", ADDRESS, nullable=False),
        sa.Column("mode", sa.String(16), nullable=False, server_default="heuristic"),
        sa.Column("opportunity_score", SCORE, nullable=True),
        sa.Column("narrative_score", SCORE, nullable=True),
        sa.Column("momentum_score", SCORE, nullable=True),
        sa.Column("liquidity_score", SCORE, nullable=True),
        sa.Column("holder_quality_score", SCORE, nullable=True),
        sa.Column("distribution_score", SCORE, nullable=True),
        sa.Column("creator_score", SCORE, nullable=True),
        sa.Column("whale_score", SCORE, nullable=True),
        sa.Column("social_authenticity_score", SCORE, nullable=True),
        sa.Column("exit_liquidity_score", SCORE, nullable=True),
        sa.Column("manipulation_risk_score", SCORE, nullable=True),
        sa.Column("rug_risk_score", SCORE, nullable=True),
        sa.Column("execution_quality_score", SCORE, nullable=True),
        sa.Column("data_confidence_score", SCORE, nullable=True),
        sa.Column("weights", JSONB, nullable=True),
        # Razones concretas con cifras, no etiquetas (SPEC.md 8).
        sa.Column("reasons", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("model_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("feature_set_version", sa.String(32), nullable=True),
        sa.CheckConstraint("mode IN ('heuristic', 'model')", name="ck_scores_mode"),
        sa.PrimaryKeyConstraint("observed_at", "mint", "mode", name="pk_scores"),
    )
    op.create_index("ix_scores_mint_time", "scores", ["mint", sa.text("observed_at DESC")])
    op.create_index("ix_scores_opportunity", "scores",
                    [sa.text("observed_at DESC"), sa.text("opportunity_score DESC")])

    # =========================================================================================
    # ESTRATEGIAS Y MODELOS (SPEC.md 19, 20)
    # =========================================================================================

    op.create_table(
        "strategies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=NEW_UUID),
        sa.Column("name", sa.String(96), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    op.create_table(
        "strategy_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=NEW_UUID),
        sa.Column("strategy_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("params", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("commit_sha", sa.String(40), nullable=True),
        # Aprobacion SIEMPRE manual (SPEC.md 20): el sistema no se auto-promociona.
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("approved_by", sa.String(96), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('draft', 'backtested', 'paper', 'approved', 'retired')",
            name="ck_strategy_version_status",
        ),
        sa.CheckConstraint(
            "(status <> 'approved') OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)",
            name="ck_strategy_version_approval_required",
        ),
        sa.UniqueConstraint("strategy_id", "version", name="uq_strategy_version"),
    )

    op.create_table(
        "model_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=NEW_UUID),
        sa.Column("name", sa.String(96), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("algorithm", sa.String(48), nullable=False),
        sa.Column("target", sa.String(96), nullable=False),
        sa.Column("feature_set_version", sa.String(32), nullable=False),
        sa.Column("trained_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("train_window_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("train_window_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metrics", JSONB, nullable=True),
        sa.Column("calibration", JSONB, nullable=True),
        sa.Column("artifact_uri", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="training"),
        sa.Column("drift_metrics", JSONB, nullable=True),
        # Un modelo degradado se desactiva AUTOMATICAMENTE (SPEC.md 19); queda el motivo.
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disabled_reason", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('training', 'validated', 'active', 'degraded', 'retired')",
            name="ck_model_status",
        ),
        sa.UniqueConstraint("name", "version", name="uq_model_version"),
    )
    op.create_index("ix_model_status", "model_versions", ["status", sa.text("trained_at DESC")])

    # =========================================================================================
    # SENALES, ORDENES Y CARTERA (SPEC.md 13, 15)
    # =========================================================================================

    op.create_table(
        "signals",
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=NEW_UUID),
        sa.Column("mint", ADDRESS, nullable=False),
        sa.Column("signal_type", sa.String(24), nullable=False),
        sa.Column("score", SCORE, nullable=True),
        sa.Column("confidence", CONFIDENCE, nullable=True),
        sa.Column("strategy_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("feature_set_version", sa.String(32), nullable=True),
        sa.Column("top_features", JSONB, nullable=True),
        sa.Column("risks", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        # Cantidad recomendada por el RiskEngine determinista, no por el modelo ni por el LLM.
        sa.Column("recommended_size_sol", SOL, nullable=True),
        sa.Column("expected_price_sol", PRICE, nullable=True),
        sa.Column("expected_slippage_bps", sa.Integer(), nullable=True),
        sa.Column("invalidation", JSONB, nullable=True),
        sa.Column("max_duration_seconds", sa.Integer(), nullable=True),
        sa.Column("planned_exit", JSONB, nullable=True),
        # Cual de los 17 vetos de elegibilidad se activo, con su valor y su umbral.
        sa.Column("eligibility_vetoes", JSONB, nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.CheckConstraint(
            "signal_type IN ('WATCH', 'PREPARE', 'ENTER_SMALL', 'ENTER', 'ADD_FORBIDDEN', "
            "'REDUCE', 'TAKE_PROFIT', 'EXIT', 'EMERGENCY_EXIT', 'IGNORE')",
            name="ck_signals_type",
        ),
        sa.PrimaryKeyConstraint("created_at", "id", name="pk_signals"),
    )
    op.create_index("ix_signals_mint_time", "signals", ["mint", sa.text("created_at DESC")])
    op.create_index("ix_signals_type_time", "signals",
                    ["signal_type", sa.text("created_at DESC")])

    def _order_columns() -> list[sa.Column]:
        """Columnas comunes a ordenes simuladas y reales.

        Se mantienen en tablas separadas (SPEC.md 23) para que sea IMPOSIBLE confundir una
        orden de papel con una real en una consulta.
        """
        return [
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                      server_default=NOW),
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False,
                      server_default=NEW_UUID),
            sa.Column("mint", ADDRESS, nullable=False),
            sa.Column("signal_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("position_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("side", sa.String(8), nullable=False),
            sa.Column("mode", sa.String(12), nullable=False),
            sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
            sa.Column("requested_sol", SOL, nullable=False),
            sa.Column("quote", JSONB, nullable=True),
            sa.Column("quote_age_ms", sa.Integer(), nullable=True),
            sa.Column("expected_output", TOKEN_AMOUNT, nullable=True),
            sa.Column("min_expected_output", TOKEN_AMOUNT, nullable=True),
            sa.Column("price_impact_bps", sa.Integer(), nullable=True),
            sa.Column("slippage_bps", sa.Integer(), nullable=True),
            sa.Column("priority_fee_lamports", sa.BigInteger(), nullable=True),
            # Clave de idempotencia: impide duplicar una orden ante un timeout (SPEC.md 15).
            sa.Column("idempotency_key", sa.String(96), nullable=False),
            sa.Column("latency_breakdown", JSONB, nullable=True),
            sa.Column("retries", sa.SmallInteger(), nullable=False, server_default="0"),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        ]

    def _order_constraints(prefix: str) -> list[sa.SchemaItem]:
        return [
            sa.CheckConstraint("side IN ('buy', 'sell')", name=f"ck_{prefix}_side"),
            sa.CheckConstraint(
                "status IN ('pending', 'quoted', 'simulated', 'submitted', 'confirmed', "
                "'filled', 'partially_filled', 'failed', 'cancelled', 'expired')",
                name=f"ck_{prefix}_status",
            ),
            sa.CheckConstraint("requested_sol > 0", name=f"ck_{prefix}_size_positive"),
            sa.PrimaryKeyConstraint("created_at", "id", name=f"pk_{prefix}"),
            sa.UniqueConstraint("created_at", "idempotency_key", name=f"uq_{prefix}_idempotency"),
        ]

    op.create_table(
        "simulated_orders",
        *_order_columns(),
        sa.Column("backtest_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("simulated_latency_ms", sa.Integer(), nullable=True),
        sa.Column("fill_probability", CONFIDENCE, nullable=True),
        sa.CheckConstraint("mode IN ('DRY_RUN', 'PAPER', 'BACKTEST')",
                           name="ck_simulated_orders_mode"),
        *_order_constraints("simulated_orders"),
    )
    op.create_index("ix_sim_orders_mint_time", "simulated_orders",
                    ["mint", sa.text("created_at DESC")])
    op.create_index("ix_sim_orders_backtest", "simulated_orders", ["backtest_run_id"])

    op.create_table(
        "live_orders",
        *_order_columns(),
        sa.Column("signature", SIGNATURE, nullable=True),
        sa.Column("signer_request_id", sa.String(64), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_slot", sa.BigInteger(), nullable=True),
        sa.Column("network_fee_lamports", sa.BigInteger(), nullable=True),
        # Reconciliacion contra el estado on-chain: obligatoria tras cada orden (SPEC.md 15).
        sa.Column("reconciled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reconciliation_diff", JSONB, nullable=True),
        # Una orden LIVE solo puede existir en modo LIVE. El CHECK lo hace estructural.
        sa.CheckConstraint("mode = 'LIVE'", name="ck_live_orders_mode"),
        *_order_constraints("live_orders"),
    )
    op.create_index("ix_live_orders_mint_time", "live_orders",
                    ["mint", sa.text("created_at DESC")])
    op.create_index("ix_live_orders_signature", "live_orders", ["signature"])
    op.create_index("ix_live_orders_unreconciled", "live_orders",
                    [sa.text("created_at DESC")],
                    postgresql_where=sa.text("reconciled = false"))

    op.create_table(
        "fills",
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=NEW_UUID),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_kind", sa.String(12), nullable=False),
        sa.Column("mint", ADDRESS, nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("sol_amount", SOL, nullable=False),
        sa.Column("token_amount", TOKEN_AMOUNT, nullable=False),
        sa.Column("price_sol", PRICE, nullable=False),
        sa.Column("network_fee_lamports", sa.BigInteger(), nullable=True),
        sa.Column("priority_fee_lamports", sa.BigInteger(), nullable=True),
        sa.Column("realized_slippage_bps", sa.Integer(), nullable=True),
        sa.Column("is_partial", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("signature", SIGNATURE, nullable=True),
        sa.CheckConstraint("order_kind IN ('simulated', 'live')", name="ck_fills_order_kind"),
        sa.CheckConstraint("side IN ('buy', 'sell')", name="ck_fills_side"),
        sa.PrimaryKeyConstraint("filled_at", "id", name="pk_fills"),
    )
    op.create_index("ix_fills_order", "fills", ["order_id"])
    op.create_index("ix_fills_mint_time", "fills", ["mint", sa.text("filled_at DESC")])

    op.create_table(
        "positions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=NEW_UUID),
        sa.Column("mint", ADDRESS, nullable=False),
        sa.Column("mode", sa.String(12), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="open"),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("strategy_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("entry_price_sol", PRICE, nullable=True),
        sa.Column("avg_price_sol", PRICE, nullable=True),
        sa.Column("token_amount", TOKEN_AMOUNT, nullable=False, server_default="0"),
        sa.Column("cost_sol", SOL, nullable=False, server_default="0"),
        sa.Column("realized_pnl_sol", SOL, nullable=False, server_default="0"),
        sa.Column("unrealized_pnl_sol", SOL, nullable=True),
        sa.Column("fees_sol", SOL, nullable=False, server_default="0"),
        # MFE / MAE: necesarias para aprender que habria pasado con otras salidas (SPEC.md 20).
        sa.Column("max_favorable_excursion_pct", PCT, nullable=True),
        sa.Column("max_adverse_excursion_pct", PCT, nullable=True),
        sa.Column("stop_state", JSONB, nullable=True),
        sa.Column("exit_reason", sa.String(48), nullable=True),
        # Averaging down automatico PROHIBIDO en esta version (SPEC.md 13). Default true.
        sa.Column("add_forbidden", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.CheckConstraint("mode IN ('DRY_RUN', 'PAPER', 'BACKTEST', 'LIVE')",
                           name="ck_positions_mode"),
        sa.CheckConstraint("status IN ('open', 'closed', 'stuck', 'liquidating')",
                           name="ck_positions_status"),
    )
    op.create_index("ix_positions_mint", "positions", ["mint"])
    op.create_index("ix_positions_open", "positions", ["mode", sa.text("opened_at DESC")],
                    postgresql_where=sa.text("status = 'open'"))

    op.create_table(
        "portfolio_snapshots",
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("mode", sa.String(12), nullable=False),
        sa.Column("balance_sol", SOL, nullable=False),
        sa.Column("equity_sol", SOL, nullable=False),
        sa.Column("open_positions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("exposure_sol", SOL, nullable=False, server_default="0"),
        sa.Column("realized_pnl_day_sol", SOL, nullable=False, server_default="0"),
        sa.Column("unrealized_pnl_sol", SOL, nullable=False, server_default="0"),
        sa.Column("drawdown_pct", PCT, nullable=True),
        sa.Column("risk_used_pct", PCT, nullable=True),
        sa.Column("consecutive_losses", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("kill_switch_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("kill_switch_reasons", JSONB, nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.PrimaryKeyConstraint("observed_at", "mode", name="pk_portfolio_snapshots"),
    )

    op.create_table(
        "backtest_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=NEW_UUID),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("strategy_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("model_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        # Reproducibilidad bit a bit (BACKTESTING.md 8): sin esto no es evidencia de nada.
        sa.Column("commit_sha", sa.String(40), nullable=True),
        sa.Column("seed", sa.BigInteger(), nullable=True),
        sa.Column("dataset_hash", sa.String(64), nullable=True),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("universe_filter", JSONB, nullable=True),
        sa.Column("cost_config", JSONB, nullable=False),
        sa.Column("simulation_mode", sa.String(24), nullable=False),
        sa.Column("metrics", JSONB, nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "simulation_mode IN ('HISTORICAL_REPLAY', 'PAPER_LIVE', 'MONTE_CARLO', "
            "'STRESS_TEST')",
            name="ck_backtest_mode",
        ),
        sa.CheckConstraint("status IN ('running', 'completed', 'failed', 'cancelled')",
                           name="ck_backtest_status"),
        sa.CheckConstraint("window_end > window_start", name="ck_backtest_window"),
    )
    op.create_index("ix_backtest_created", "backtest_runs", [sa.text("created_at DESC")])

    # =========================================================================================
    # OPERACION: ALERTAS, SALUD, AUDITORIA, CONFIGURACION (SPEC.md 22, 24)
    # =========================================================================================

    op.create_table(
        "alerts",
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=NEW_UUID),
        sa.Column("alert_type", sa.String(48), nullable=False),
        sa.Column("severity", sa.String(12), nullable=False, server_default="info"),
        sa.Column("channel", sa.String(24), nullable=False),
        sa.Column("mint", ADDRESS, nullable=True),
        # Datos verificables, nunca mensajes vagos (SPEC.md 22).
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("dedup_key", sa.String(128), nullable=True),
        sa.Column("delivered", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivery_error", sa.Text(), nullable=True),
        sa.CheckConstraint("severity IN ('info', 'warning', 'critical')",
                           name="ck_alerts_severity"),
        sa.CheckConstraint("channel IN ('telegram', 'discord', 'email', 'webpush', 'internal')",
                           name="ck_alerts_channel"),
        sa.PrimaryKeyConstraint("created_at", "id", name="pk_alerts"),
    )
    op.create_index("ix_alerts_type_time", "alerts", ["alert_type", sa.text("created_at DESC")])
    op.create_index("ix_alerts_dedup", "alerts", ["dedup_key", sa.text("created_at DESC")])
    op.create_index("ix_alerts_undelivered", "alerts", [sa.text("created_at DESC")],
                    postgresql_where=sa.text("delivered = false"))

    op.create_table(
        "provider_health",
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider", sa.String(48), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("latency_p50_ms", sa.Integer(), nullable=True),
        sa.Column("latency_p95_ms", sa.Integer(), nullable=True),
        sa.Column("latency_p99_ms", sa.Integer(), nullable=True),
        sa.Column("requests", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("errors", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_rate", CONFIDENCE, nullable=True),
        sa.Column("missing_data_pct", PCT, nullable=True),
        # Divergencia frente a otras fuentes: baja la confianza (SPEC.md 5).
        sa.Column("divergence_pct", PCT, nullable=True),
        sa.Column("circuit_open", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.CheckConstraint("status IN ('healthy', 'degraded', 'down', 'disabled')",
                           name="ck_provider_health_status"),
        sa.PrimaryKeyConstraint("observed_at", "provider", name="pk_provider_health"),
    )
    op.create_index("ix_provider_health_time", "provider_health",
                    ["provider", sa.text("observed_at DESC")])

    op.create_table(
        "audit_logs",
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=NEW_UUID),
        # `llm` es un actor de primera clase a proposito: si algo lo origino un modelo,
        # queda escrito quien fue y con que prompt (SPEC.md 2).
        sa.Column("actor", sa.String(16), nullable=False),
        sa.Column("actor_id", sa.String(96), nullable=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("subject_type", sa.String(48), nullable=True),
        sa.Column("subject_id", sa.String(96), nullable=True),
        sa.Column("before", JSONB, nullable=True),
        sa.Column("after", JSONB, nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("severity", sa.String(12), nullable=False, server_default="info"),
        sa.CheckConstraint("actor IN ('system', 'user', 'signer', 'llm', 'worker', 'api')",
                           name="ck_audit_actor"),
        sa.PrimaryKeyConstraint("created_at", "id", name="pk_audit_logs"),
    )
    op.create_index("ix_audit_action_time", "audit_logs",
                    ["action", sa.text("created_at DESC")])
    op.create_index("ix_audit_subject", "audit_logs", ["subject_type", "subject_id"])
    op.create_index("ix_audit_actor_time", "audit_logs", ["actor", sa.text("created_at DESC")])

    # audit_logs es append-only: sin UPDATE ni DELETE, ni siquiera para el owner de la app.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mit_audit_logs_immutable() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_logs es append-only: % no permitido', TG_OP;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_audit_logs_immutable
        BEFORE UPDATE OR DELETE ON audit_logs
        FOR EACH ROW EXECUTE FUNCTION mit_audit_logs_immutable();
        """
    )

    op.create_table(
        "configuration_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=NEW_UUID),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("created_by", sa.String(96), nullable=False),
        sa.Column("config", JSONB, nullable=False),
        sa.Column("diff", JSONB, nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        # Un cambio de limites de riesgo no puede activarse sin aprobacion (RISK_POLICY.md 7).
        sa.Column("approved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("approved_by", sa.String(96), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint("(NOT is_active) OR approved", name="ck_config_active_requires_approval"),
    )
    # Solo puede haber UNA configuracion activa a la vez.
    op.create_index("uq_config_single_active", "configuration_versions", ["is_active"],
                    unique=True, postgresql_where=sa.text("is_active = true"))


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_audit_logs_immutable ON audit_logs")
    op.execute("DROP FUNCTION IF EXISTS mit_audit_logs_immutable()")
    for table in reversed(_TABLES_IN_CREATION_ORDER):
        op.drop_table(table)
