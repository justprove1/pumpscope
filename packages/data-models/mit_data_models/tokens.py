"""Contratos de token, curva y distribucion de holders."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from mit_data_models.enums import TokenStatus, Venue


class TokenRef(BaseModel):
    """Referencia minima a un token. Lo que basta para identificarlo sin cargar su ficha."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mint: str = Field(min_length=32, max_length=64)
    symbol: str | None = None
    name: str | None = None


class TokenIdentity(BaseModel):
    """Identidad completa (SPEC.md 7)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mint: str
    creator: str | None = None
    symbol: str | None = None
    name: str | None = None
    uri: str | None = None
    decimals: int | None = Field(default=None, ge=0, le=18)
    total_supply: int | None = Field(default=None, ge=0)
    platform: str = "pumpfun"
    status: TokenStatus = TokenStatus.UNKNOWN
    mint_authority: str | None = None
    freeze_authority: str | None = None
    created_at_slot: int | None = None
    created_at: datetime | None = None
    first_seen_at: datetime
    detection_latency_ms: int | None = None


class BondingCurveState(BaseModel):
    """Estado de la bonding curve.

    `graduation_threshold_sol` se DERIVA de las reservas de este token concreto, no de una
    cifra fija en dolares. El umbral esta fijado en SOL por el propio programa, asi que una
    constante en USD queda obsoleta en cuanto se mueve el precio de SOL.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    mint: str
    observed_at: datetime
    virtual_sol_reserves: int | None = Field(default=None, ge=0)
    virtual_token_reserves: int | None = Field(default=None, ge=0)
    real_sol_reserves: int | None = Field(default=None, ge=0)
    real_token_reserves: int | None = Field(default=None, ge=0)
    graduation_threshold_sol: Decimal | None = None
    progress_pct: Decimal | None = Field(default=None, ge=0, le=100)
    is_complete: bool = False


class HolderDistribution(BaseModel):
    """Distribucion de holders (SPEC.md 7).

    Los porcentajes `*_adjusted` EXCLUYEN pools y cuentas identificadas. Son los unicos que
    deben usarse para decidir: incluir el pool en la concentracion la subestima siempre.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    mint: str
    observed_at: datetime
    holder_count: int = Field(ge=0)
    new_holders_1m: int | None = None
    exited_holders_1m: int | None = None
    top1_pct: Decimal | None = None
    top5_pct: Decimal | None = None
    top10_pct: Decimal | None = None
    top20_pct: Decimal | None = None
    top10_pct_adjusted: Decimal | None = None
    hhi: Decimal | None = None
    gini: Decimal | None = Field(default=None, ge=0, le=1)
    entropy: Decimal | None = None
    clustered_pct: Decimal | None = None
    new_wallet_pct: Decimal | None = None


class CreatorProfile(BaseModel):
    """Historial del creador. Alimenta `CreatorScore` y el veto `creator_history_critical`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    address: str
    first_seen_at: datetime | None = None
    funded_by: str | None = None
    tokens_created: int = 0
    tokens_graduated: int = 0
    tokens_rugged: int = 0
    tokens_dumped: int = 0
    total_sold_sol: Decimal = Decimal(0)
    # None mientras no haya historial suficiente. No se inventa un valor neutro:
    # "sin datos" y "riesgo medio" son cosas distintas.
    reputation_score: Decimal | None = None


class LiquidityState(BaseModel):
    """Liquidez y profundidad efectiva.

    `price_impact_bps` mapea tamano de orden en SOL -> impacto en puntos basicos, para los
    seis tamanos de SPEC.md 7 (0.01, 0.05, 0.1, 0.25, 0.5, 1 SOL).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    mint: str
    observed_at: datetime
    venue: Venue = Venue.BONDING_CURVE
    liquidity_sol: Decimal | None = None
    effective_depth_sol: Decimal | None = None
    price_impact_bps: dict[str, int] = Field(default_factory=dict)
    liquidity_change_pct: Decimal | None = None
