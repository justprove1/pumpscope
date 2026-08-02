"""Listas planas de los valores enumerados.

Existen para que un test pueda comparar los enums de Python contra los CHECK de las
migraciones sin importar `enum` en el test ni depender del intérprete. Si alguien anade un
estado en un sitio y se olvida del otro, el test lo detecta.
"""

from __future__ import annotations

from typing import Final

SIGNAL_TYPE_VALUES: Final[tuple[str, ...]] = (
    "WATCH",
    "PREPARE",
    "ENTER_SMALL",
    "ENTER",
    "ADD_FORBIDDEN",
    "REDUCE",
    "TAKE_PROFIT",
    "EXIT",
    "EMERGENCY_EXIT",
    "IGNORE",
)

NARRATIVE_STATE_VALUES: Final[tuple[str, ...]] = (
    "NASCENT",
    "EMERGING",
    "ACCELERATING",
    "VIRAL",
    "SATURATED",
    "DECELERATING",
    "EXHAUSTED",
    "REVIVING",
)

FEATURE_WINDOW_VALUES: Final[tuple[str, ...]] = (
    "5s",
    "15s",
    "30s",
    "1m",
    "3m",
    "5m",
    "15m",
    "1h",
)

ELIGIBILITY_VETO_VALUES: Final[tuple[str, ...]] = (
    "low_data_confidence",
    "high_rug_risk",
    "high_manipulation_risk",
    "insufficient_liquidity",
    "no_exit_route",
    "sell_simulation_failed",
    "price_impact_too_high",
    "creator_history_critical",
    "holder_concentration",
    "dangerous_cluster",
    "already_pumped",
    "narrative_exhausted",
    "excessive_spread",
    "stale_data",
    "source_divergence",
    "insufficient_sol",
    "daily_risk_limit_reached",
)
