"""Punto de entrada del signer aislado.

En Fase 1 el signer NO firma nada: no existe ExecutionEngine al que servir. Arranca, declara
su modo y se queda esperando. Es intencional que exista y no haga nada: asi el contenedor
esta en el compose desde el principio, con su aislamiento de red ya probado, y activar la
firma en Fase 6 no requiere tocar la infraestructura.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import time

LOGGER = logging.getLogger("mit.signer")


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(message)s")
    mode = os.environ.get("SIGNER_MODE", "disabled")
    LOGGER.info(json.dumps({"event": "signer_started", "mode": mode, "can_sign": False}))

    if mode != "disabled":
        # Se avisa alto y claro: llegar aqui con un modo activo en Fase 1 significa que
        # alguien configuro la firma antes de que exista nada que firmar.
        LOGGER.warning(
            json.dumps(
                {
                    "event": "signer_mode_unexpected",
                    "mode": mode,
                    "detail": "La firma se implementa en Fase 6 (LIVE_TRADING_CHECKLIST.md).",
                }
            )
        )

    running = True

    def stop(*_: object) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while running:
        time.sleep(1)
    LOGGER.info(json.dumps({"event": "signer_stopped"}))


if __name__ == "__main__":
    main()
