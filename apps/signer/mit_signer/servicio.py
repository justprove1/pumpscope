"""Servicio de firma. Es el unico componente con acceso a la clave.

Solo escucha en la red interna de Docker: no publica puerto al host. Quien le habla es la
API, y aun asi no se fia de ella —ver `policy.py`—: valida la transaccion decodificada, no lo
que le digan que contiene.

**Arranca apagado.** Sin `SIGNER_MODE=local_encrypted` responde a todo que no. Encenderlo es
una decision explicita, no un descuido de configuracion.

Cada firma queda registrada con su importe, su destino y su resultado. Si algun dia hay que
reconstruir que paso, ese registro es lo unico que lo permite.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from solders.transaction import Transaction

from solders.pubkey import Pubkey

from mit_signer.cartera import CarteraError, cargar_o_crear
from mit_signer.policy import Contador, Limites, PolicyError, programas_de, validar

LOGGER = logging.getLogger("mit.signer")

MODO = os.environ.get("SIGNER_MODE", "disabled")
LAMPORTS = 1_000_000_000


def _limites() -> Limites:
    """Topes en SOL, del entorno. Por defecto, deliberadamente pequenos.

    Quien los suba que lo haga a sabiendas: son el techo de lo que se puede perder en un dia
    si todo lo demas falla.
    """
    por_orden = float(os.environ.get("SIGNER_MAX_ORDER_SOL", "0.05"))
    diario = float(os.environ.get("SIGNER_MAX_DAILY_SOL", "0.2"))
    return Limites(
        max_por_orden=int(por_orden * LAMPORTS),
        max_diario=int(diario * LAMPORTS),
    )


def _ruta_contador() -> Path:
    """Donde se guarda lo gastado hoy. Junto a la clave, que es lo que ya hay que proteger."""
    por_entorno = os.environ.get("SIGNER_COUNTER_PATH", "").strip()
    if por_entorno:
        return Path(por_entorno)
    return Path(os.environ.get("SIGNER_KEY_PATH", "/data/signer/trading_key.enc")).with_name(
        "gastado_hoy.json"
    )


# En disco, no en memoria: dentro del programa de escritorio este proceso muere cada vez que
# se cierra la ventana, y un tope diario que se reinicia con cada apertura no frena nada.
def _ruta_retirada() -> Path:
    """Donde se guarda TU cartera: el unico destino al que se puede sacar saldo."""
    return _ruta_contador().with_name("cartera_retirada.txt")


def _retirada() -> str | None:
    """La cartera de retirada registrada, o `None` si aun no hay ninguna.

    Vive en el firmante y no en la API a proposito. Si la guardara quien pide las firmas,
    cambiarla seria tan facil como pedir la transferencia, y entonces fijar el destino no
    protegeria de nada: el atacante pondria el suyo justo antes de pedirla.
    """
    ruta = _ruta_retirada()
    try:
        valor = ruta.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return valor or None


CONTADOR = Contador(ruta=_ruta_contador())
app = FastAPI(title="mit-signer", description="Servicio de firma aislado")


class PeticionFirma(BaseModel):
    transaction_base64: str = Field(description="Transaccion SIN firmar, serializada")
    importe_lamports: int = Field(ge=0, description="Lo que el solicitante dice que mueve")
    motivo: str = Field(default="", max_length=120, description="Para el registro de auditoria")


class RespuestaFirma(BaseModel):
    signed_transaction_base64: str
    firmante: str


def _registrar(evento: str, **campos: Any) -> None:
    LOGGER.info(json.dumps({"event": evento, "ts": datetime.now(UTC).isoformat(), **campos}))


@app.get("/health")
async def health() -> dict[str, Any]:
    limites = _limites()
    activo = MODO == "local_encrypted"
    salida: dict[str, Any] = {
        "modo": MODO,
        "puede_firmar": activo,
        "max_por_orden_sol": limites.max_por_orden / LAMPORTS,
        "max_diario_sol": limites.max_diario / LAMPORTS,
        "gastado_hoy_sol": CONTADOR.gastado / LAMPORTS,
        "disponible_hoy_sol": CONTADOR.disponible(limites.max_diario) / LAMPORTS,
    }
    salida["cartera_retirada"] = _retirada()
    if activo:
        try:
            salida["direccion"] = str(cargar_o_crear().pubkey())
        except CarteraError as exc:
            salida["error"] = str(exc)
    return salida


@app.post("/firmar", response_model=RespuestaFirma)
async def firmar(peticion: PeticionFirma) -> RespuestaFirma:
    """Firma una transaccion, si y solo si pasa toda la politica."""
    if MODO != "local_encrypted":
        _registrar("firma_rechazada", motivo="signer apagado", modo=MODO)
        raise HTTPException(
            status_code=403,
            detail=f"el firmante esta apagado (SIGNER_MODE={MODO}). No firma nada.",
        )

    try:
        cartera = cargar_o_crear()
    except CarteraError as exc:
        _registrar("firma_rechazada", motivo="cartera ilegible")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    try:
        crudo = base64.b64decode(peticion.transaction_base64)
        transaccion = Transaction.from_bytes(crudo)
    except Exception as exc:
        _registrar("firma_rechazada", motivo="transaccion ilegible")
        raise HTTPException(status_code=400, detail=f"transaccion ilegible: {exc}") from exc

    limites = _limites()
    try:
        validar(
            transaccion,
            cartera=cartera.pubkey(),
            importe_lamports=peticion.importe_lamports,
            limites=limites,
            contador=CONTADOR,
            retirada=_retirada(),
        )
    except PolicyError as exc:
        _registrar(
            "firma_rechazada",
            motivo=str(exc),
            importe_sol=peticion.importe_lamports / LAMPORTS,
            programas=sorted(programas_de(transaccion)),
        )
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    # La cuenta del dia se anota ANTES de firmar. Si algo revienta despues, se habra contado
    # de mas; contar de menos permitiria colar gasto por encima del tope reintentando.
    CONTADOR.anotar(peticion.importe_lamports)

    firmada = Transaction.from_bytes(crudo)
    firmada.sign([cartera], transaccion.message.recent_blockhash)

    _registrar(
        "firma_emitida",
        importe_sol=peticion.importe_lamports / LAMPORTS,
        motivo=peticion.motivo,
        programas=sorted(programas_de(transaccion)),
        gastado_hoy_sol=CONTADOR.gastado / LAMPORTS,
    )
    return RespuestaFirma(
        signed_transaction_base64=base64.b64encode(bytes(firmada)).decode(),
        firmante=str(cartera.pubkey()),
    )


class CarteraRetirada(BaseModel):
    direccion: str = Field(min_length=32, max_length=44)


@app.post("/retirada")
async def registrar_retirada(peticion: CarteraRetirada) -> dict[str, Any]:
    """Registra TU cartera como unico destino al que se puede sacar saldo.

    **Se registra una sola vez.** Cambiarla exige borrar el fichero a mano, con el programa
    cerrado. Es incomodo a proposito: si esta ruta permitiera repuntarla, fijar el destino no
    protegeria de nada —quien pudiera pedir la transferencia pondria antes su direccion— y
    todo el cerrojo seria decorativo.
    """
    if MODO != "local_encrypted":
        raise HTTPException(status_code=403, detail="el firmante esta apagado")

    try:
        destino = Pubkey.from_string(peticion.direccion.strip())
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail="esa no es una direccion de Solana valida. Copiala de Phantom entera.",
        ) from exc

    # Mandarse el saldo a uno mismo no retira nada y deja el cerrojo abierto para siempre.
    try:
        if str(destino) == str(cargar_o_crear().pubkey()):
            raise HTTPException(
                status_code=400,
                detail="esa es la cartera del propio programa: la de retirada tiene que ser la tuya",
            )
    except CarteraError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    ya = _retirada()
    if ya and ya != str(destino):
        raise HTTPException(
            status_code=409,
            detail=(
                f"ya hay una cartera de retirada registrada ({ya}). Para cambiarla, cierra el "
                f"programa y borra el fichero {_ruta_retirada()}."
            ),
        )

    ruta = _ruta_retirada()
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(str(destino), encoding="utf-8")
    ruta.chmod(0o600)
    _registrar("cartera_retirada_registrada", direccion=str(destino))
    return {"cartera_retirada": str(destino)}
