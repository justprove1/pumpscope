"""Entrada de la API cuando corre DENTRO del programa de escritorio.

Es la misma API de siempre, con dos diferencias que solo tienen sentido fuera de Docker:

1. **Sirve tambien el panel**, en la raiz. Asi el panel y la API comparten origen y no hay
   CORS que configurar ni puerto que recordar: el programa abre `/` y ya esta todo.
2. **No necesita Postgres ni Redis.** El panel solo usa cuatro rutas —precio de SOL, mercado
   en vivo del token, y preparar/consultar orden— y ninguna toca la base de datos ni la cache.
   Quien las usa es el radar, que este panel ya no lleva. `create_async_engine` y
   `redis.from_url` no conectan hasta que alguien los usa, asi que basta con darles una URL
   con la forma correcta para que el arranque no falle. Se comprobo: la API levanta y las
   cuatro rutas responden con las dos cosas apagadas.

Si algun dia el programa vuelve a querer el radar, habra que levantar de verdad esos dos
servicios; entonces estas URL de relleno dejaran de valer y hay que cambiarlas por las reales.
"""

from __future__ import annotations

import os
from pathlib import Path

# **Antes de importar la app**: su `lifespan` lee `DATABASE_URL` al arrancar y revienta si no
# esta. El puerto 1 es deliberado —no hay nada escuchando ahi—, de modo que si alguna ruta
# intentara usar la base de datos fallaria en el acto en vez de conectarse por accidente a
# una que no le corresponde.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://sin:usar@127.0.0.1:1/sin_usar")
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:1/0")

from fastapi.staticfiles import StaticFiles  # noqa: E402  (tiene que ir tras las variables)

from mit_api.main import app  # noqa: E402

_AQUI = Path(__file__).resolve()
_PANEL_POR_DEFECTO = _AQUI.parents[3] / "apps" / "panel"
PANEL = Path(os.environ.get("PUMPSCOPE_PANEL", _PANEL_POR_DEFECTO))

if not (PANEL / "index.html").is_file():  # pragma: no cover - solo empaquetado mal hecho
    raise RuntimeError(
        f"No encuentro el panel en {PANEL}. Se puede indicar con PUMPSCOPE_PANEL."
    )

# Va el ULTIMO a proposito: montar en `/` captura todo lo que no haya reclamado ya otra ruta,
# asi que si se montara antes se comeria `/v1/...` y la API dejaria de existir.
app.mount("/", StaticFiles(directory=str(PANEL), html=True), name="panel")
