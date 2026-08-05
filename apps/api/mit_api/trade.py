"""Preparacion de operaciones de compra y venta en Pump.fun.

**Aqui no se firma nada y no hay ninguna clave.** Este modulo construye una transaccion SIN
FIRMAR y la devuelve al panel; quien firma es la cartera del navegador del usuario, que le
muestra la operacion y espera su aprobacion. El backend nunca ve material criptografico, y
por eso no hace falta el `signer` aislado ni tocar `ENABLE_LIVE_TRADING`: eso gobierna el
trading AUTOMATICO, en el que decide el sistema. Aqui decide y aprueba una persona.

Todos los importes se calculan contra el estado de la curva leido en el momento, no contra
la base de datos: entre lo que el worker persistio y ahora puede haber pasado cualquier cosa.
"""

from __future__ import annotations

import base64
import logging
import os
import time
from collections.abc import Callable
from typing import Any, Final, Literal

import based58
import httpx
from fastapi import APIRouter, HTTPException
from mit_pumpfun.trade import (
    BondingCurveAccount,
    TradeAccounts,
    TradeError,
    apply_slippage_down,
    apply_slippage_up,
    associated_token_address,
    bonding_curve_pda,
    build_buy_instruction,
    build_create_ata_idempotent_instruction,
    build_sell_instruction,
    decode_global_buyback_recipients,
    decode_global_fee_bps,
    decode_global_fee_recipient,
    decode_global_reserved_fee_recipient,
    global_pda,
    lamports_out_for_tokens,
    net_lamports_after_fee,
    tokens_out_for_lamports,
    user_volume_accumulator_pda,
)
from mit_solana.rpc import RpcError, RpcLimits, RpcRateLimitedError, SolanaRpc
from pydantic import BaseModel, Field
from solders.hash import Hash
from solders.instruction import Instruction
from solders.message import Message
from solders.pubkey import Pubkey
from solders.transaction import Transaction

LOGGER = logging.getLogger("mit.trade")
router = APIRouter(prefix="/v1/trade", tags=["trade"])

LAMPORTS_PER_SOL: Final = 1_000_000_000

COMPUTE_BUDGET_PROGRAM: Final = Pubkey.from_string("ComputeBudget111111111111111111111111111111")

# **La prioridad se paga sobre el limite PEDIDO, no sobre el consumido.** Pedir de mas no es
# gratis: son lamports tirados en cada operacion. Medido en mainnet, una compra completa
# —crear la cuenta de token, crear el acumulador de volumen y la instruccion `buy`— se queda
# bastante por debajo de este numero; el margen que sobra es el colchon para que una cuenta
# que haya que crear no haga revertir la orden por quedarse corta de computo.
# Con 250_000 se pagaba 0,000125 SOL por operacion: un 1,3% sobre una orden de 0,005 SOL.
COMPUTE_UNIT_LIMIT: Final = 150_000

# Topes de cordura. No son una politica de riesgo, son un freno a un dedo torpe: un cero de
# mas en el importe no debe convertirse en una transaccion enviada.
MAX_ORDER_SOL: Final = float(os.environ.get("PANEL_MAX_ORDER_SOL", "2.0"))
MAX_SLIPPAGE_BPS: Final = 5_000

# Coste fijo de mandar una transaccion: la comision base de Solana mas la prioridad, que se
# paga sobre el limite de computo pedido.
_COMISION_BASE_LAMPORTS: Final = 5_000

# El programa devuelve este error cuando se piden mas tokens de los que quedan en la cuenta.
_ERROR_TOKENS_INSUFICIENTES: Final = 6023
# Y este cuando el precio subio por encima del tope de gasto mientras se preparaba la orden.
_ERROR_PRECIO_SUBIO: Final = 6002

# Traduccion de lo que responde la cadena. El mensaje crudo de Anchor —«AnchorError thrown in
# programs/pump/src/lib.rs:444»— es correcto y no le dice nada a quien lo lee: no explica que
# ha pasado ni que hacer. Lo que sigue si.
_EXPLICACIONES: Final[dict[int, str]] = {
    6002: (
        "el precio subio mas de lo que permite tu slippage mientras se preparaba la orden. "
        "En un token con mucha presion compradora pasa: sube el slippage o reintenta"
    ),
    6003: (
        "la venta daria menos SOL de lo que permite tu slippage: el precio bajo mientras se "
        "preparaba. Sube el slippage o reintenta"
    ),
    6005: "este token ya graduo: la curva se completo y ahora se opera en PumpSwap",
    6023: "no quedan tantos tokens en la cartera: el saldo cambio mientras se preparaba",
    6000: "esa cuenta no esta autorizada para esta operacion en este token",
    6062: "falta el destinatario de recompra que el programa exige",
    6074: "el programa rechaza una de las cuentas de la operacion",
    1: "no hay SOL suficiente en la cartera para cubrir la orden y las comisiones",
}


class RechazoCadena(HTTPException):
    """La cadena rechazo la operacion. Lleva el codigo del programa para poder reaccionar.

    Hace falta porque el mensaje que ve el usuario esta traducido a lenguaje llano, y de un
    texto traducido ya no se puede sacar que error era para decidir si reintentar.
    """

    def __init__(self, detail: str, codigo: int | None) -> None:
        super().__init__(status_code=409, detail=detail)
        self.codigo = codigo


def _codigo_de(err: object) -> int | None:
    """Codigo `Custom` que devuelve el programa, si lo hay."""
    if isinstance(err, dict):
        detalle = err.get("InstructionError")
        if isinstance(detalle, list) and len(detalle) == 2 and isinstance(detalle[1], dict):
            valor = detalle[1].get("Custom")
            if isinstance(valor, int):
                return valor
    return None


async def curva_fresca(rpc: SolanaRpc, mint: Pubkey) -> BondingCurveAccount | None:
    """Relee la curva SIN pasar por la cache, sobre la conexion ya abierta.

    Se usa al reintentar: si el precio se movio, volver a leer lo que ya teniamos guardado
    daria el mismo resultado y el reintento no serviria de nada.
    """
    try:
        cuenta = await _fetch_account(rpc, str(bonding_curve_pda(mint)))
    except (RpcError, RpcRateLimitedError):
        return None
    if cuenta is None:
        return None
    try:
        return BondingCurveAccount.decode(cuenta[0])
    except TradeError:
        return None


def _explicar(err: object, logs: list[str]) -> str:
    """Traduce el rechazo de la cadena a algo accionable, sin perder el detalle tecnico."""
    codigo = _codigo_de(err)
    if codigo is not None and codigo in _EXPLICACIONES:
        return _EXPLICACIONES[codigo]
    for linea in reversed(logs):
        if "Error Message:" in linea:
            return linea.split("Error Message:", 1)[1].strip().rstrip(".")
        if "insufficient" in linea.lower():
            return linea.strip()
    return str(err)

# Alquiler de la cuenta de token (~0,00204 SOL) mas las comisiones de cadena. Se reserva
# aparte del importe: si no, una compra que gasta hasta el ultimo lamport revierte al no
# quedar con que crear la cuenta donde recibir los tokens.
_MARGEN_CUENTA_Y_RED: Final = 2_500_000


# **Una sola conexion, reutilizada.** Abrir un cliente nuevo por operacion rehacia el saludo
# TLS cada vez: medido, 94 ms tirados en cada compra. Con la conexion abierta, dos llamadas
# pasan de 209 ms a 113.
_cliente_http: httpx.AsyncClient | None = None

# Con la conexion ya abierta, el estrangulador pasa a ser el freno: a 8/s las mismas dos
# llamadas cuestan 168 ms y a 25/s, 91. Por encima de 25 ya no se gana nada (87 ms a 1000/s),
# asi que se queda ahi: lo suficiente para no esperar, sin buscarle las cosquillas al RPC
# publico. Es espaciado DENTRO de una operacion; el ritmo sostenido lo marca el usuario.
_RPS_OPERATIVA: Final = 25.0


def _cliente() -> httpx.AsyncClient:
    """Cliente HTTP compartido, con las conexiones vivas entre operaciones."""
    global _cliente_http
    if _cliente_http is None or _cliente_http.is_closed:
        _cliente_http = httpx.AsyncClient(
            timeout=30.0,
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
        )
    return _cliente_http


async def cerrar_cliente() -> None:
    """Cierra la conexion compartida al apagar la API."""
    global _cliente_http
    if _cliente_http is not None and not _cliente_http.is_closed:
        await _cliente_http.aclose()
    _cliente_http = None


def _abrir_rpc() -> SolanaRpc:
    """Cliente RPC para el camino de operativa: conexion compartida y sin esperas de mas."""
    return SolanaRpc(
        url=_rpc_url(),
        limits=RpcLimits(requests_per_second=_RPS_OPERATIVA),
        client=_cliente(),
    )


def _rpc_url() -> str:
    """Helius si hay credencial; si no, el RPC publico."""
    helius = os.environ.get("HELIUS_RPC_URL", "")
    if helius and os.environ.get("HELIUS_API_KEY"):
        return helius
    return os.environ.get("SOLANA_FALLBACK_RPC_URL", "https://api.mainnet-beta.solana.com")


def _compute_budget_instructions(priority_fee_microlamports: int) -> list[Instruction]:
    """Limite de computo y precio por unidad. El segundo es lo que decide si la transaccion
    entra rapido o se queda mirando: en un token que se mueve, llegar tarde es no llegar."""
    return [
        Instruction(
            program_id=COMPUTE_BUDGET_PROGRAM,
            data=bytes([2]) + COMPUTE_UNIT_LIMIT.to_bytes(4, "little"),
            accounts=[],
        ),
        Instruction(
            program_id=COMPUTE_BUDGET_PROGRAM,
            data=bytes([3]) + priority_fee_microlamports.to_bytes(8, "little"),
            accounts=[],
        ),
    ]


class PrepareRequest(BaseModel):
    mint: str
    user: str = Field(description="Cartera que firmara. Debe ser la conectada en el panel.")
    side: Literal["buy", "sell"]
    # Compra: cuanto SOL meter. Venta: que porcentaje de lo que se tiene vender.
    amount_sol: float | None = None
    sell_percent: float | None = Field(default=None, ge=0.01, le=100)
    slippage_bps: int = Field(default=1_000, ge=1, le=MAX_SLIPPAGE_BPS)
    priority_fee_microlamports: int = Field(default=500_000, ge=0, le=50_000_000)


class PrepareResponse(BaseModel):
    # Transaccion completa sin firmar, para carteras que aceptan bytes crudos.
    transaction_base64: str
    # La TRANSACCION en base58: es lo que consume `signAndSendTransaction` de Phantom.
    # Se da hecho para que el panel no tenga que cargar una libreria de Solana solo para
    # serializar.
    transaction_base58: str
    # El MENSAJE en base58. Algunas versiones de cartera esperan esto en vez de la
    # transaccion entera; se envia tambien para que el panel pueda reintentar con el otro
    # formato si el primero no se deserializa.
    message_base58: str
    summary: dict[str, Any]


async def _fetch_account(rpc: SolanaRpc, address: str) -> tuple[bytes, str] | None:
    """Datos y programa propietario de una cuenta."""
    result = await rpc.call("getAccountInfo", [address, {"encoding": "base64"}])
    if not result or not result.get("value"):
        return None
    value = result["value"]
    return base64.b64decode(value["data"][0]), value["owner"]


async def _fetch_accounts(
    rpc: SolanaRpc, addresses: list[str]
) -> list[tuple[bytes, str, int] | None]:
    """Varias cuentas en UNA sola llamada.

    Cada consulta al RPC son cientos de milisegundos y aqui se van en serie. En un token que
    se mueve, medio segundo de mas es entrar a otro precio, asi que todo lo que se pueda leer
    junto se lee junto.
    """
    result = await rpc.call("getMultipleAccounts", [addresses, {"encoding": "base64"}])
    values = (result or {}).get("value") or []
    out: list[tuple[bytes, str, int] | None] = []
    for value in values:
        out.append(
            (base64.b64decode(value["data"][0]), value["owner"], int(value["lamports"]))
            if value
            else None
        )
    # `getMultipleAccounts` devuelve una entrada por direccion; si no, no se puede emparejar.
    out.extend([None] * (len(addresses) - len(out)))
    return out


# El blockhash vale unos 60-90 segundos. Se reutiliza unos pocos para ahorrar una llamada en
# el camino critico, pero muy por debajo de su caducidad: uno vencido es una firma perdida.
_BLOCKHASH_TTL_SECONDS: Final = 8.0
_blockhash_cache: tuple[str, int, float] | None = None


async def _recent_blockhash(rpc: SolanaRpc) -> tuple[str, int]:
    """Blockhash y la altura hasta la que sigue siendo valido.

    `last_valid_block_height` se devuelve al panel a proposito: sin el, una firma que llega
    tarde falla con un error del que no se puede decir nada util. Con el, se sabe cuanto
    queda y se puede avisar antes de que el usuario firme algo que ya no vale.
    """
    global _blockhash_cache
    now = time.monotonic()
    if _blockhash_cache is not None and now - _blockhash_cache[2] < _BLOCKHASH_TTL_SECONDS:
        return _blockhash_cache[0], _blockhash_cache[1]
    result = await rpc.call("getLatestBlockhash", [{"commitment": "confirmed"}])
    value = str(result["value"]["blockhash"])
    height = int(result["value"]["lastValidBlockHeight"])
    _blockhash_cache = (value, height, now)
    return value, height


# La cuenta `Global` cambia muy de vez en cuando (comisiones, destinatarios). Se relee cada
# poco en vez de en cada operacion.
_GLOBAL_TTL_SECONDS: Final = 60.0
_global_cache: tuple[bytes, float] | None = None


async def _global_account(rpc: SolanaRpc) -> bytes:
    global _global_cache
    now = time.monotonic()
    if _global_cache is not None and now - _global_cache[1] < _GLOBAL_TTL_SECONDS:
        return _global_cache[0]
    fetched = await _fetch_account(rpc, str(global_pda()))
    if fetched is None:
        raise HTTPException(status_code=502, detail="no se pudo leer la cuenta global")
    _global_cache = (fetched[0], now)
    return fetched[0]


# Cuentas que el IDL declara para cada instruccion. El programa desplegado va por delante del
# IDL y espera algunas mas; todo lo que venga despues de estas es "extra".
_IDL_ACCOUNT_COUNT: Final = {"buy": 16, "sell": 14}

# La cuenta ligada al token es fija, asi que se recuerda. Si el programa cambiara, la
# simulacion previa lo detecta y la operacion no llega a ofrecerse para firmar.
_TOKEN_CACHE_MAX: Final = 2_000
_token_account_cache: dict[str, Pubkey] = {}


async def _discover_token_extra_account(
    rpc: SolanaRpc, mint: Pubkey, buyback_recipients: tuple[Pubkey, ...]
) -> Pubkey:
    """Cuenta ligada al token que el programa exige y su IDL no declara.

    Pump.fun tiene desplegada una version por delante de su IDL publicado: `buy` y `sell`
    piden cuentas que el IDL no lista. Se probaron todas las semillas que aparecen en los dos
    IDLs (el del programa y el de fees) contra el mint, la curva, el creador y la sharing
    config, y ninguna la deriva. Como el programa la valida —con una cuenta cualquiera
    revierte con el error 6074—, no vale inventarla.

    Asi que se copia de una operacion REAL del mismo token, donde el programa ya la acepto.
    En todas las operaciones observadas la forma es la misma: la ULTIMA cuenta es uno de los
    `buyback_fee_recipients` y la PENULTIMA es esta, igual en compras y en ventas, y la misma
    para cualquier usuario. Que la ultima sea un buyback conocido es el control de que lo
    copiado tiene la forma esperada.
    """
    from mit_pumpfun.decoder import DecodeError, iter_instructions
    from mit_pumpfun.trade import DISC_BUY, DISC_SELL, bonding_curve_pda

    cached = _token_account_cache.get(str(mint))
    if cached is not None:
        return cached

    known_buyback = {str(x) for x in buyback_recipients}
    signatures = await rpc.get_signatures(str(bonding_curve_pda(mint)), limit=25)

    for entry in signatures:
        if entry.get("err"):
            continue
        transaction = await rpc.get_transaction(entry["signature"])
        if not transaction:
            continue
        try:
            instructions = list(iter_instructions(transaction))
        except DecodeError:
            continue
        for parsed in instructions:
            side = (
                "buy" if parsed.discriminator == DISC_BUY
                else "sell" if parsed.discriminator == DISC_SELL
                else None
            )
            if side is None or len(parsed.accounts) <= _IDL_ACCOUNT_COUNT[side] + 1:
                continue
            if parsed.accounts[-1] not in known_buyback:
                continue
            resolved = Pubkey.from_string(parsed.accounts[-2])
            # Acotado: el radar ve miles de tokens al dia y una cache sin limite acaba
            # siendo una fuga de memoria en un proceso que no se reinicia.
            if len(_token_account_cache) >= _TOKEN_CACHE_MAX:
                _token_account_cache.pop(next(iter(_token_account_cache)))
            _token_account_cache[str(mint)] = resolved
            return resolved

    msg = (
        "no se encontro ninguna operacion reciente de este token de la que copiar la cuenta "
        "que el programa exige y no publica en su IDL"
    )
    raise HTTPException(status_code=409, detail=msg)


async def _simulate(rpc: SolanaRpc, transaction: Transaction) -> None:
    """Ejecuta la transaccion contra el estado real SIN enviarla. Si el programa la rechaza,
    se aborta aqui: mas vale un boton que no responde que una firma que quema comisiones."""
    result = await rpc.call(
        "simulateTransaction",
        [
            base64.b64encode(bytes(transaction)).decode(),
            {
                "sigVerify": False,
                "replaceRecentBlockhash": True,
                "commitment": "confirmed",
                "encoding": "base64",
            },
        ],
    )
    value = (result or {}).get("value") or {}
    if value.get("err") is None:
        return

    logs = value.get("logs") or []
    raise RechazoCadena(_explicar(value["err"], logs), _codigo_de(value["err"]))


async def _token_balance(rpc: SolanaRpc, ata: Pubkey) -> int:
    """Saldo en unidades base. Cero si la cuenta no existe todavia."""
    try:
        result = await rpc.call("getTokenAccountBalance", [str(ata)])
    except RpcError:
        # La cuenta no existe todavia: saldo cero, no es un fallo. Un 429 SI es un fallo y
        # sube: tratarlo como saldo cero convertiria un corte del RPC en "no tienes nada".
        return 0
    if not result or not result.get("value"):
        return 0
    return int(result["value"]["amount"])


@router.post("/prepare", response_model=PrepareResponse)
async def prepare(request: PrepareRequest) -> PrepareResponse:
    """Construye la transaccion sin firmar para que la cartera del usuario la apruebe."""
    try:
        mint = Pubkey.from_string(request.mint)
        user = Pubkey.from_string(request.user)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"direccion invalida: {exc}") from exc

    # Los importes se extraen aqui, una sola vez, para que mas abajo sean numeros y no
    # opcionales: un `assert` desaparece con `python -O` y dejaria pasar un `None` justo en
    # el punto donde se decide cuanto dinero se mueve.
    amount_sol = 0.0
    sell_percent = 0.0
    if request.side == "buy":
        if request.amount_sol is None or request.amount_sol <= 0:
            raise HTTPException(status_code=400, detail="amount_sol debe ser mayor que cero")
        if request.amount_sol > MAX_ORDER_SOL:
            raise HTTPException(
                status_code=400,
                detail=f"importe por encima del tope del panel ({MAX_ORDER_SOL} SOL)",
            )
        amount_sol = request.amount_sol
    else:
        if request.sell_percent is None:
            raise HTTPException(status_code=400, detail="sell_percent obligatorio para vender")
        sell_percent = request.sell_percent

    async with _abrir_rpc() as rpc:
        try:
            # Todo lo que hace falta leer de la cadena, en una sola llamada. El acumulador
            # del usuario solo importa al vender, pero pedirlo siempre no cuesta nada mas y
            # ahorra una segunda ronda cuando toca.
            accumulator = user_volume_accumulator_pda(user)
            curve_account, mint_account, accumulator_account, user_account = (
                await _fetch_accounts(
                    rpc, [str(bonding_curve_pda(mint)), str(mint), str(accumulator), str(user)]
                )
            )

            # **La cartera del usuario, comprobada aqui y no en la cadena.** Una Phantom recien
            # creada y sin fondear no existe on-chain, y el programa responde `AccountNotFound`:
            # un error correcto que no le dice nada a quien lo lee. Es mejor decirle que le
            # falta SOL, que es lo que de verdad pasa.
            if user_account is None:
                raise HTTPException(
                    status_code=409,
                    # Se nombra la cartera por su DIRECCION y no como «tu Phantom»: esta misma
                    # ruta la usa el programa de escritorio con su cartera propia, y ahi decir
                    # «manda SOL a tu Phantom» manda al usuario justo a la cartera equivocada.
                    detail=(
                        f"la cartera {user} no tiene SOL todavia: sin saldo la cuenta ni "
                        "siquiera existe en Solana. Mandale algo antes de operar."
                    ),
                )

            if curve_account is None:
                raise HTTPException(
                    status_code=404,
                    detail="ese mint no tiene curva de Pump.fun: o no es de Pump.fun o ya migro",
                )
            curve = BondingCurveAccount.decode(curve_account[0])

            if curve.complete:
                raise HTTPException(
                    status_code=409,
                    detail="la curva se completo: el token ya graduo y se opera en PumpSwap",
                )
            if not curve.quotes_in_sol:
                raise HTTPException(
                    status_code=409,
                    detail=f"esta curva no cotiza en SOL sino en {curve.quote_mint}",
                )
            if mint_account is None:
                raise HTTPException(status_code=404, detail="el mint no existe")
            token_program = Pubkey.from_string(mint_account[1])

            global_data = await _global_account(rpc)
            # Un token en *mayhem mode* cobra por otra ventanilla: con el destinatario normal
            # el programa revierte con `NotAuthorized`.
            fee_recipient = (
                decode_global_reserved_fee_recipient(global_data)
                if curve.is_mayhem_mode
                else decode_global_fee_recipient(global_data)
            )
            buyback_recipients = decode_global_buyback_recipients(global_data)
            fee_bps = decode_global_fee_bps(global_data)
            token_extra = await _discover_token_extra_account(rpc, mint, buyback_recipients)
            # Cualquiera de los ocho vale: se simulo la misma compra contra varios y el
            # programa acepto todos. Se fija el primero para que la operacion sea reproducible.
            extra_accounts: tuple[Pubkey, ...] = (token_extra, buyback_recipients[0])

            # **La venta no admite una unica composicion.** Segun la cartera, el programa
            # espera el acumulador de volumen del vendedor delante de las otras dos cuentas o
            # no lo espera en absoluto; con la eleccion equivocada revierte con el error 6074.
            # Se probo sobre carteras reales del mismo token: unas exigian una forma y otras
            # la otra, y una regla fija dejaba sin poder vender a dos de cada tres.
            # Por eso se preparan las dos y decide la cadena en la simulacion, que ya se hace.
            candidatos: list[tuple[Pubkey, ...]] = [extra_accounts]
            if request.side == "sell" and accumulator_account is not None:
                candidatos.insert(0, (accumulator, *extra_accounts))

            blockhash_str, last_valid_height = await _recent_blockhash(rpc)
            blockhash = Hash.from_string(blockhash_str)

            accounts = TradeAccounts(
                mint=mint,
                user=user,
                token_program=token_program,
                fee_recipient=fee_recipient,
                creator=curve.creator,
            )

            summary: dict[str, Any]
            construir: Callable[[tuple[Pubkey, ...]], list[Instruction]]

            if request.side == "buy":
                lamports_in = int(amount_sol * LAMPORTS_PER_SOL)
                # La comision se aparta ANTES de calcular los tokens: asi el gasto total es el
                # importe que el usuario escribio, no ese importe mas un pico.
                to_curve = net_lamports_after_fee(lamports_in, fee_bps)
                tokens_out = tokens_out_for_lamports(curve, to_curve)
                if tokens_out <= 0:
                    raise HTTPException(
                        status_code=409, detail="la curva no puede entregar tokens por ese importe"
                    )
                # El tope SI cubre comision e imprevisto: es lo maximo que el programa puede
                # llegar a cobrar, y por encima de eso revierte en vez de ejecutar.
                max_cost = apply_slippage_up(lamports_in, request.slippage_bps)

                # Hace falta el tope MAS el alquiler de la cuenta de token y las comisiones de
                # cadena. Decirlo aqui, con las cifras, evita que el usuario firme algo que va
                # a revertir y pague la comision de una transaccion fallida.
                necesario = max_cost + _MARGEN_CUENTA_Y_RED
                if user_account[2] < necesario:
                    tienes = user_account[2] / LAMPORTS_PER_SOL
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"saldo insuficiente: tienes {tienes:.4f} SOL y esta compra puede "
                            f"llegar a necesitar {necesario / LAMPORTS_PER_SOL:.4f} SOL "
                            f"(importe + deslizamiento + cuenta del token + comision de red)."
                        ),
                    )

                def construir(extras: tuple[Pubkey, ...]) -> list[Instruction]:
                    return [
                        *_compute_budget_instructions(request.priority_fee_microlamports),
                        # Idempotente: si ya tenia cuenta de este token, no estorba.
                        build_create_ata_idempotent_instruction(user, user, mint, token_program),
                        build_buy_instruction(
                            accounts,
                            token_amount=tokens_out,
                            max_lamports_cost=max_cost,
                            extra_accounts=extras,
                        ),
                    ]
                summary = {
                    "side": "buy",
                    "amount_sol": amount_sol,
                    "tokens_expected": tokens_out,
                    "max_cost_sol": max_cost / LAMPORTS_PER_SOL,
                    "fee_bps": fee_bps,
                    "slippage_bps": request.slippage_bps,
                }
            else:
                ata = associated_token_address(user, mint, token_program)
                balance = await _token_balance(rpc, ata)
                if balance <= 0:
                    raise HTTPException(
                        status_code=409, detail="no tienes tokens de este mint en esa cartera"
                    )
                tokens_in = int(balance * sell_percent / 100)
                if tokens_in <= 0:
                    raise HTTPException(
                        status_code=400, detail="la cantidad a vender queda en cero"
                    )

                # Al vender la comision se descuenta de lo que se recibe, asi que el suelo se
                # calcula sobre el neto. Ponerlo sobre el bruto exige a la curva mas SOL del
                # que puede dar y hace revertir ventas perfectamente normales.
                gross = lamports_out_for_tokens(curve, tokens_in)
                expected = net_lamports_after_fee(gross, fee_bps)
                min_output = apply_slippage_down(expected, request.slippage_bps)

                # Vender polvo cuesta mas de lo que entra: la comision de red y la prioridad
                # son fijas, y por debajo de cierto importe se paga por perder dinero. Mejor
                # decirlo que dejar firmar una operacion que sale negativa por definicion.
                coste_red = _COMISION_BASE_LAMPORTS + (
                    COMPUTE_UNIT_LIMIT * request.priority_fee_microlamports // 1_000_000
                )
                if expected <= coste_red:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"esta venta daria {expected / LAMPORTS_PER_SOL:.9f} SOL y mandarla "
                            f"cuesta {coste_red / LAMPORTS_PER_SOL:.9f}: saldrias perdiendo. "
                            "Vende un porcentaje mayor o dejalo estar."
                        ),
                    )
                def construir(extras: tuple[Pubkey, ...]) -> list[Instruction]:
                    return [
                        *_compute_budget_instructions(request.priority_fee_microlamports),
                        build_sell_instruction(
                            accounts,
                            token_amount=tokens_in,
                            min_lamports_output=min_output,
                            extra_accounts=extras,
                        ),
                    ]
                summary = {
                    "side": "sell",
                    "tokens_sold": tokens_in,
                    "balance": balance,
                    "sell_percent": sell_percent,
                    "expected_sol": expected / LAMPORTS_PER_SOL,
                    "min_output_sol": min_output / LAMPORTS_PER_SOL,
                    "fee_bps": fee_bps,
                    "slippage_bps": request.slippage_bps,
                }

            # Nada se ofrece a firmar sin que la cadena lo haya aceptado antes. Si hay mas de
            # una composicion posible de cuentas, la simulacion es quien decide cual vale: se
            # queda la primera que el programa acepta.
            transaction = None
            ultimo_rechazo: HTTPException | None = None
            reintentado_por_saldo = False
            reintentado_por_precio = False
            for extras in candidatos:
                message = Message.new_with_blockhash(construir(extras), user, blockhash)
                candidata = Transaction.new_unsigned(message)
                try:
                    await _simulate(rpc, candidata)
                except HTTPException as exc:
                    ultimo_rechazo = exc
                    # **El precio pudo subir entre leer la curva y simular.** En un token con
                    # presion compradora fuerte pasa en decimas de segundo. Se relee la curva
                    # y se recalcula UNA vez con el precio de ahora; si vuelve a subir, es que
                    # el slippage del usuario se queda corto y hay que decirselo, no insistir.
                    if (
                        request.side == "buy"
                        and not reintentado_por_precio
                        and isinstance(exc, RechazoCadena)
                        and exc.codigo == _ERROR_PRECIO_SUBIO
                    ):
                        reintentado_por_precio = True
                        fresca = await curva_fresca(rpc, mint)
                        if fresca is not None and not fresca.complete:
                            curve = fresca
                            to_curve = net_lamports_after_fee(lamports_in, fee_bps)
                            tokens_out = tokens_out_for_lamports(curve, to_curve)
                            summary["tokens_expected"] = tokens_out
                            summary["precio_releido"] = True
                            if tokens_out > 0:
                                message = Message.new_with_blockhash(
                                    construir(extras), user, blockhash)
                                reintento = Transaction.new_unsigned(message)
                                try:
                                    await _simulate(rpc, reintento)
                                except HTTPException as otra:
                                    ultimo_rechazo = otra
                                    continue
                                transaction = reintento
                                break
                    # **El saldo pudo moverse entre leerlo y simular.** Pasa al liquidar el
                    # 100%: se pide todo lo que habia y para cuando llega ya hay menos, y el
                    # programa responde `NotEnoughTokensToSell`. Se relee y se reintenta UNA
                    # vez; insistir en bucle contra un saldo que baja no termina nunca.
                    if (
                        request.side == "sell"
                        and not reintentado_por_saldo
                        and isinstance(exc, RechazoCadena)
                        and exc.codigo == _ERROR_TOKENS_INSUFICIENTES
                    ):
                        reintentado_por_saldo = True
                        saldo_ahora = await _token_balance(rpc, associated_token_address(
                            user, mint, token_program))
                        if 0 < saldo_ahora < balance:
                            balance = saldo_ahora
                            tokens_in = int(balance * sell_percent / 100)
                            # El suelo se recalcula: con menos tokens entra menos SOL, y
                            # dejar el de la cantidad anterior haria revertir la venta por
                            # exigir un minimo que ya no da la curva.
                            gross = lamports_out_for_tokens(curve, tokens_in)
                            expected = net_lamports_after_fee(gross, fee_bps)
                            min_output = apply_slippage_down(expected, request.slippage_bps)
                            summary["tokens_sold"] = tokens_in
                            summary["balance"] = balance
                            summary["expected_sol"] = expected / LAMPORTS_PER_SOL
                            summary["min_output_sol"] = min_output / LAMPORTS_PER_SOL
                            summary["saldo_releido"] = True
                            if tokens_in > 0:
                                message = Message.new_with_blockhash(
                                    construir(extras), user, blockhash)
                                reintento = Transaction.new_unsigned(message)
                                try:
                                    await _simulate(rpc, reintento)
                                except HTTPException as otra:
                                    ultimo_rechazo = otra
                                    continue
                                transaction = reintento
                                break
                    continue
                transaction = candidata
                break

            if transaction is None:
                raise ultimo_rechazo or HTTPException(
                    status_code=409, detail="la cadena rechaza esta operacion"
                )

        except (RpcError, RpcRateLimitedError) as exc:
            raise HTTPException(status_code=502, detail=f"el RPC no respondio: {exc}") from exc
        except TradeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    summary["blockhash"] = str(blockhash)
    # Hasta que altura vale esta transaccion. El panel lo usa para avisar ANTES de que el
    # usuario firme algo caducado, en vez de dejarle un fallo sin explicacion.
    summary["last_valid_block_height"] = last_valid_height
    return PrepareResponse(
        transaction_base64=base64.b64encode(bytes(transaction)).decode(),
        transaction_base58=based58.b58encode(bytes(transaction)).decode("ascii"),
        message_base58=based58.b58encode(bytes(message)).decode("ascii"),
        summary=summary,
    )


# La graduacion es de ida sin vuelta: una curva completada no vuelve a abrirse. Por eso una
# vez confirmada se recuerda para siempre, y solo lo desconocido se vuelve a preguntar.
_GRADUACION_TTL: Final = 20.0
_graduacion_cache: dict[str, tuple[bool, float]] = {}


# La curva se relee muy a menudo: de sus reservas sale el precio, y del precio sale el stop
# loss. Un dato viejo aqui no es un numero feo en pantalla, es un stop que no salta.
#
# Medio segundo, no segundo y medio. Con 1,5 s el precio servido llegaba a desviarse un 20%
# del real en tokens que se mueven; con 0,5 baja a la unidad. Se puede permitir porque una
# lectura fresca cuesta ~60 ms —los 400 ms que parecian del RPC eran en realidad la espera
# inicial del seguidor— y el panel solo vigila un token a la vez.
_CURVA_TTL: Final = 0.5
_curva_cache: dict[str, tuple[BondingCurveAccount, float]] = {}


async def curva_actual(mint: str) -> BondingCurveAccount | None:
    """Estado de la curva leido de SU CUENTA, no de las operaciones observadas.

    Derivar el precio de los eventos que se ven pasar por los logs parece equivalente y no lo
    es: si se pierde una operacion, las reservas se quedan atras. Medido contra la cadena, el
    desvio llegaba al 9%, y con el trailing puesto al 3% eso significa un stop que no salta
    cuando deberia. La cuenta de la curva siempre esta al dia.
    """
    guardado = _curva_cache.get(mint)
    ahora = time.monotonic()
    if guardado is not None and ahora - guardado[1] < _CURVA_TTL:
        return guardado[0]

    # **Se espera a la lectura, a proposito.** Se probo servir el dato viejo y refrescar por
    # detras: bajaba la respuesta de 400 ms a 13, pero el precio servido llegaba a desviarse
    # un 26% del real. De ese precio sale la decision del stop, asi que un dato rapido y
    # equivocado es peor que uno lento y cierto: el stop saltaria tarde justo en los tokens
    # que se mueven rapido, que son los unicos donde importa.
    return await _leer_curva(mint)


async def _leer_curva(mint: str) -> BondingCurveAccount | None:
    """Lee la curva del RPC y la guarda. Devuelve lo guardado si la lectura falla."""
    guardado = _curva_cache.get(mint)
    try:
        clave = Pubkey.from_string(mint)
    except ValueError:
        return None
    async with _abrir_rpc() as rpc:
        try:
            cuenta = await _fetch_account(rpc, str(bonding_curve_pda(clave)))
        except (RpcError, RpcRateLimitedError):
            return guardado[0] if guardado else None
    if cuenta is None:
        return None
    try:
        curva = BondingCurveAccount.decode(cuenta[0])
    except TradeError:
        return None
    if len(_curva_cache) >= _TOKEN_CACHE_MAX:
        _curva_cache.pop(next(iter(_curva_cache)))
    _curva_cache[mint] = (curva, time.monotonic())
    return curva


def forget_graduation(mint: str) -> None:
    """Olvida lo que se sabia de este token para que se vuelva a mirar la cadena.

    La llama el seguidor en vivo cuando ve pasar una `migrate`: en vez de creerse el log,
    fuerza a confirmarlo contra la cuenta de la curva en la siguiente consulta.
    """
    _graduacion_cache.pop(mint, None)


async def token_graduated(mint: str) -> bool | None:
    """¿Se completo la curva de este token? `None` si no se ha podido averiguar.

    **Se mira la CUENTA de la curva, no los logs.** Deducirlo de la forma de las operaciones
    daba falsos positivos —una operacion de PumpSwap que solo menciona el mint bastaba— y con
    eso el panel se negaba a operar tokens perfectamente vivos. Comparado contra la cadena,
    fallaba en 6 de cada 10.

    Ante la duda devuelve `None` y no `False`: quien llama debe poder distinguir «no graduo»
    de «no lo se», porque bloquear una compra buena y permitir una imposible no cuestan igual.
    """
    guardado = _graduacion_cache.get(mint)
    now = time.monotonic()
    if guardado is not None and (guardado[0] or now - guardado[1] < _GRADUACION_TTL):
        return guardado[0]

    # **Se reutiliza la lectura de la curva en vez de repetirla.** La graduacion es el campo
    # `complete` de esa misma cuenta: leerla dos veces por peticion era el doble de viajes al
    # RPC para el mismo dato, y ahi se iba la mitad del tiempo de respuesta.
    curva = await curva_actual(mint)
    if curva is None:
        return None
    completa = curva.complete

    if len(_graduacion_cache) >= _TOKEN_CACHE_MAX:
        _graduacion_cache.pop(next(iter(_graduacion_cache)))
    _graduacion_cache[mint] = (completa, now)
    return completa


@router.post("/warm/{mint}")
async def warm(mint: str) -> dict[str, Any]:
    """Deja listo lo caro de un token ANTES de que el usuario pulse comprar.

    Descubrir la cuenta que Pump.fun no publica exige rastrear operaciones recientes, y eso
    son varias consultas al RPC. Hacerlo en el momento de comprar mete ese retraso justo donde
    mas caro sale. El panel llama aqui en cuanto hay un mint en pantalla, y para cuando se
    pulsa el boton ya esta todo en memoria.
    """
    try:
        mint_key = Pubkey.from_string(mint)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"mint invalido: {exc}") from exc

    async with _abrir_rpc() as rpc:
        try:
            global_data = await _global_account(rpc)
            recipients = decode_global_buyback_recipients(global_data)
            await _discover_token_extra_account(rpc, mint_key, recipients)
            await _recent_blockhash(rpc)
        except (RpcError, RpcRateLimitedError) as exc:
            raise HTTPException(status_code=502, detail=f"el RPC no respondio: {exc}") from exc
        except TradeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return {"mint": mint, "ready": True}


@router.get("/position/{user}/{mint}")
async def position(user: str, mint: str) -> dict[str, Any]:
    """Cuantos tokens de `mint` tiene `user`. El panel lo usa para dimensionar la venta."""
    try:
        user_key = Pubkey.from_string(user)
        mint_key = Pubkey.from_string(mint)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"direccion invalida: {exc}") from exc

    async with _abrir_rpc() as rpc:
        mint_account = await _fetch_account(rpc, str(mint_key))
        if mint_account is None:
            raise HTTPException(status_code=404, detail="el mint no existe")
        token_program = Pubkey.from_string(mint_account[1])
        ata = associated_token_address(user_key, mint_key, token_program)
        balance = await _token_balance(rpc, ata)

    return {"mint": mint, "user": user, "balance": balance, "token_account": str(ata)}


@router.get("/status/{signature}")
async def status(signature: str) -> dict[str, Any]:
    """Estado de una transaccion ya enviada por la cartera.

    `pendiente` no significa que se haya perdido: significa que todavia no se sabe. Distinguir
    ambas cosas es justo lo que evita comprar dos veces el mismo token.
    """
    async with _abrir_rpc() as rpc:
        try:
            result = await rpc.call(
                "getSignatureStatuses", [[signature], {"searchTransactionHistory": True}]
            )
        except (RpcError, RpcRateLimitedError) as exc:
            raise HTTPException(status_code=502, detail=f"el RPC no respondio: {exc}") from exc

    value = (result or {}).get("value") or [None]
    entry = value[0]
    if entry is None:
        return {"signature": signature, "state": "pendiente"}
    if entry.get("err"):
        return {"signature": signature, "state": "fallida", "error": str(entry["err"])}
    return {
        "signature": signature,
        "state": "confirmada",
        "confirmations": entry.get("confirmations"),
        "confirmation_status": entry.get("confirmationStatus"),
    }
