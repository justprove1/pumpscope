"""Feature engineering por ventanas (5s a 1h).

STUB Fase 0: sin implementacion. Fase 2 (SPEC.md 10).

Invariante del paquete: una feature solo puede usar informacion disponible ANTES del
timestamp de prediccion. Cada feature declara su ventana de mirada atras y el motor rechaza
las que no la respeten. El data leakage no se revisa a mano: se hace imposible por
construccion.
"""

from __future__ import annotations

__all__: list[str] = []
