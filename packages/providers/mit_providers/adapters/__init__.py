"""Adaptadores concretos de proveedores.

**VACIO A PROPOSITO.** No es un olvido ni un TODO pendiente.

SPEC.md 32: "Cuando una API no este confirmada: no inventes su funcionamiento; crea una
interfaz abstracta; marca el proveedor como pendiente de configuracion; implementa primero
una alternativa basada en datos on-chain."

Escribir un adaptador exige antes:

1. Verificar los endpoints en la documentacion VIGENTE del proveedor.
2. Capturar una respuesta real y guardarla como fixture en `tests/fixtures/`.
3. Escribir el modelo de validacion contra esa respuesta real.
4. Comprobar los limites de uso reales y configurar el rate limiter por debajo.
5. Confirmar que el uso previsto no viola sus terminos de servicio.

Nada de eso se puede hacer en Fase 0. Un adaptador escrito de memoria es exactamente el
"codigo ficticio presentado como funcional" que CLAUDE.md 2 prohibe.

Orden previsto en Fase 1 (DATA_PROVIDERS.md 1: primero lo que da verdad, luego lo que da
conveniencia):

    1. RPC de Solana         — OnChainReadProvider    (base de todo)
    2. Helius WebSocket      — EventStreamProvider    (deteccion < 1s)
    3. Pump.fun on-chain     — BondingCurveProvider   (decodificacion propia, sin API)
    4. Jupiter               — QuoteProvider          (solo al llegar a ejecucion)
    5. DexScreener           — MarketDataProvider     (corroboracion secundaria)
"""

from __future__ import annotations

__all__: list[str] = []
