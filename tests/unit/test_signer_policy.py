"""Que acepta y que rechaza el firmante.

Estos tests son la red de la unica pieza del sistema que puede gastar dinero sola. Cada uno
describe un ataque o un descuido concreto, no una linea de codigo.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.message import Message
from solders.pubkey import Pubkey
from solders.transaction import Transaction

from mit_signer.policy import (
    PROGRAMAS_PERMITIDOS,
    Contador,
    Limites,
    PolicyError,
    pagador_de,
    programas_de,
    validar,
)

PUMPFUN = Pubkey.from_string("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P")
COMPUTE = Pubkey.from_string("ComputeBudget111111111111111111111111111111")
ATA = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
SYSTEM = Pubkey.from_string("11111111111111111111111111111111")
SPL_TOKEN = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")

LAMPORTS = 1_000_000_000


def _tx(pagador: Pubkey, programas: list[Pubkey]) -> Transaction:
    """Una transaccion con la forma justa: importa quien paga y que programas invoca."""
    instrucciones = [
        Instruction(
            program_id=p,
            data=b"\x00",
            accounts=[AccountMeta(pubkey=pagador, is_signer=True, is_writable=True)],
        )
        for p in programas
    ]
    mensaje = Message.new_with_blockhash(instrucciones, pagador, Hash.default())
    return Transaction.new_unsigned(mensaje)


@pytest.fixture
def limites() -> Limites:
    return Limites(max_por_orden=int(0.05 * LAMPORTS), max_diario=int(0.2 * LAMPORTS))


@pytest.fixture
def cartera() -> Keypair:
    return Keypair()


# --- Lo que SI se firma ----------------------------------------------------


def test_una_compra_normal_pasa(cartera: Keypair, limites: Limites) -> None:
    """La forma exacta de una compra real: ComputeBudget x2, ATA y Pump.fun."""
    tx = _tx(cartera.pubkey(), [COMPUTE, COMPUTE, ATA, PUMPFUN])
    validar(
        tx,
        cartera=cartera.pubkey(),
        importe_lamports=int(0.01 * LAMPORTS),
        limites=limites,
        contador=Contador(),
    )


def test_una_venta_normal_pasa(cartera: Keypair, limites: Limites) -> None:
    """La forma exacta de una venta real: ComputeBudget x2 y Pump.fun."""
    tx = _tx(cartera.pubkey(), [COMPUTE, COMPUTE, PUMPFUN])
    validar(
        tx,
        cartera=cartera.pubkey(),
        importe_lamports=0,
        limites=limites,
        contador=Contador(),
    )


# --- Lo que NO se firma ----------------------------------------------------


def test_no_firma_una_transferencia_de_sol(cartera: Keypair, limites: Limites) -> None:
    """El ataque que mas importa: sacar el saldo a otra cartera.

    Una transferencia de SOL es una instruccion del programa System. Mientras System estuvo en
    la lista blanca, esto se firmaba: bastaba con pedirlo por debajo del tope por orden.
    """
    tx = _tx(cartera.pubkey(), [SYSTEM])
    with pytest.raises(PolicyError, match="ninguna cartera de retirada"):
        validar(
            tx,
            cartera=cartera.pubkey(),
            importe_lamports=1,
            limites=limites,
            contador=Contador(),
        )


def test_no_firma_una_transferencia_de_tokens(cartera: Keypair, limites: Limites) -> None:
    """La misma fuga, con los tokens comprados en vez de con el SOL."""
    tx = _tx(cartera.pubkey(), [SPL_TOKEN])
    with pytest.raises(PolicyError, match="programas no permitidos"):
        validar(
            tx,
            cartera=cartera.pubkey(),
            importe_lamports=0,
            limites=limites,
            contador=Contador(),
        )


def test_no_firma_si_se_cuela_un_programa_extra(cartera: Keypair, limites: Limites) -> None:
    """Una compra legitima CON una instruccion de mas escondida detras."""
    tx = _tx(cartera.pubkey(), [COMPUTE, ATA, PUMPFUN, SYSTEM])
    with pytest.raises(PolicyError, match="ninguna cartera de retirada"):
        validar(
            tx,
            cartera=cartera.pubkey(),
            importe_lamports=int(0.01 * LAMPORTS),
            limites=limites,
            contador=Contador(),
        )


def test_no_firma_por_otro_pagador(cartera: Keypair, limites: Limites) -> None:
    """Si el pagador no es esta cartera, firmar no tiene sentido y es senal de algo raro."""
    ajena = Keypair().pubkey()
    tx = _tx(ajena, [COMPUTE, PUMPFUN])
    with pytest.raises(PolicyError, match="no es esta cartera"):
        validar(
            tx,
            cartera=cartera.pubkey(),
            importe_lamports=0,
            limites=limites,
            contador=Contador(),
        )


def test_tope_por_orden(cartera: Keypair, limites: Limites) -> None:
    """Un cero de mas en el importe no puede convertirse en una firma."""
    tx = _tx(cartera.pubkey(), [COMPUTE, PUMPFUN])
    with pytest.raises(PolicyError, match="tope por orden"):
        validar(
            tx,
            cartera=cartera.pubkey(),
            importe_lamports=int(0.5 * LAMPORTS),
            limites=limites,
            contador=Contador(),
        )


def test_tope_diario_corta_la_racha(cartera: Keypair, limites: Limites) -> None:
    """Cuatro ordenes de 0,05 caben en el tope de 0,2. La quinta no."""
    tx = _tx(cartera.pubkey(), [COMPUTE, PUMPFUN])
    contador = Contador()
    orden = int(0.05 * LAMPORTS)
    for _ in range(4):
        validar(
            tx,
            cartera=cartera.pubkey(),
            importe_lamports=orden,
            limites=limites,
            contador=contador,
        )
        contador.anotar(orden)
    with pytest.raises(PolicyError, match="tope diario"):
        validar(
            tx,
            cartera=cartera.pubkey(),
            importe_lamports=orden,
            limites=limites,
            contador=contador,
        )


def test_importe_negativo(cartera: Keypair, limites: Limites) -> None:
    tx = _tx(cartera.pubkey(), [COMPUTE, PUMPFUN])
    with pytest.raises(PolicyError, match="negativo"):
        validar(
            tx,
            cartera=cartera.pubkey(),
            importe_lamports=-1,
            limites=limites,
            contador=Contador(),
        )


# --- El contador que sobrevive al reinicio ---------------------------------


def test_el_tope_diario_sobrevive_a_cerrar_el_programa(tmp_path) -> None:
    """El agujero que tenia el contador en memoria.

    Cerrar y volver a abrir el programa reiniciaba lo gastado, asi que el tope diario se
    saltaba con dos clics —y justo por el camino que toma quien va perdiendo—.
    """
    ruta = tmp_path / "gastado_hoy.json"
    primero = Contador(ruta=ruta)
    primero.anotar(int(0.15 * LAMPORTS))

    # El programa se cierra y se vuelve a abrir: proceso nuevo, contador nuevo.
    segundo = Contador(ruta=ruta)
    assert segundo.gastado == int(0.15 * LAMPORTS)
    assert segundo.disponible(int(0.2 * LAMPORTS)) == int(0.05 * LAMPORTS)


def test_el_contador_se_reinicia_al_cambiar_el_dia(tmp_path) -> None:
    ruta = tmp_path / "gastado_hoy.json"
    viejo = Contador(ruta=ruta)
    viejo.anotar(int(0.2 * LAMPORTS))
    # Se reescribe con fecha de ayer, como si el fichero llevara ahi desde entonces.
    viejo.dia = datetime.now(UTC).date() - timedelta(days=1)
    viejo._guardar()

    hoy = Contador(ruta=ruta)
    assert hoy.disponible(int(0.2 * LAMPORTS)) == int(0.2 * LAMPORTS)
    assert hoy.dia == datetime.now(UTC).date()


def test_contador_corrupto_no_revienta(tmp_path) -> None:
    """Un fichero truncado por un corte no puede dejar el firmante sin arrancar."""
    ruta = tmp_path / "gastado_hoy.json"
    ruta.write_text('{"dia": "no-es-una-fecha", "gast', encoding="utf-8")
    contador = Contador(ruta=ruta)
    assert contador.gastado == 0
    assert contador.dia == datetime.now(UTC).date()


def test_la_escritura_es_atomica(tmp_path) -> None:
    """No debe quedar ningun temporal suelto: si queda, es que el reemplazo no ocurrio."""
    ruta = tmp_path / "gastado_hoy.json"
    contador = Contador(ruta=ruta)
    contador.anotar(1_000)
    assert ruta.exists()
    assert list(tmp_path.glob("*.tmp")) == []


# --- La lista blanca en si -------------------------------------------------


def test_la_lista_blanca_no_deja_mover_saldo() -> None:
    """Ningun programa capaz de mandar saldo a una direccion cualquiera esta permitido.

    Este test es el que hay que mirar si algun dia se anade algo a la lista: el criterio no es
    «hace falta para que funcione», es «que puede hacer con nuestra firma».
    """
    for capaz_de_transferir in (SYSTEM, SPL_TOKEN,
                                Pubkey.from_string("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb")):
        assert str(capaz_de_transferir) not in PROGRAMAS_PERMITIDOS


def test_programas_y_pagador_se_leen_de_la_transaccion(cartera: Keypair) -> None:
    """La politica lee la transaccion, no lo que diga quien la manda."""
    tx = _tx(cartera.pubkey(), [COMPUTE, PUMPFUN, ATA])
    assert programas_de(tx) == {str(COMPUTE), str(PUMPFUN), str(ATA)}
    assert pagador_de(tx) == str(cartera.pubkey())


# --- La puerta de retirada -------------------------------------------------
#
# Es la unica via por la que sale SOL de la cartera del programa. Cada test de aqui abajo es un
# intento de colar algo por ella.

from solders.system_program import TransferParams, transfer  # noqa: E402

from mit_signer.policy import RetiradaNoPermitida  # noqa: E402


def _retirada(origen: Pubkey, destino: Pubkey, lamports: int = 10_000_000) -> Transaction:
    ix = transfer(TransferParams(from_pubkey=origen, to_pubkey=destino, lamports=lamports))
    mensaje = Message.new_with_blockhash([ix], origen, Hash.default())
    return Transaction.new_unsigned(mensaje)


def test_retirada_a_la_cartera_registrada_pasa(cartera: Keypair, limites: Limites) -> None:
    mia = Keypair().pubkey()
    tx = _retirada(cartera.pubkey(), mia)
    validar(
        tx,
        cartera=cartera.pubkey(),
        importe_lamports=10_000_000,
        limites=limites,
        contador=Contador(),
        retirada=str(mia),
    )


def test_sin_cartera_registrada_no_hay_retirada(cartera: Keypair, limites: Limites) -> None:
    """El estado de partida: hasta que no registras tu cartera, no sale un SOL."""
    tx = _retirada(cartera.pubkey(), Keypair().pubkey())
    with pytest.raises(RetiradaNoPermitida, match="ninguna cartera de retirada"):
        validar(
            tx,
            cartera=cartera.pubkey(),
            importe_lamports=10_000_000,
            limites=limites,
            contador=Contador(),
            retirada=None,
        )


def test_no_se_puede_retirar_a_otra_direccion(cartera: Keypair, limites: Limites) -> None:
    """EL ataque que esto para: pedir la retirada, pero eligiendo otro destino."""
    mia = Keypair().pubkey()
    del_atacante = Keypair().pubkey()
    tx = _retirada(cartera.pubkey(), del_atacante)
    with pytest.raises(RetiradaNoPermitida, match="no va a la cartera de retirada"):
        validar(
            tx,
            cartera=cartera.pubkey(),
            importe_lamports=10_000_000,
            limites=limites,
            contador=Contador(),
            retirada=str(mia),
        )


def test_no_se_puede_colar_una_compra_dentro_de_una_retirada(
    cartera: Keypair, limites: Limites
) -> None:
    """Mezclar las dos cosas en una firma dejaria pasar lo que no se mira."""
    mia = Keypair().pubkey()
    ix_transfer = transfer(
        TransferParams(from_pubkey=cartera.pubkey(), to_pubkey=mia, lamports=1_000)
    )
    ix_otro = Instruction(
        program_id=SPL_TOKEN,
        data=b"\x03",
        accounts=[AccountMeta(pubkey=cartera.pubkey(), is_signer=True, is_writable=True)],
    )
    mensaje = Message.new_with_blockhash(
        [ix_transfer, ix_otro], cartera.pubkey(), Hash.default()
    )
    with pytest.raises(PolicyError):
        validar(
            Transaction.new_unsigned(mensaje),
            cartera=cartera.pubkey(),
            importe_lamports=1_000,
            limites=limites,
            contador=Contador(),
            retirada=str(mia),
        )


def test_los_topes_siguen_frenando_lo_que_si_puede_perder(
    cartera: Keypair, limites: Limites
) -> None:
    """La exencion es SOLO para retiradas. Una compra por encima del tope sigue rechazada.

    Este test existe para que la exencion no se convierta con el tiempo en un agujero: si
    alguien ensancha `_es_retirada_valida` de mas, esto se cae.
    """
    mia = Keypair().pubkey()
    tx = _tx(cartera.pubkey(), [COMPUTE, PUMPFUN])
    with pytest.raises(PolicyError, match="tope por orden"):
        validar(
            tx,
            cartera=cartera.pubkey(),
            importe_lamports=int(0.5 * LAMPORTS),
            limites=limites,
            contador=Contador(),
            retirada=str(mia),
        )


def test_otra_instruccion_de_system_no_es_una_retirada(
    cartera: Keypair, limites: Limites
) -> None:
    """System hace mas cosas que transferir. Solo `Transfer` cuenta como retirada."""
    mia = Keypair().pubkey()
    ix = Instruction(
        program_id=SYSTEM,
        data=(8).to_bytes(4, "little"),  # Allocate, no Transfer
        accounts=[
            AccountMeta(pubkey=cartera.pubkey(), is_signer=True, is_writable=True),
            AccountMeta(pubkey=mia, is_signer=False, is_writable=True),
        ],
    )
    mensaje = Message.new_with_blockhash([ix], cartera.pubkey(), Hash.default())
    with pytest.raises(RetiradaNoPermitida):
        validar(
            Transaction.new_unsigned(mensaje),
            cartera=cartera.pubkey(),
            importe_lamports=0,
            limites=limites,
            contador=Contador(),
            retirada=str(mia),
        )


def test_una_retirada_grande_no_choca_con_los_topes(cartera: Keypair, limites: Limites) -> None:
    """Sacar TU dinero no puede depender de un tope pensado para frenar perdidas.

    El tope por orden son 0,05 SOL. Se retira 1 SOL —veinte veces mas— y tiene que pasar:
    el destino esta fijado a la cartera registrada, asi que no hay dano que acotar.
    """
    mia = Keypair().pubkey()
    tx = _retirada(cartera.pubkey(), mia, lamports=1 * LAMPORTS)
    validar(
        tx,
        cartera=cartera.pubkey(),
        importe_lamports=1 * LAMPORTS,
        limites=limites,
        contador=Contador(),
        retirada=str(mia),
    )


def test_retirar_no_consume_el_tope_diario_de_operar(cartera: Keypair, limites: Limites) -> None:
    """Retirar no puede dejarte sin poder comprar el resto del dia."""
    mia = Keypair().pubkey()
    contador = Contador()
    validar(_retirada(cartera.pubkey(), mia, lamports=1 * LAMPORTS),
            cartera=cartera.pubkey(), importe_lamports=1 * LAMPORTS,
            limites=limites, contador=contador, retirada=str(mia))
    assert contador.gastado == 0
    # Y la compra siguiente sigue teniendo el dia entero disponible.
    validar(_tx(cartera.pubkey(), [COMPUTE, PUMPFUN]),
            cartera=cartera.pubkey(), importe_lamports=int(0.05 * LAMPORTS),
            limites=limites, contador=contador, retirada=str(mia))
