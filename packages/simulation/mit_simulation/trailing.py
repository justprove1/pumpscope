"""Trailing stop sobre una serie de precios real (SPEC.md 13, 18).

**El error que este modulo existe para evitar.** Es tentador calcular la salida como
`pico x (1 - trailing)`: se sabe el maximo que alcanzo el token, se descuenta el trailing y sale
un numero bonito. Es falso. Un trailing stop salta en el PRIMER retroceso que supera el umbral,
y en un memecoin eso suele pasar mucho antes del maximo. En los datos capturados, 2 de cada 7
estampidas se hundieron mas de un 20% a mitad de subida: un stop del 20% las cerro abajo y nunca
vio el pico que el calculo ingenuo les atribuia.

Por eso aqui se recorre la serie punto a punto. Es mas lento y da cifras peores. Son las de
verdad.

**No decide nada por si mismo.** Es analisis historico: no abre posiciones, no firma nada y no
conoce el estado de la cartera. Alimentar con esto un ejecutor real exige pasar por el
RiskEngine y el signer, con su checklist.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass

# Serie de precios: (segundos desde la entrada, precio o capitalizacion).
Series = Sequence[tuple[float, float]]


@dataclass(frozen=True, slots=True)
class CostModel:
    """Lo que cuesta entrar y salir. Ignorarlo convierte perdidas en 'beneficios'."""

    # Comision del protocolo por lado (Pump.fun cobra ~1%).
    entry_fee: float = 0.01
    exit_fee: float = 0.01
    # Deslizamiento por lado, en fraccion de precio.
    entry_slippage: float = 0.0125
    exit_slippage: float = 0.0125
    # Comisiones fijas de cadena (base + prioridad), ida y vuelta, en SOL.
    fixed_sol: float = 0.00041


@dataclass(frozen=True, slots=True)
class TrailingResult:
    """Resultado de una operacion simulada."""

    exit_price: float
    exit_reason: str
    multiple: float
    peak_multiple: float
    captured_peak: bool
    profit_sol: float
    seconds_held: float


def backtest_series(
    series: Series,
    *,
    trailing: float,
    costs: CostModel | None = None,
    size_sol: float = 1.0,
) -> TrailingResult:
    """Recorre la serie y cierra en el primer retroceso mayor que `trailing`.

    La entrada es el primer punto de la serie: representa comprar en el instante en que el
    sistema detecta la oportunidad, no antes. Comprar "en el segundo cero" no es una opcion
    disponible y suponerlo falsearia el resultado.
    """
    if not 0.0 < trailing < 1.0:
        msg = "el trailing tiene que estar entre 0 y 1 sin incluirlos"
        raise ValueError(msg)
    if len(series) < 2:
        msg = "la serie necesita al menos dos puntos para simular una operacion"
        raise ValueError(msg)

    model = costs or CostModel()
    entry_time, entry_price = series[0]
    if entry_price <= 0:
        msg = "la serie arranca con un precio no positivo"
        raise ValueError(msg)

    running_peak = entry_price
    exit_price = series[-1][1]
    exit_time = series[-1][0]
    exit_reason = "fin de serie"
    exit_index = len(series) - 1

    for offset, (at, price) in enumerate(series[1:], start=1):
        running_peak = max(running_peak, price)
        if running_peak > 0 and (running_peak - price) / running_peak >= trailing:
            exit_price, exit_time, exit_reason = price, at, "trailing"
            exit_index = offset
            break

    peak_overall = max(price for _, price in series)
    peak_index = next(i for i, (_, price) in enumerate(series) if price == peak_overall)
    multiple = exit_price / entry_price

    # Se paga el deslizamiento y la comision en ambas patas, sobre el importe de cada una.
    paid = size_sol * (1 + model.entry_fee + model.entry_slippage)
    received = size_sol * multiple * (1 - model.exit_fee - model.exit_slippage)
    profit = received - paid - model.fixed_sol

    return TrailingResult(
        exit_price=exit_price,
        exit_reason=exit_reason,
        multiple=multiple,
        peak_multiple=peak_overall / entry_price,
        # Capturado = el stop dejo llegar al maximo antes de cerrar. Si salta ANTES, corto la
        # subida y el pico posterior nunca estuvo disponible: es el caso que hay que detectar.
        captured_peak=exit_index >= peak_index,
        profit_sol=profit,
        seconds_held=exit_time - entry_time,
    )


@dataclass(frozen=True, slots=True)
class SweepRow:
    """Resultado agregado de un nivel de trailing sobre todos los casos."""

    trailing: float
    cases: int
    winners: int
    losers: int
    total_sol: float
    mean_sol: float
    median_sol: float
    worst_sol: float
    best_sol: float
    captured_peak: int


def sweep(
    cases: Sequence[Series],
    *,
    levels: Sequence[float],
    costs: CostModel | None = None,
    size_sol: float = 1.0,
) -> list[SweepRow]:
    """Prueba varios niveles de trailing sobre los mismos casos.

    Se barre en lugar de elegir un nivel a dedo: quedarse con el que mejor sale y presentarlo
    como "el bueno" es sobreajustar a un punado de casos.
    """
    model = costs or CostModel()
    rows: list[SweepRow] = []

    for level in levels:
        profits: list[float] = []
        captured = 0
        for series in cases:
            try:
                result = backtest_series(
                    series, trailing=level, costs=model, size_sol=size_sol
                )
            except ValueError:
                # Una serie inservible se descarta; no invalida el barrido entero.
                continue
            profits.append(result.profit_sol)
            captured += int(result.captured_peak)

        if not profits:
            rows.append(SweepRow(level, 0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0))
            continue

        rows.append(
            SweepRow(
                trailing=level,
                cases=len(profits),
                winners=sum(1 for p in profits if p > 0),
                losers=sum(1 for p in profits if p < 0),
                total_sol=sum(profits),
                mean_sol=statistics.mean(profits),
                median_sol=statistics.median(profits),
                worst_sol=min(profits),
                best_sol=max(profits),
                captured_peak=captured,
            )
        )
    return rows
