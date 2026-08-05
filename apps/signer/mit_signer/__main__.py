"""Punto de entrada del signer aislado.

Levanta el servicio de firma en la red interna de Docker. **No publica puerto al host**: solo
la API puede hablarle, y aun asi el firmante valida por su cuenta cada transaccion.

Arranca SIEMPRE, tambien con `SIGNER_MODE=disabled`. Asi el estado se puede consultar y queda
claro que esta apagado, en vez de parecer que el contenedor esta roto. Apagado, responde a
toda peticion de firma que no.
"""

from __future__ import annotations

import json
import logging
import os

import uvicorn

LOGGER = logging.getLogger("mit.signer")

PUERTO = int(os.environ.get("SIGNER_PORT", "8100"))


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(message)s")
    modo = os.environ.get("SIGNER_MODE", "disabled")
    puede_firmar = modo == "local_encrypted"

    LOGGER.info(
        json.dumps({"event": "signer_started", "mode": modo, "can_sign": puede_firmar})
    )

    if puede_firmar:
        # Se abre la cartera AL ARRANCAR, no con una orden delante: si la contrasena o el
        # fichero cifrado estan mal, es mejor descubrirlo ahora que en mitad de una venta.
        from mit_signer.cartera import CarteraError, cargar_o_crear

        try:
            cartera = cargar_o_crear()
        except CarteraError as exc:
            LOGGER.error(json.dumps({"event": "signer_cartera_error", "detail": str(exc)}))
            raise
        LOGGER.info(
            json.dumps(
                {
                    "event": "signer_listo",
                    # Solo la direccion publica. La privada no se registra jamas.
                    "direccion": str(cartera.pubkey()),
                    "max_por_orden_sol": os.environ.get("SIGNER_MAX_ORDER_SOL", "0.05"),
                    "max_diario_sol": os.environ.get("SIGNER_MAX_DAILY_SOL", "0.2"),
                }
            )
        )

    from mit_signer.servicio import app

    uvicorn.run(app, host="0.0.0.0", port=PUERTO, log_level="warning")  # noqa: S104


if __name__ == "__main__":
    main()
