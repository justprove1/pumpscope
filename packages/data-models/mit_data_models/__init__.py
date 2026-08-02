"""Contratos de datos del sistema. Fuente unica de verdad de los tipos.

Contract-first (CLAUDE.md 0.3): estos modelos se definen ANTES que la logica que los produce
o los consume. Cualquier paquete puede importarlos; ninguno puede redefinir un tipo que ya
viva aqui.

Los modelos son `frozen=True` y `extra="forbid"` a proposito:

- **frozen**: un dato observado no se muta despues. Si cambia, es otra observacion, con su
  propio timestamp. Esto hace imposible una clase entera de bugs de auditoria.
- **extra=forbid**: un campo inesperado en una respuesta externa es una senal de que la API
  cambio. Preferimos enterarnos con un error que ignorarlo en silencio.
"""

from __future__ import annotations

from mit_data_models.encoding import (
    ELIGIBILITY_VETO_VALUES,
    FEATURE_WINDOW_VALUES,
    NARRATIVE_STATE_VALUES,
    SIGNAL_TYPE_VALUES,
)
from mit_data_models.enums import (
    EligibilityVeto,
    FeatureWindow,
    NarrativeState,
    OrderStatus,
    ProviderStatus,
    Side,
    SignalType,
    SocialPlatform,
    TokenStatus,
    TradingMode,
    Venue,
)
from mit_data_models.envelope import Observation, ProviderHealth
from mit_data_models.market import (
    Candle,
    Quote,
    QuoteRequest,
    SimulationResult,
    TradeEvent,
)
from mit_data_models.social import (
    NarrativeAssessment,
    NarrativeSummary,
    NewsItem,
    SocialPost,
)
from mit_data_models.tokens import (
    BondingCurveState,
    CreatorProfile,
    HolderDistribution,
    LiquidityState,
    TokenIdentity,
    TokenRef,
)

__all__ = [
    "ELIGIBILITY_VETO_VALUES",
    "FEATURE_WINDOW_VALUES",
    "NARRATIVE_STATE_VALUES",
    "SIGNAL_TYPE_VALUES",
    "BondingCurveState",
    "Candle",
    "CreatorProfile",
    "EligibilityVeto",
    "FeatureWindow",
    "HolderDistribution",
    "LiquidityState",
    "NarrativeAssessment",
    "NarrativeState",
    "NarrativeSummary",
    "NewsItem",
    "Observation",
    "OrderStatus",
    "ProviderHealth",
    "ProviderStatus",
    "Quote",
    "QuoteRequest",
    "Side",
    "SignalType",
    "SimulationResult",
    "SocialPlatform",
    "SocialPost",
    "TokenIdentity",
    "TokenRef",
    "TokenStatus",
    "TradeEvent",
    "TradingMode",
    "Venue",
]
