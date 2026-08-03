"""Frontera del LLM (SPEC.md 9, CLAUDE.md 1).

**Este es el unico punto por el que un modelo generativo entra en el sistema, y es el limite
de seguridad mas facil de romper sin darse cuenta.**

La regla no es "validar la respuesta". Es: la respuesta se valida ENTERA o se rechaza ENTERA.
Un modelo que devuelve un campo de mas no es un modelo servicial: es una respuesta que no
cumple el contrato, y aceptarla parcialmente abre la puerta a que un dia llegue un
`recommended_size_sol` y alguien lo lea.

Por eso `extra="forbid"`. Y por eso el resultado alimenta UNICAMENTE el NarrativeScore: no
toca importes, ni limites de riesgo, ni firma. Eso no lo garantiza este modulo, lo garantiza
que ningun otro modulo importe de aqui nada que no sea el score.
"""

from __future__ import annotations

import json
from typing import Any

from mit_data_models.enums import NarrativeState
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class LlmContractError(ValueError):
    """La salida del LLM no cumple el contrato. NO se intenta arreglar ni interpretar."""


class NarrativeLlmOutput(BaseModel):
    """Contrato EXACTO de lo que puede devolver el LLM (ejemplo de SPEC.md 9).

    Cualquier desviacion —campo extra, tipo distinto, rango fuera— invalida la respuesta
    completa.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=False)

    narrative: str = Field(min_length=1, max_length=256)
    state: NarrativeState
    score: float = Field(ge=0.0, le=100.0)
    confidence: float = Field(ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list, max_length=10)


def parse_llm_output(raw: str | bytes | dict[str, Any]) -> NarrativeLlmOutput:
    """Convierte la salida cruda del LLM en un objeto validado, o falla.

    Acepta texto porque es lo que devuelve un modelo, pero NO acepta texto libre: tiene que
    ser JSON y tiene que cumplir el esquema. No se hace ninguna extraccion heuristica de
    JSON dentro de prosa: si el modelo no obedece el formato, la respuesta se descarta. Ser
    tolerante aqui es entrenar al sistema para aceptar lo que sea.
    """
    if isinstance(raw, dict):
        payload: Any = raw
    else:
        text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
        try:
            payload = json.loads(text)
        except (ValueError, TypeError) as error:
            msg = f"la salida del LLM no es JSON valido: {error}"
            raise LlmContractError(msg) from error

    if not isinstance(payload, dict):
        msg = f"la salida del LLM debe ser un objeto JSON, llego {type(payload).__name__}"
        raise LlmContractError(msg)

    try:
        return NarrativeLlmOutput.model_validate(payload)
    except ValidationError as error:
        msg = f"la salida del LLM no cumple el esquema: {error.error_count()} problema(s)"
        raise LlmContractError(msg) from error
