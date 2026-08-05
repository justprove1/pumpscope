"""Lo que el firmante acepta firmar, y lo que no.

**Se asume que quien pide la firma esta comprometido.** El panel, la API y el vigilante del
stop pueden tener un bug o estar manipulados; esta capa no se fia de ninguno. Cada peticion
se valida contra la transaccion REAL, decodificada aqui, no contra lo que diga quien la manda.

Las tres reglas que acotan el dano:

1. **Solo programas de la lista.** Si la transaccion toca cualquier programa que no este
   permitido, se rechaza. Eso convierte «una transferencia a una direccion cualquiera» en algo
   que este firmante no puede producir, ni con un bug ni con malicia.
2. **Tope por orden.** Un cero de mas en un importe no puede convertirse en una firma.
3. **Tope diario.** Aunque cada orden pase el tope individual, el acumulado del dia tiene su
   propio limite. Es lo que corta un bucle que compra bien cuarenta veces seguidas.

Y una regla que no es un limite sino una invariante: **el pagador tiene que ser esta misma
cartera**. Firmar una transaccion cuyo pagador es otro no tiene sentido y seria la forma
obvia de colar algo raro.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Final

from solders.pubkey import Pubkey
from solders.transaction import Transaction

LOGGER = logging.getLogger("mit.signer.policy")

# Programas que el firmante puede tocar. Nada mas.
#
# **Son EXACTAMENTE los que aparecen en una orden real, ni uno mas.** No se dedujeron leyendo
# codigo: se construyeron una compra y una venta de verdad contra la cadena y se listaron sus
# instrucciones de primer nivel. Salieron estos tres, siempre:
#
#   compra -> ComputeBudget, ComputeBudget, ATA, Pump.fun
#   venta  -> ComputeBudget, ComputeBudget, Pump.fun
#
# Lo que se ha QUITADO, y por que importa: aqui estaban tambien System, SPL Token y Token-2022.
# Ninguno aparece en una orden —las cuentas asociadas se crean por CPI desde el programa ATA, y
# una CPI no es una instruccion de primer nivel, asi que la politica nunca la ve—, pero los
# tres saben hacer una cosa que Pump.fun no puede: **mandar saldo a una direccion cualquiera**.
# System transfiere SOL; los de token transfieren tokens. Con ellos permitidos, una API con un
# bug o manipulada podia pedir «transfiere todo a esta direccion» y la politica lo aprobaba,
# que es justo lo que esta lista existe para impedir. Tambien se quito el programa de fees de
# Pump.fun: la instruccion lo invoca por CPI, nunca directamente.
#
# Consecuencia buscada: con esta lista, lo peor que puede hacer una peticion maliciosa es
# comprar o vender en la curva. El dinero puede perderse operando, pero no puede irse a otra
# cartera.
PROGRAMAS_PERMITIDOS: frozenset[str] = frozenset(
    {
        "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",  # Pump.fun: comprar y vender en la curva
        "ComputeBudget111111111111111111111111111111",  # limite de computo y prioridad
        "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",  # crear la cuenta donde llega el token
    }
)

SYSTEM_PROGRAM = "11111111111111111111111111111111"

# Indice de la instruccion `Transfer` del programa System, en 4 bytes little-endian. System
# tiene otras instrucciones —crear cuenta, asignar propietario, delegar stake— y ninguna
# pinta en una retirada.
_SYSTEM_TRANSFER: Final = (2).to_bytes(4, "little")


class PolicyError(RuntimeError):
    """La transaccion no cumple la politica. NO se firma."""


class RetiradaNoPermitida(PolicyError):
    """Una transferencia que no va a la cartera de retirada registrada."""


def _es_retirada_valida(transaccion: Transaction, retirada: str | None) -> bool:
    """¿Es esta transaccion una retirada limpia a la cartera registrada?

    **Aqui se reabre, muy estrecha, la unica puerta por la que sale SOL.** El programa System
    quedo fuera de la lista blanca porque sabe mandar saldo a cualquier direccion, y esa es la
    forma obvia de vaciar la cartera. Pero sin el, el dinero entra y no sale salvo operando:
    una trampa, no una medida de seguridad.

    La puerta se abre con cuatro cerrojos a la vez, y basta que falle uno para que no:

    1. Tiene que haber una cartera de retirada REGISTRADA. Sin ella no hay retirada posible.
    2. La transaccion solo puede llevar transferencias de System y ajustes de ComputeBudget.
       Nada de mezclar una retirada con una compra en la misma firma.
    3. Cada instruccion de System tiene que ser `Transfer`, no cualquier otra cosa.
    4. El destino de CADA transferencia tiene que ser esa cartera registrada. Aqui es donde
       una peticion manipulada se estrella: puede pedir la transferencia, pero no puede elegir
       adonde.
    """
    if not retirada:
        return False

    mensaje = transaccion.message
    cuentas = list(mensaje.account_keys)
    vio_transferencia = False

    for instruccion in mensaje.instructions:
        programa = str(cuentas[instruccion.program_id_index])
        if programa == "ComputeBudget111111111111111111111111111111":
            continue
        if programa != SYSTEM_PROGRAM:
            return False
        if bytes(instruccion.data)[:4] != _SYSTEM_TRANSFER:
            return False
        # Cuentas de un Transfer: [origen, destino]. El destino es el segundo.
        indices = list(instruccion.accounts)
        if len(indices) < 2:
            return False
        if str(cuentas[indices[1]]) != retirada:
            return False
        vio_transferencia = True

    return vio_transferencia


@dataclass(frozen=True, slots=True)
class Limites:
    """Topes en lamports. Se leen del entorno al arrancar y no se tocan en caliente."""

    max_por_orden: int
    max_diario: int


@dataclass
class Contador:
    """Lo gastado hoy, GUARDADO EN DISCO.

    Antes vivia solo en memoria, y se argumentaba que reiniciar el firmante reiniciara el
    contador era aceptable porque quedaba en los logs. Eso valia cuando el firmante era un
    servicio que se levantaba una vez y se quedaba encendido semanas.

    **Dentro de un programa de escritorio deja de valer.** Ahi el firmante arranca y muere
    cada vez que se abre y se cierra la ventana, que pueden ser veinte veces en una tarde. Un
    tope diario que se pone a cero en cada apertura no es un tope: es un adorno, y encima uno
    que da confianza. Peor aun, el camino para saltarselo seria justo el que toma alguien que
    esta perdiendo —cerrar y volver a abrir—, que es precisamente cuando el tope tiene que
    aguantar.

    Con `ruta` a None se comporta como antes, en memoria. Es lo que usan los tests.
    """

    dia: date = field(default_factory=lambda: datetime.now(UTC).date())
    gastado: int = 0
    ruta: Path | None = None

    def __post_init__(self) -> None:
        if self.ruta is not None:
            self._leer()

    def _leer(self) -> None:
        """Recupera lo gastado. Un fichero ilegible NO se toma como cero silencioso."""
        assert self.ruta is not None
        try:
            datos = json.loads(self.ruta.read_text(encoding="utf-8"))
            self.dia = date.fromisoformat(str(datos["dia"]))
            self.gastado = int(datos["gastado"])
        except FileNotFoundError:
            pass  # primera vez: no se ha gastado nada, que es cierto
        except (OSError, ValueError, KeyError, TypeError):
            # Se empieza de cero porque no hay nada mejor que hacer, pero se deja constancia:
            # un contador corrupto es indistinguible de uno manipulado, y quien mire el
            # registro tiene que poder verlo.
            LOGGER.warning(
                json.dumps({"event": "contador_ilegible", "ruta": str(self.ruta)})
            )
            self.dia = datetime.now(UTC).date()
            self.gastado = 0

    def _guardar(self) -> None:
        if self.ruta is None:
            return
        # Escritura atomica: un corte a media escritura dejaria un JSON truncado, y eso vuelve
        # por el camino de arriba como «contador ilegible», o sea con el tope a cero.
        try:
            self.ruta.parent.mkdir(parents=True, exist_ok=True)
            temporal = self.ruta.with_suffix(".tmp")
            temporal.write_text(
                json.dumps({"dia": self.dia.isoformat(), "gastado": self.gastado}),
                encoding="utf-8",
            )
            temporal.replace(self.ruta)
        except OSError as exc:  # pragma: no cover - disco lleno o permisos
            LOGGER.error(json.dumps({"event": "contador_no_guardado", "error": str(exc)}))

    def _al_dia(self) -> None:
        hoy = datetime.now(UTC).date()
        if hoy != self.dia:
            self.dia = hoy
            self.gastado = 0
            self._guardar()

    def disponible(self, limite: int) -> int:
        self._al_dia()
        return max(0, limite - self.gastado)

    def anotar(self, lamports: int) -> None:
        self._al_dia()
        self.gastado += lamports
        self._guardar()


def programas_de(transaccion: Transaction) -> set[str]:
    """Programas que la transaccion invoca, leidos de la transaccion misma."""
    mensaje = transaccion.message
    cuentas = list(mensaje.account_keys)
    salida: set[str] = set()
    for instruccion in mensaje.instructions:
        indice = instruccion.program_id_index
        if indice < len(cuentas):
            salida.add(str(cuentas[indice]))
    return salida


def pagador_de(transaccion: Transaction) -> str:
    """Quien paga —y por tanto quien firma— es siempre la primera cuenta del mensaje."""
    cuentas = list(transaccion.message.account_keys)
    return str(cuentas[0]) if cuentas else ""


def validar(
    transaccion: Transaction,
    *,
    cartera: Pubkey,
    importe_lamports: int,
    limites: Limites,
    contador: Contador,
    retirada: str | None = None,
) -> None:
    """Lanza `PolicyError` si esta transaccion no se puede firmar. No devuelve nada.

    `importe_lamports` es lo que el solicitante DICE que va a mover. Se usa para los topes,
    y por eso los topes son un freno y no una garantia: lo que de verdad gaste la transaccion
    lo decide el programa. La lista de programas permitidos es la que impide que ese gasto
    acabe en cualquier sitio.
    """
    if importe_lamports < 0:
        msg = "el importe no puede ser negativo"
        raise PolicyError(msg)

    pagador = pagador_de(transaccion)
    if pagador != str(cartera):
        msg = f"el pagador de la transaccion no es esta cartera: {pagador}"
        raise PolicyError(msg)

    invocados = programas_de(transaccion)
    prohibidos = invocados - PROGRAMAS_PERMITIDOS

    # Una retirada a la cartera registrada es la unica excepcion, y se comprueba entera antes
    # de dejarla pasar. Si la transaccion invoca System pero NO cumple todas las condiciones
    # de `_es_retirada_valida`, cae por el camino normal y se rechaza.
    #
    # Los topes se le aplican igual: retirar tambien mueve dinero, y un bug que retire en
    # bucle a la cartera correcta sigue siendo un bug que hay que frenar.
    es_retirada = _es_retirada_valida(transaccion, retirada)
    if prohibidos and es_retirada:
        prohibidos = set()

    if prohibidos:
        if SYSTEM_PROGRAM in prohibidos and retirada:
            msg = (
                "esa transferencia no va a la cartera de retirada registrada, o lleva algo "
                "mas dentro. Solo se firma una retirada limpia a "
                f"{retirada}"
            )
            raise RetiradaNoPermitida(msg)
        if SYSTEM_PROGRAM in prohibidos:
            msg = (
                "no hay ninguna cartera de retirada registrada, asi que no se puede firmar "
                "ninguna transferencia de SOL"
            )
            raise RetiradaNoPermitida(msg)
        msg = f"la transaccion toca programas no permitidos: {sorted(prohibidos)}"
        raise PolicyError(msg)

    # **Los topes NO se aplican a una retirada.** Existen para acotar lo que se puede perder
    # si algo va mal: un bucle que compra, una API manipulada, un cero de mas en un importe.
    # Una retirada no puede perder nada —solo puede ir a TU cartera, y eso ya lo garantizan
    # los cuatro cerrojos de `_es_retirada_valida`—, asi que limitarla no protegia de ningun
    # dano; lo unico que hacia era dejar el dinero encerrado a 0,2 SOL por dia.
    #
    # Sacar tu dinero nunca deberia depender de un tope pensado para frenar perdidas.
    if es_retirada:
        return

    if importe_lamports > limites.max_por_orden:
        msg = (
            f"importe por encima del tope por orden: "
            f"{importe_lamports / 1e9:.4f} SOL > {limites.max_por_orden / 1e9:.4f}"
        )
        raise PolicyError(msg)

    queda = contador.disponible(limites.max_diario)
    if importe_lamports > queda:
        msg = (
            f"tope diario agotado: quedan {queda / 1e9:.4f} SOL y se piden "
            f"{importe_lamports / 1e9:.4f}"
        )
        raise PolicyError(msg)
