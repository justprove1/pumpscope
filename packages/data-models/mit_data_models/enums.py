"""Valores enumerados del dominio.

Deben coincidir exactamente con los CHECK de las migraciones
(`infrastructure/migrations/versions/0001_initial_schema.py`). Un test de la Fase 1 verificara
esa correspondencia contra la base de datos real, para que no puedan divergir en silencio.
"""

from __future__ import annotations

from enum import StrEnum


class TradingMode(StrEnum):
    """Modo de operacion (SPEC.md 15).

    El arranque es SIEMPRE `DRY_RUN`. `LIVE` exige el checklist completo.
    """

    DRY_RUN = "DRY_RUN"
    PAPER = "PAPER"
    BACKTEST = "BACKTEST"
    LIVE = "LIVE"


class TokenStatus(StrEnum):
    NEW = "new"
    BONDING = "bonding"
    GRADUATED = "graduated"
    MIGRATED = "migrated"
    DEAD = "dead"
    RUGGED = "rugged"
    UNKNOWN = "unknown"


class Venue(StrEnum):
    BONDING_CURVE = "bonding_curve"
    PUMPSWAP = "pumpswap"
    RAYDIUM = "raydium"
    JUPITER = "jupiter"
    OTHER = "other"


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class SignalType(StrEnum):
    """SPEC.md 13. `ADD_FORBIDDEN` es el estado por defecto de toda posicion abierta:
    el averaging down automatico esta prohibido en esta version.
    """

    WATCH = "WATCH"
    PREPARE = "PREPARE"
    ENTER_SMALL = "ENTER_SMALL"
    ENTER = "ENTER"
    ADD_FORBIDDEN = "ADD_FORBIDDEN"
    REDUCE = "REDUCE"
    TAKE_PROFIT = "TAKE_PROFIT"
    EXIT = "EXIT"
    EMERGENCY_EXIT = "EMERGENCY_EXIT"
    IGNORE = "IGNORE"


class OrderStatus(StrEnum):
    PENDING = "pending"
    QUOTED = "quoted"
    SIMULATED = "simulated"
    SUBMITTED = "submitted"
    CONFIRMED = "confirmed"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class NarrativeState(StrEnum):
    """SPEC.md 9."""

    NASCENT = "NASCENT"
    EMERGING = "EMERGING"
    ACCELERATING = "ACCELERATING"
    VIRAL = "VIRAL"
    SATURATED = "SATURATED"
    DECELERATING = "DECELERATING"
    EXHAUSTED = "EXHAUSTED"
    REVIVING = "REVIVING"


class FeatureWindow(StrEnum):
    """Ventanas de SPEC.md 10."""

    S5 = "5s"
    S15 = "15s"
    S30 = "30s"
    M1 = "1m"
    M3 = "3m"
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"


class SocialPlatform(StrEnum):
    X = "x"
    REDDIT = "reddit"
    TELEGRAM = "telegram"
    DISCORD = "discord"
    YOUTUBE = "youtube"
    OTHER = "other"


class ProviderStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"
    DISABLED = "disabled"


class EligibilityVeto(StrEnum):
    """Los 17 vetos duros de SPEC.md 12.

    Cualquiera de ellos produce `IGNORE`. Ningun score los compensa.
    """

    LOW_DATA_CONFIDENCE = "low_data_confidence"
    HIGH_RUG_RISK = "high_rug_risk"
    HIGH_MANIPULATION_RISK = "high_manipulation_risk"
    INSUFFICIENT_LIQUIDITY = "insufficient_liquidity"
    NO_EXIT_ROUTE = "no_exit_route"
    SELL_SIMULATION_FAILED = "sell_simulation_failed"
    PRICE_IMPACT_TOO_HIGH = "price_impact_too_high"
    CREATOR_HISTORY_CRITICAL = "creator_history_critical"
    HOLDER_CONCENTRATION = "holder_concentration"
    DANGEROUS_CLUSTER = "dangerous_cluster"
    ALREADY_PUMPED = "already_pumped"
    NARRATIVE_EXHAUSTED = "narrative_exhausted"
    EXCESSIVE_SPREAD = "excessive_spread"
    STALE_DATA = "stale_data"
    SOURCE_DIVERGENCE = "source_divergence"
    INSUFFICIENT_SOL = "insufficient_sol"
    DAILY_RISK_LIMIT_REACHED = "daily_risk_limit_reached"
