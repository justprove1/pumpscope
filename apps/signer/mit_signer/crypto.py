"""Cifrado en reposo del material de firma (SPEC.md 16, SECURITY.md 3).

**Este modulo nunca ve una seed phrase.** Cifra y descifra bytes opacos; que esos bytes sean
material de firma es cosa de quien los guarda. La distincion importa: no hay ninguna ruta por
la que una frase mnemotecnica entre aqui, porque el sistema no la usa ni la necesita.

Se usa libsodium via PyNaCl:

- **Argon2id** para derivar la clave de cifrado desde la contrasena. No PBKDF2 ni un simple
  hash: Argon2id esta disenado para ser caro en memoria, que es lo que encarece un ataque por
  fuerza bruta contra un archivo robado.
- **XSalsa20-Poly1305** (`SecretBox`) para el cifrado autenticado. Autenticado significa que
  un archivo manipulado falla al descifrar en vez de devolver basura silenciosamente.

El salt se guarda junto al cifrado porque no es secreto: su funcion es impedir tablas
precomputadas, no ocultarse.
"""

from __future__ import annotations

import json
from base64 import b64decode, b64encode
from dataclasses import dataclass

from nacl import pwhash, secret, utils
from nacl.exceptions import CryptoError

# Parametros de Argon2id. `MODERATE` es el compromiso recomendado por libsodium para material
# que se descifra pocas veces: caro para un atacante, tolerable al arrancar el servicio.
OPSLIMIT = pwhash.argon2id.OPSLIMIT_MODERATE
MEMLIMIT = pwhash.argon2id.MEMLIMIT_MODERATE
SALT_BYTES = pwhash.argon2id.SALTBYTES


class DecryptionError(RuntimeError):
    """No se pudo descifrar: contrasena incorrecta o archivo manipulado.

    NO se distingue entre los dos casos a proposito: decirle a un atacante cual de las dos
    cosas fallo le da informacion gratis.
    """


@dataclass(frozen=True, slots=True)
class EncryptedBlob:
    """Material cifrado con su salt. Serializable a un archivo."""

    ciphertext: str
    salt: str
    algorithm: str = "argon2id+xsalsa20poly1305"

    def to_json(self) -> str:
        return json.dumps(
            {"algorithm": self.algorithm, "salt": self.salt, "ciphertext": self.ciphertext},
            indent=2,
        )

    @classmethod
    def from_json(cls, raw: str) -> EncryptedBlob:
        payload = json.loads(raw)
        return cls(
            ciphertext=payload["ciphertext"],
            salt=payload["salt"],
            algorithm=payload.get("algorithm", "argon2id+xsalsa20poly1305"),
        )


def _derive(password: str, salt: bytes) -> bytes:
    if not password:
        msg = "la contrasena no puede estar vacia"
        raise ValueError(msg)
    return pwhash.argon2id.kdf(
        secret.SecretBox.KEY_SIZE,
        password.encode("utf-8"),
        salt,
        opslimit=OPSLIMIT,
        memlimit=MEMLIMIT,
    )


def encrypt(material: bytes, password: str) -> EncryptedBlob:
    """Cifra bytes opacos con una contrasena.

    Cada llamada usa un salt nuevo: cifrar dos veces lo mismo produce resultados distintos,
    asi que nadie puede deducir que dos archivos guardan el mismo material.
    """
    if not material:
        msg = "no hay nada que cifrar"
        raise ValueError(msg)
    salt = utils.random(SALT_BYTES)
    box = secret.SecretBox(_derive(password, salt))
    return EncryptedBlob(
        ciphertext=b64encode(box.encrypt(material)).decode("ascii"),
        salt=b64encode(salt).decode("ascii"),
    )


def decrypt(blob: EncryptedBlob, password: str) -> bytes:
    """Descifra. Lanza `DecryptionError` si la contrasena falla o el archivo se manipulo."""
    try:
        salt = b64decode(blob.salt)
        box = secret.SecretBox(_derive(password, salt))
        return bytes(box.decrypt(b64decode(blob.ciphertext)))
    except (CryptoError, ValueError, TypeError) as error:
        msg = "no se pudo descifrar: contrasena incorrecta o material manipulado"
        raise DecryptionError(msg) from error
