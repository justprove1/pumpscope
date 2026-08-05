"""La cartera del firmante: se genera aqui, vive cifrada y no sale nunca.

**Tu frase de recuperacion no entra en este modulo, ni en ningun otro.** La cartera del
firmante es NUEVA: la genera el propio programa la primera vez y guarda su clave cifrada.
No hay ninguna ruta por la que una cartera existente pueda importarse, y es a proposito:
asi lo maximo que se puede perder es lo que tu decidas mandarle, y nunca lo que tengas en la
cartera de siempre.

La clave se guarda cifrada con Argon2id + libsodium (ver `crypto.py`). Eso protege el fichero
si alguien copia el disco. **No protege el momento de firmar**, porque para firmar hay que
descifrarla y ahi vive en memoria del proceso. Quien pueda leer la memoria de este proceso
puede sacar la clave; por eso el firmante corre aislado y por eso los topes existen.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from pathlib import Path

from solders.keypair import Keypair
from solders.pubkey import Pubkey

from mit_signer.crypto import EncryptedBlob, decrypt, encrypt

LOGGER = logging.getLogger("mit.signer.cartera")


class CarteraError(RuntimeError):
    """No se pudo abrir ni crear la cartera del firmante."""


def _ruta_clave() -> Path:
    return Path(os.environ.get("SIGNER_KEY_PATH", "/data/signer/trading_key.enc"))


def _ruta_password() -> Path:
    return Path(os.environ.get("SIGNER_PASSWORD_PATH", "/data/signer/key_password"))


def _password() -> str:
    """Contrasena de cifrado. Se genera sola la primera vez y se guarda aparte de la clave.

    Guardarlas en el mismo sitio no cifraria nada en la practica —quien se lleve una se lleva
    la otra— pero separarlas al menos obliga a robar dos ficheros en vez de uno, y permite
    poner la contrasena en otro volumen o pasarla por entorno si algun dia se quiere.
    """
    desde_entorno = os.environ.get("SIGNER_PASSWORD", "").strip()
    if desde_entorno:
        return desde_entorno

    ruta = _ruta_password()
    if ruta.exists():
        return ruta.read_text(encoding="utf-8").strip()

    ruta.parent.mkdir(parents=True, exist_ok=True)
    generada = secrets.token_urlsafe(48)
    ruta.write_text(generada, encoding="utf-8")
    ruta.chmod(0o600)
    LOGGER.info(json.dumps({"event": "signer_password_creada", "ruta": str(ruta)}))
    return generada


def cargar_o_crear() -> Keypair:
    """Devuelve la cartera del firmante, creandola cifrada si no existia."""
    ruta = _ruta_clave()
    password = _password()

    if ruta.exists():
        try:
            blob = EncryptedBlob.from_json(ruta.read_text(encoding="utf-8"))
            material = decrypt(blob, password)
        except Exception as exc:
            msg = (
                f"no se pudo descifrar la cartera del firmante en {ruta}: {exc}. "
                "Si la contrasena se perdio, la clave es irrecuperable."
            )
            raise CarteraError(msg) from exc
        return Keypair.from_bytes(material)

    # Primera vez: cartera NUEVA. Nunca se importa una existente.
    ruta.parent.mkdir(parents=True, exist_ok=True)
    nueva = Keypair()
    ruta.write_text(encrypt(bytes(nueva), password).to_json(), encoding="utf-8")
    ruta.chmod(0o600)
    LOGGER.info(
        json.dumps(
            {
                "event": "signer_cartera_creada",
                # La direccion PUBLICA si se registra: hace falta para poder fondearla.
                # La privada no se escribe en ningun log, nunca.
                "direccion": str(nueva.pubkey()),
                "ruta": str(ruta),
            }
        )
    )
    return nueva


def direccion() -> Pubkey:
    """Direccion publica de la cartera del firmante, la que hay que fondear."""
    return cargar_o_crear().pubkey()
