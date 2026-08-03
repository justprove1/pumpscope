"""Autenticacion entre ExecutionEngine y signer (SECURITY.md 2).

HMAC del cuerpo mas un timestamp, con ventana anti-replay. Dos cosas que no son opcionales:

1. **Comparacion en tiempo constante.** Un `==` sobre el HMAC filtra por tiempo cuantos bytes
   coinciden, y con suficientes intentos eso permite construir una firma valida byte a byte.
2. **Ventana temporal.** Sin ella, capturar una peticion valida permite reenviarla mañana.

Un HMAC valido NO basta para firmar: la transaccion pasa igualmente por las nueve
validaciones de politica. Esto solo demuestra quien llama, no que lo que pide sea aceptable.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256

# Ventana de aceptacion. Corta a proposito: alarga el margen y alargas la ventana de replay.
DEFAULT_WINDOW = timedelta(seconds=30)


class AuthError(RuntimeError):
    """La peticion no esta autenticada o llega fuera de ventana."""


@dataclass(frozen=True, slots=True)
class SignedRequest:
    """Peticion firmada con HMAC."""

    body: str
    timestamp: datetime
    signature: str


def compute_signature(body: str, timestamp: datetime, shared_secret: str) -> str:
    """HMAC-SHA256 del cuerpo Y del timestamp.

    El timestamp entra en el HMAC: si quedara fuera, un atacante podria cambiarlo para
    revivir una peticion antigua sin invalidar la firma.
    """
    if not shared_secret:
        msg = "no hay secreto compartido configurado"
        raise AuthError(msg)
    payload = f"{timestamp.isoformat()}|{body}".encode()
    return hmac.new(shared_secret.encode("utf-8"), payload, sha256).hexdigest()


def sign_request(body: str, timestamp: datetime, shared_secret: str) -> SignedRequest:
    return SignedRequest(
        body=body,
        timestamp=timestamp,
        signature=compute_signature(body, timestamp, shared_secret),
    )


def verify_request(
    request: SignedRequest,
    shared_secret: str,
    now: datetime,
    window: timedelta = DEFAULT_WINDOW,
) -> None:
    """Verifica firma y ventana. Lanza `AuthError` si algo no cuadra.

    Se comprueba la VENTANA primero: rechazar por tiempo es mas barato que calcular un HMAC,
    y no filtra nada util.
    """
    age = abs((now - request.timestamp).total_seconds())
    if age > window.total_seconds():
        msg = f"peticion fuera de ventana: {age:.0f}s, maximo {window.total_seconds():.0f}s"
        raise AuthError(msg)

    expected = compute_signature(request.body, request.timestamp, shared_secret)
    # `compare_digest` y no `==`: la comparacion normal filtra por tiempo cuantos bytes
    # coinciden, y eso permite construir una firma valida a base de intentos.
    if not hmac.compare_digest(expected, request.signature):
        msg = "firma HMAC invalida"
        raise AuthError(msg)
