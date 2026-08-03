"""Matematica de la bonding curve de Pump.fun (SPEC.md 7).

Todo se deriva de la invariante de producto constante `x·y = k` sobre las reservas VIRTUALES
del propio token. Nada aqui es una constante copiada de un blog.

**El umbral de graduacion no son 69.000 $.** Esa cifra circula por todas partes y es un
artefacto de cuando SOL valia ~168 $. El umbral real esta fijado en SOL por el programa y se
deduce de la curva: cuando se agotan las reservas reales de token, la curva se completa. Con
los parametros observados en mainnet salen ~85 SOL recaudados y ~411 SOL de capitalizacion.
Si SOL cambia de precio, esas cifras en dolares cambian y la de aqui no, porque no depende
del dolar.

Los importes son enteros de lamports y unidades base de token. Ni un `float` en el camino:
un redondeo aqui se propaga al price impact, y del price impact salen decisiones de tamano.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from mit_shared.types import LAMPORTS_PER_SOL

# Tamanos de orden para los que SPEC.md 7 exige calcular el impacto.
IMPACT_SIZES_SOL: Final[tuple[str, ...]] = ("0.01", "0.05", "0.1", "0.25", "0.5", "1")

BASIS_POINTS: Final = 10_000


class CurveError(ValueError):
    """Los parametros de la curva no permiten calcular."""


@dataclass(frozen=True, slots=True)
class CurveState:
    """Reservas de la curva, en unidades enteras de cadena."""

    virtual_sol_reserves: int
    virtual_token_reserves: int
    real_token_reserves: int
    token_total_supply: int
    real_sol_reserves: int = 0

    def __post_init__(self) -> None:
        if self.virtual_sol_reserves <= 0 or self.virtual_token_reserves <= 0:
            msg = "las reservas virtuales deben ser positivas"
            raise CurveError(msg)
        if self.real_token_reserves < 0:
            msg = "las reservas reales no pueden ser negativas"
            raise CurveError(msg)

    @property
    def invariant(self) -> int:
        """`k = x·y`. Entero exacto: es lo que conserva la curva."""
        return self.virtual_sol_reserves * self.virtual_token_reserves

    @property
    def is_complete(self) -> bool:
        """La curva se ha completado cuando no quedan tokens reales que vender."""
        return self.real_token_reserves <= 0


def spot_price_lamports(state: CurveState) -> Decimal:
    """Precio marginal, en lamports por unidad base de token."""
    return Decimal(state.virtual_sol_reserves) / Decimal(state.virtual_token_reserves)


def tokens_out_for_sol(state: CurveState, lamports_in: int) -> int:
    """Tokens que se reciben al aportar `lamports_in`, segun la invariante.

    Se trunca hacia abajo: nunca se promete mas salida de la que la curva puede dar.
    """
    if lamports_in <= 0:
        return 0
    new_sol = state.virtual_sol_reserves + lamports_in
    new_tokens = state.invariant // new_sol
    out = state.virtual_token_reserves - new_tokens
    # No se puede comprar mas de lo que realmente queda en la curva.
    return max(0, min(out, state.real_token_reserves))


def sol_out_for_tokens(state: CurveState, tokens_in: int) -> int:
    """Lamports que se reciben al vender `tokens_in`."""
    if tokens_in <= 0:
        return 0
    new_tokens = state.virtual_token_reserves + tokens_in
    new_sol = state.invariant // new_tokens
    return max(0, state.virtual_sol_reserves - new_sol)


def price_impact_bps(state: CurveState, lamports_in: int) -> int:
    """Impacto de una compra, en puntos basicos sobre el precio marginal.

    Compara el precio EFECTIVO pagado (importe / tokens recibidos) contra el precio spot.
    Es la diferencia entre lo que crees que pagas y lo que pagas.
    """
    tokens = tokens_out_for_sol(state, lamports_in)
    if tokens <= 0:
        # La curva no puede absorber la orden: impacto total, no "cero".
        return BASIS_POINTS
    effective = Decimal(lamports_in) / Decimal(tokens)
    spot = spot_price_lamports(state)
    return int((effective / spot - 1) * BASIS_POINTS)


def impact_curve(state: CurveState) -> dict[str, int]:
    """Impacto en bps para cada tamano de SPEC.md 7.

    Se devuelve el mapa completo y no un solo numero porque la forma de la curva es la
    informacion util: un token puede tener impacto asumible a 0,01 SOL y prohibitivo a 1 SOL,
    y eso decide el tamano maximo operable mucho antes que cualquier score.
    """
    result: dict[str, int] = {}
    for size in IMPACT_SIZES_SOL:
        lamports = int(Decimal(size) * LAMPORTS_PER_SOL)
        result[size] = price_impact_bps(state, lamports)
    return result


def sol_to_complete(state: CurveState) -> int:
    """Lamports que faltan para completar la curva (graduacion).

    Deriva de la invariante: cuando se hayan vendido todas las reservas REALES de token, las
    virtuales restantes fijan la reserva de SOL final, y la diferencia con la actual es lo
    que falta por recaudar.
    """
    if state.is_complete:
        return 0
    remaining_virtual = state.virtual_token_reserves - state.real_token_reserves
    if remaining_virtual <= 0:
        msg = "reservas incoherentes: las reales superan a las virtuales"
        raise CurveError(msg)
    final_sol = state.invariant // remaining_virtual
    return max(0, final_sol - state.virtual_sol_reserves)


def graduation_market_cap_lamports(state: CurveState) -> int:
    """Capitalizacion en el momento de graduarse, en lamports.

    Es la cifra que la gente traduce a dolares y luego repite como si fuera fija. Aqui se
    calcula desde la curva de ESTE token, asi que sigue siendo correcta aunque Pump.fun
    cambie parametros o SOL se mueva.
    """
    remaining_virtual = state.virtual_token_reserves - state.real_token_reserves
    if remaining_virtual <= 0:
        return 0
    final_price = Decimal(state.invariant // remaining_virtual) / Decimal(remaining_virtual)
    return int(final_price * state.token_total_supply)


def progress_pct(state: CurveState) -> Decimal:
    """Progreso hacia la graduacion, 0-100, por tokens reales ya vendidos."""
    initial = state.real_token_reserves + (state.token_total_supply - state.virtual_token_reserves)
    if state.token_total_supply <= 0 or initial <= 0:
        return Decimal(0)
    sold = initial - state.real_token_reserves
    pct = Decimal(sold) / Decimal(initial) * 100
    return max(Decimal(0), min(Decimal(100), pct))


def market_cap_lamports(state: CurveState) -> int:
    """Capitalizacion actual: supply x precio marginal."""
    return int(spot_price_lamports(state) * state.token_total_supply)
