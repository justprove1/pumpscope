"""NarrativeEngine (SPEC.md 9).

Tres piezas, y solo una necesita datos externos:

1. **La frontera del LLM** (`schema`) — validacion estricta de la salida del modelo. Es un
   guardarrail de CLAUDE.md 1 y se prueba entera sin llamar a ningun modelo.
2. **El ciclo de vida** (`states`) — NASCENT a EXHAUSTED, determinista, decidido por metricas
   medidas y NO por lo que opine el LLM.
3. **Los sub-scores** (`scoring`) — funciones puras sobre menciones ya recogidas.

Lo que falta es la ingesta social: X, Reddit y YouTube necesitan credenciales, y su adaptador
no existe (DATA_PROVIDERS.md 5). El motor funciona; no tiene de que alimentarse.
"""

from __future__ import annotations

from mit_narratives.schema import (
    LlmContractError,
    NarrativeLlmOutput,
    parse_llm_output,
)
from mit_narratives.scoring import (
    Mention,
    authentic_mentions,
    cross_platform_spread,
    influencer_score,
    is_creator_only,
    mention_acceleration,
    mention_velocity,
    narrative_score,
    spam_probability,
    unique_author_growth,
)
from mit_narratives.states import (
    EXHAUSTED_STATES,
    NarrativeSignals,
    classify,
    is_exhausted,
)

__all__ = [
    "EXHAUSTED_STATES",
    "LlmContractError",
    "Mention",
    "NarrativeLlmOutput",
    "NarrativeSignals",
    "authentic_mentions",
    "classify",
    "cross_platform_spread",
    "influencer_score",
    "is_creator_only",
    "is_exhausted",
    "mention_acceleration",
    "mention_velocity",
    "narrative_score",
    "parse_llm_output",
    "spam_probability",
    "unique_author_growth",
]
