"""Servicio de firma AISLADO.

STUB Fase 0: sin implementacion. Fase 6 (SPEC.md 16, SECURITY.md 2).

Unico componente con acceso al material criptografico. Arranca con SIGNER_MODE=disabled y
rechaza toda peticion hasta completar LIVE_TRADING_CHECKLIST.md.

Implementa `mit_providers.base.signing.SigningService` y aplica sus nueve validaciones por
si mismo: asume que quien le habla puede estar comprometido.
"""

from __future__ import annotations

__all__: list[str] = []
