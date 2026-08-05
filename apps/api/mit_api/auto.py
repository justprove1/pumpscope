"""Stop loss AUTOMATICO: vigila y vende sin que haya nadie delante.

**Esto es lo unico del sistema que mueve dinero sin que una persona lo apruebe.** Por eso:

- Opera con la cartera DEL FIRMANTE, que es nueva y separada de la del usuario. Lo maximo que
  se puede perder es lo que se le haya mandado a esa cartera.
- El firmante valida cada transaccion por su cuenta y tiene sus propios topes. Un bug aqui no
  puede saltarselos: esta capa pide, no decide.
- Todo queda registrado.

La vigilancia corre en el servidor, no en el navegador. Es la diferencia entre un stop que
funciona con la pestana cerrada y uno que no.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx
from fastapi import APIRouter, HTTPException
from mit_pumpfun.trade import associated_token_address
from pydantic import BaseModel, Field
from solders.hash import Hash
from solders.message import Message
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from solders.transaction import Transaction

from mit_api.trade import (
    LAMPORTS_PER_SOL,
    PrepareRequest,
    _abrir_rpc,
    _token_balance,
    curva_fresca,
    prepare,
)

LOGGER = logging.getLogger("mit.auto")
router = APIRouter(prefix="/v1/auto", tags=["auto"])

# El firmante vive en la red interna de Docker y no publica puerto al host.
SIGNER_URL = os.environ.get("SIGNER_URL", "http://signer:8100")

# Cada cuanto se mira el precio de cada posicion vigilada. Mas rapido no ayuda: la curva solo
# cambia cuando alguien opera, y cada vuelta son consultas al RPC.
INTERVALO_S = 2.0

# Si el precio no se puede leer, no se vende a ciegas. Pero tampoco se calla para siempre:
# tras esta racha se avisa en el registro, porque una posicion sin vigilancia real es peor
# que una sin vigilancia declarada.
FALLOS_PARA_AVISAR = 15


@dataclass
class Vigilada:
    """Una posicion que el sistema vigila y puede vender por su cuenta."""

    mint: str
    entrada_cap: float
    pico_cap: float
    trailing_pct: float
    # Suelo duro desde la ENTRADA. Distinto del trailing, que mide desde el maximo. Como el
    # maximo arranca en la entrada, la caida desde el maximo va siempre por delante: este
    # suelo solo llega a mandar si su umbral es MENOR que el del trailing.
    stop_loss_pct: float
    slippage_bps: int
    abierta_en: float = field(default_factory=time.monotonic)
    fallos_lectura: int = 0
    cerrada: bool = False
    motivo_cierre: str = ""
    firma_venta: str = ""
    ultima_cap: float = 0.0

    def caida_pct(self) -> float:
        """Caida desde el maximo alcanzado. Es lo que mira el trailing."""
        if self.pico_cap <= 0:
            return 0.0
        return ((self.pico_cap - self.ultima_cap) / self.pico_cap) * 100

    def caida_desde_entrada_pct(self) -> float:
        """Caida desde la entrada. Es lo que mira el stop loss. Nunca negativa."""
        if self.entrada_cap <= 0:
            return 0.0
        return max(0.0, ((self.entrada_cap - self.ultima_cap) / self.entrada_cap) * 100)

    def motivo_disparo(self) -> str | None:
        """Por que hay que vender, o `None` si no hay que vender.

        Una sola funcion decide Y explica, para que el registro no pueda contar una cosa
        distinta de la que provoco la venta.
        """
        desde_entrada = self.caida_desde_entrada_pct()
        if self.stop_loss_pct > 0 and desde_entrada >= self.stop_loss_pct:
            return f"stop loss: caída del {desde_entrada:.1f}% desde la entrada"
        caida = self.caida_pct()
        if self.trailing_pct > 0 and caida >= self.trailing_pct:
            return f"trailing: caída del {caida:.1f}% desde el máximo"
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "mint": self.mint,
            "entrada_cap": self.entrada_cap,
            "pico_cap": self.pico_cap,
            "cap_actual": self.ultima_cap,
            "caida_pct": round(self.caida_pct(), 2),
            "caida_desde_entrada_pct": round(self.caida_desde_entrada_pct(), 2),
            "trailing_pct": self.trailing_pct,
            "stop_loss_pct": self.stop_loss_pct,
            "cerrada": self.cerrada,
            "motivo": self.motivo_cierre,
            "firma_venta": self.firma_venta,
            "segundos_abierta": round(time.monotonic() - self.abierta_en, 1),
        }


_VIGILADAS: dict[str, Vigilada] = {}
_TAREA: asyncio.Task[None] | None = None


def _log(evento: str, **campos: Any) -> None:
    LOGGER.info(json.dumps({"event": evento, **campos}))


async def direccion_firmante() -> str | None:
    """Direccion publica de la cartera del firmante, o `None` si esta apagado."""
    try:
        async with httpx.AsyncClient(timeout=8.0) as cliente:
            r = await cliente.get(f"{SIGNER_URL}/health")
            datos = r.json()
    except Exception:
        return None
    return datos.get("direccion") if datos.get("puede_firmar") else None


async def _firmar(transaccion_b64: str, importe_lamports: int, motivo: str) -> str:
    """Pide la firma. El firmante puede decir que no, y decir que no es su trabajo."""
    async with httpx.AsyncClient(timeout=20.0) as cliente:
        r = await cliente.post(
            f"{SIGNER_URL}/firmar",
            json={
                "transaction_base64": transaccion_b64,
                "importe_lamports": importe_lamports,
                "motivo": motivo,
            },
        )
    if r.status_code != 200:
        detalle = r.json().get("detail", r.text) if r.content else r.text
        msg = f"el firmante rechaza: {detalle}"
        raise RuntimeError(msg)
    return str(r.json()["signed_transaction_base64"])


async def _enviar(firmada_b64: str) -> str:
    """Envia la transaccion ya firmada y devuelve su firma."""
    async with _abrir_rpc() as rpc:
        resultado = await rpc.call(
            "sendTransaction",
            [firmada_b64, {"encoding": "base64", "skipPreflight": False, "maxRetries": 3}],
        )
    return str(resultado)


async def _vender_todo(posicion: Vigilada, firmante: str, motivo: str) -> None:
    """Construye la venta del 100%, la manda a firmar y la envia."""
    respuesta = await prepare(
        PrepareRequest(
            mint=posicion.mint,
            user=firmante,
            side="sell",
            sell_percent=100.0,
            slippage_bps=posicion.slippage_bps,
        )
    )
    # El importe declarado es lo que se espera RECIBIR: para el tope del firmante lo que
    # importa es la magnitud de lo que se mueve, no su signo.
    importe = int(float(respuesta.summary.get("expected_sol", 0.0)) * LAMPORTS_PER_SOL)
    firmada = await _firmar(respuesta.transaction_base64, importe, motivo[:120])
    firma = await _enviar(firmada)

    posicion.cerrada = True
    posicion.motivo_cierre = motivo
    posicion.firma_venta = firma
    _log(
        "auto_venta_enviada",
        mint=posicion.mint,
        motivo=motivo,
        caida_pct=round(posicion.caida_pct(), 2),
        firma=firma,
    )


async def _vuelta() -> None:
    """Una pasada por todas las posiciones vigiladas."""
    if not _VIGILADAS:
        return
    firmante = await direccion_firmante()
    if firmante is None:
        return

    for posicion in list(_VIGILADAS.values()):
        if posicion.cerrada:
            continue
        try:
            async with _abrir_rpc() as rpc:
                curva = await curva_fresca(rpc, Pubkey.from_string(posicion.mint))
        except Exception as exc:
            posicion.fallos_lectura += 1
            if posicion.fallos_lectura == FALLOS_PARA_AVISAR:
                _log("auto_sin_lectura", mint=posicion.mint, detalle=str(exc)[:120])
            continue

        if curva is None or curva.complete:
            # Graduado: su precio ya no es comparable y aqui no se puede vender. Se deja de
            # vigilar en vez de disparar por una caida que no existe.
            if curva is not None and curva.complete:
                posicion.cerrada = True
                posicion.motivo_cierre = "graduó: fuera de la curva, ciérrala a mano"
                _log("auto_graduado", mint=posicion.mint)
            else:
                posicion.fallos_lectura += 1
            continue

        posicion.fallos_lectura = 0
        precio = curva.virtual_quote_reserves / curva.virtual_token_reserves
        cap = precio * curva.token_total_supply / LAMPORTS_PER_SOL
        posicion.ultima_cap = cap
        posicion.pico_cap = max(posicion.pico_cap, cap)

        motivo = posicion.motivo_disparo()
        if motivo is None:
            continue

        try:
            await _vender_todo(posicion, firmante, motivo)
        except Exception as exc:
            _log("auto_venta_fallida", mint=posicion.mint, detalle=str(exc)[:200])


async def _bucle() -> None:
    while True:
        try:
            await _vuelta()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _log("auto_error", detalle=str(exc)[:200])
        await asyncio.sleep(INTERVALO_S)


def arrancar() -> None:
    global _TAREA
    if _TAREA is None or _TAREA.done():
        _TAREA = asyncio.create_task(_bucle())
        _log("auto_vigilante_arrancado", intervalo_s=INTERVALO_S)


async def parar() -> None:
    global _TAREA
    if _TAREA is not None:
        _TAREA.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _TAREA
        _TAREA = None


class AltaVigilancia(BaseModel):
    mint: str
    trailing_pct: float = Field(default=15.0, ge=0, lt=100)
    stop_loss_pct: float = Field(default=10.0, ge=0, lt=100)
    slippage_bps: int = Field(default=1500, ge=1, le=5000)


@router.get("/estado")
async def estado() -> dict[str, Any]:
    """Que vigila el sistema ahora mismo, y si puede vender."""
    firmante = await direccion_firmante()
    saldo_sol = None
    # Tu cartera registrada. La guarda el firmante, no esta capa; aqui solo se reenvia para
    # que el panel sepa si tiene que pedirla o si ya puede ofrecer el boton de retirar.
    retirada = None
    try:
        async with httpx.AsyncClient(timeout=8.0) as cliente:
            retirada = (await cliente.get(f"{SIGNER_URL}/health")).json().get("cartera_retirada")
    except Exception:
        retirada = None
    if firmante is not None:
        async with _abrir_rpc() as rpc:
            try:
                r = await rpc.call("getBalance", [firmante])
                saldo_sol = (r or {}).get("value", 0) / LAMPORTS_PER_SOL
            except Exception:
                saldo_sol = None
    return {
        "firmante": firmante,
        "puede_vender_solo": firmante is not None,
        "saldo_firmante_sol": saldo_sol,
        "cartera_retirada": retirada,
        "vigiladas": [v.as_dict() for v in _VIGILADAS.values()],
    }


@router.post("/vigilar")
async def vigilar(alta: AltaVigilancia) -> dict[str, Any]:
    """Empieza a vigilar una posicion que YA tiene la cartera del firmante."""
    firmante = await direccion_firmante()
    if firmante is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "el firmante esta apagado: sin el, nadie puede vender sin ti. "
                "Arrancalo con SIGNER_MODE=local_encrypted."
            ),
        )
    try:
        mint = Pubkey.from_string(alta.mint)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"mint invalido: {exc}") from exc

    # Sin ninguna regla no hay vigilancia, y creer que la hay es peor que saber que no.
    if alta.trailing_pct <= 0 and alta.stop_loss_pct <= 0:
        raise HTTPException(
            status_code=400,
            detail="pon al menos una regla: trailing, stop loss, o las dos. Con las dos a "
            "cero no habria nada que vigilar.",
        )

    async with _abrir_rpc() as rpc:
        curva = await curva_fresca(rpc, mint)
        if curva is None or curva.complete:
            raise HTTPException(
                status_code=409, detail="ese token no esta operable en la curva de Pump.fun"
            )
        # Sin tokens no hay nada que vender, y vigilar el aire da falsa tranquilidad.
        cuenta = await rpc.call("getAccountInfo", [str(mint), {"encoding": "base64"}])
        programa = Pubkey.from_string(cuenta["value"]["owner"])
        ata = associated_token_address(Pubkey.from_string(firmante), mint, programa)
        saldo = await _token_balance(rpc, ata)

    if saldo <= 0:
        raise HTTPException(
            status_code=409,
            detail=(
                f"la cartera del firmante ({firmante[:8]}…) no tiene tokens de este mint. "
                "Compra con ella antes de pedir que los vigile."
            ),
        )

    precio = curva.virtual_quote_reserves / curva.virtual_token_reserves
    cap = precio * curva.token_total_supply / LAMPORTS_PER_SOL
    _VIGILADAS[alta.mint] = Vigilada(
        mint=alta.mint,
        entrada_cap=cap,
        pico_cap=cap,
        ultima_cap=cap,
        trailing_pct=alta.trailing_pct,
        stop_loss_pct=alta.stop_loss_pct,
        slippage_bps=alta.slippage_bps,
    )
    arrancar()
    _log(
        "auto_vigilancia_alta",
        mint=alta.mint,
        trailing_pct=alta.trailing_pct,
        stop_loss_pct=alta.stop_loss_pct,
        cap=cap,
    )
    return {"vigilando": True, **_VIGILADAS[alta.mint].as_dict()}


@router.post("/cancelar/{mint}")
async def cancelar(mint: str) -> dict[str, Any]:
    """Deja de vigilar. No vende: solo deja de mirar."""
    quitada = _VIGILADAS.pop(mint, None)
    _log("auto_vigilancia_baja", mint=mint, existia=quitada is not None)
    return {"cancelada": quitada is not None}


class OrdenPropia(BaseModel):
    """Una orden que firma el PROGRAMA con su propia cartera, sin navegador."""

    mint: str
    side: Literal["buy", "sell"]
    amount_sol: float | None = Field(default=None, gt=0, description="Compra: cuanto meter")
    sell_percent: float | None = Field(default=None, gt=0, le=100, description="Venta: que %")
    slippage_bps: int = Field(default=1_000, ge=1, le=5_000)
    priority_fee_microlamports: int = Field(default=500_000, ge=0, le=50_000_000)


@router.post("/operar")
async def operar(orden: OrdenPropia) -> dict[str, Any]:
    """Prepara, firma y envia una orden con la cartera del propio programa.

    Existe porque en la ventana nativa no hay Phantom —es una extension de navegador y ahi no
    se inyecta—, asi que la firma tiene que salir del firmante. El camino es el mismo que usa
    el stop automatico: se reaprovecha entero en vez de escribir otro, porque dos caminos que
    gastan dinero son dos sitios donde equivocarse.

    **Esta capa pide, no decide.** El firmante revalida la transaccion por su cuenta —lista de
    programas, pagador, tope por orden y tope diario— y puede decir que no. Que esta ruta se
    equivoque no basta para que salga una firma.
    """
    firmante = await direccion_firmante()
    if firmante is None:
        raise HTTPException(
            status_code=503,
            detail="el firmante del programa no responde o esta apagado: no hay con que firmar",
        )

    if orden.side == "buy" and orden.amount_sol is None:
        raise HTTPException(status_code=400, detail="una compra necesita amount_sol")
    if orden.side == "sell" and orden.sell_percent is None:
        raise HTTPException(status_code=400, detail="una venta necesita sell_percent")

    # `prepare` ya simula contra la cadena antes de devolver nada: si la orden es imposible
    # —curva completa, saldo corto, precio movido— falla aqui y no llega a firmarse.
    respuesta = await prepare(
        PrepareRequest(
            mint=orden.mint,
            user=firmante,
            side=orden.side,
            amount_sol=orden.amount_sol,
            sell_percent=orden.sell_percent,
            slippage_bps=orden.slippage_bps,
            priority_fee_microlamports=orden.priority_fee_microlamports,
        )
    )

    # Lo que se declara al firmante es la MAGNITUD de lo que se mueve, que es lo que sus topes
    # acotan. En una compra es lo que se mete; en una venta, lo que se espera recibir.
    if orden.side == "buy":
        importe = int(float(orden.amount_sol or 0.0) * LAMPORTS_PER_SOL)
        motivo = f"compra manual {orden.amount_sol} SOL"
    else:
        importe = int(float(respuesta.summary.get("expected_sol", 0.0)) * LAMPORTS_PER_SOL)
        motivo = f"venta manual {orden.sell_percent}%"

    try:
        firmada = await _firmar(respuesta.transaction_base64, importe, motivo[:120])
    except RuntimeError as exc:
        # El rechazo del firmante no es un error del servidor: es la politica haciendo su
        # trabajo, y el usuario tiene que leer POR QUE en vez de un 500 sin explicacion.
        _log("orden_propia_rechazada", mint=orden.mint, side=orden.side, motivo=str(exc))
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    firma = await _enviar(firmada)
    _log(
        "orden_propia_enviada",
        mint=orden.mint,
        side=orden.side,
        importe_sol=importe / LAMPORTS_PER_SOL,
        firma=firma,
    )
    return {"firma": firma, "firmante": firmante, "resumen": respuesta.summary}


class MiCartera(BaseModel):
    """Tu cartera. Se acepta la direccion pelada o un enlace que la contenga."""

    direccion: str = Field(min_length=32, max_length=200)


# Una direccion de Solana en base58: ni 0, ni O, ni I, ni l. Se busca DENTRO del texto para
# poder pegar un enlace de pump.fun o de un explorador sin tener que recortarlo a mano.
_BASE58 = re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,44}")


def extraer_direccion(texto: str) -> str | None:
    """Saca la direccion de lo que sea que se haya pegado.

    Se prueban todos los candidatos y se devuelve el primero que sea una clave publica de
    verdad, no solo algo con pinta. Una URL trae trozos que parecen base58 —el dominio, la
    ruta— y quedarse con el primer parecido daria una direccion inventada con muy buena pinta.
    """
    for candidato in _BASE58.findall(texto.strip()):
        try:
            return str(Pubkey.from_string(candidato))
        except Exception:
            continue
    return None


@router.post("/retirada")
async def registrar_retirada(cartera: MiCartera) -> dict[str, Any]:
    """Fija TU cartera como unico destino al que el programa puede sacar saldo."""
    direccion = extraer_direccion(cartera.direccion)
    if direccion is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "no encuentro ninguna direccion de Solana ahi dentro. Pega la direccion de tu "
                "cartera de Phantom, o un enlace que la lleve."
            ),
        )
    async with httpx.AsyncClient(timeout=10.0) as cliente:
        r = await cliente.post(f"{SIGNER_URL}/retirada", json={"direccion": direccion})
    if r.status_code != 200:
        detalle = r.json().get("detail", r.text) if r.content else r.text
        raise HTTPException(status_code=r.status_code, detail=detalle)
    _log("cartera_retirada_registrada", direccion=direccion)
    return dict(r.json())


@router.post("/retirar")
async def retirar(todo: bool = True) -> dict[str, Any]:
    """Saca el SOL de la cartera del programa a la tuya.

    Se deja una reserva para las comisiones: vaciar la cuenta hasta el ultimo lamport la deja
    por debajo del minimo de renta y Solana la cierra, con lo que la siguiente compra tendria
    que recrearla y costaria mas de lo que se ahorro.
    """
    firmante = await direccion_firmante()
    if firmante is None:
        raise HTTPException(status_code=503, detail="el firmante no responde")

    async with httpx.AsyncClient(timeout=10.0) as cliente:
        salud = (await cliente.get(f"{SIGNER_URL}/health")).json()
    destino = salud.get("cartera_retirada")
    if not destino:
        raise HTTPException(
            status_code=409,
            detail="no has registrado tu cartera todavia: sin eso no hay adonde retirar",
        )

    async with _abrir_rpc() as rpc:
        # `getBalance` devuelve {"context":..., "value": lamports}. Envolver la llamada en
        # `int()` antes de sacar `value` reventaba con un TypeError, y eso salia como un 500
        # sin explicacion justo cuando la respuesta correcta era «no hay nada que retirar».
        crudo = await rpc.call("getBalance", [firmante])
        saldo = int(crudo.get("value", 0)) if isinstance(crudo, dict) else int(crudo or 0)
        bloque = await rpc.call("getLatestBlockhash", [{"commitment": "finalized"}])

    reserva = 5_000_000  # 0,005 SOL: renta minima mas margen para la comision
    importe = saldo - reserva
    if importe <= 0:
        raise HTTPException(
            status_code=409,
            detail=(
                f"no hay nada que retirar: la cartera tiene {saldo / LAMPORTS_PER_SOL:.6f} SOL "
                f"y se dejan {reserva / LAMPORTS_PER_SOL:.4f} de reserva para comisiones."
            ),
        )

    origen = Pubkey.from_string(firmante)
    mensaje = Message.new_with_blockhash(
        [transfer(TransferParams(from_pubkey=origen, to_pubkey=Pubkey.from_string(destino),
                                 lamports=importe))],
        origen,
        Hash.from_string(bloque["value"]["blockhash"]),
    )
    cruda = base64.b64encode(bytes(Transaction.new_unsigned(mensaje))).decode()

    try:
        firmada = await _firmar(cruda, importe, f"retirada a {destino[:8]}")
    except RuntimeError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    firma = await _enviar(firmada)
    _log("retirada_enviada", destino=destino, importe_sol=importe / LAMPORTS_PER_SOL, firma=firma)
    return {"firma": firma, "destino": destino, "importe_sol": importe / LAMPORTS_PER_SOL}


__all__ = ["arrancar", "direccion_firmante", "parar", "router"]
