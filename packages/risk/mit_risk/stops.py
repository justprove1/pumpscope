"""Los nueve stops de SPEC.md 14.

Se evaluan TODOS en cada tick y **el primero que dispara manda**. No se promedian ni se vota:
en una salida, la senal mas conservadora es la que vale.

El orden de evaluacion es el orden de prioridad, y no es arbitrario: primero lo que hace
imposible salir (liquidez), luego lo que ya perdio (hard stop), y solo despues lo discrecional.
Si el hard stop fuera primero, una posicion sin liquidez esperaria a perder el porcentaje
pactado antes de intentar salir, cuando el problema es que no puede salir.
"""

from __future__ import annotations

from dataclasses import dataclass

from mit_risk.types import StopType


@dataclass(frozen=True, slots=True)
class StopConfig:
    """Umbrales de cada stop. Sin calibrar: punto de partida de RISK_POLICY.md."""

    hard_stop_loss_fraction: float = 0.30
    soft_stop_score_drop: float = 25.0
    trailing_drawdown_fraction: float = 0.25
    max_hold_seconds: int = 3600
    min_exit_liquidity_lamports: int = 2_000_000_000
    break_even_trigger_fraction: float = 0.20
    partial_take_profit_fraction: float = 0.20


@dataclass(frozen=True, slots=True)
class PositionState:
    """Estado de la posicion en el instante de evaluar."""

    unrealized_return: float = 0.0
    max_favorable_return: float = 0.0
    held_seconds: int = 0
    entry_score: float = 100.0
    current_score: float = 100.0
    exit_liquidity_lamports: int = 10_000_000_000
    narrative_exhausted: bool = False
    whale_exiting: bool = False
    partial_taken: bool = False
    break_even_armed: bool = False


@dataclass(frozen=True, slots=True)
class StopTrigger:
    """Un stop disparado, con su razon y sus cifras."""

    stop: StopType
    reason: str
    exit_fraction: float = 1.0


def evaluate_stops(position: PositionState, config: StopConfig | None = None) -> StopTrigger | None:
    """Devuelve el primer stop que dispara, o `None`.

    Determinista: mismo estado, mismo stop, siempre.
    """
    c = config or StopConfig()

    # 1. Sin liquidez para salir, todo lo demas es irrelevante.
    if position.exit_liquidity_lamports < c.min_exit_liquidity_lamports:
        return StopTrigger(
            StopType.LIQUIDITY,
            f"liquidez de salida {position.exit_liquidity_lamports / 1e9:.3f} SOL por debajo "
            f"del minimo {c.min_exit_liquidity_lamports / 1e9:.3f} SOL",
        )

    # 2. Perdida absoluta pactada.
    if position.unrealized_return <= -c.hard_stop_loss_fraction:
        return StopTrigger(
            StopType.HARD,
            f"perdida {position.unrealized_return:.1%} alcanza el stop "
            f"{-c.hard_stop_loss_fraction:.1%}",
        )

    # 3. Alguien que sabe mas esta saliendo.
    if position.whale_exiting:
        return StopTrigger(StopType.WHALE_EXIT, "un whale o el creador esta vendiendo")

    # 4. La razon por la que se entro ya no existe.
    if position.narrative_exhausted:
        return StopTrigger(StopType.NARRATIVE, "la narrativa se ha agotado")

    # 5. Trailing sobre el maximo favorable alcanzado.
    if position.max_favorable_return > 0:
        giveback = position.max_favorable_return - position.unrealized_return
        if giveback >= c.trailing_drawdown_fraction * position.max_favorable_return:
            return StopTrigger(
                StopType.TRAILING,
                f"devuelto {giveback:.1%} desde el maximo {position.max_favorable_return:.1%}",
            )

    # 6. La tesis se degrado aunque el precio aun no lo refleje.
    if position.entry_score - position.current_score >= c.soft_stop_score_drop:
        return StopTrigger(
            StopType.SOFT,
            f"el score cayo de {position.entry_score:.0f} a {position.current_score:.0f}",
        )

    # 7. Toma parcial de beneficio.
    if not position.partial_taken and position.unrealized_return >= c.partial_take_profit_fraction:
        return StopTrigger(
            StopType.PARTIAL_TAKE_PROFIT,
            f"beneficio {position.unrealized_return:.1%} alcanza la toma parcial",
            exit_fraction=0.25,
        )

    # 8. Tras la primera toma, el stop sube a coste.
    if (
        position.partial_taken
        and not position.break_even_armed
        and position.unrealized_return >= c.break_even_trigger_fraction
    ):
        return StopTrigger(
            StopType.BREAK_EVEN, "stop movido a coste tras la toma parcial", exit_fraction=0.0
        )

    # 9. Se acabo el tiempo.
    if position.held_seconds >= c.max_hold_seconds:
        return StopTrigger(
            StopType.TIME,
            f"{position.held_seconds}s en posicion supera el maximo {c.max_hold_seconds}s",
        )
    return None
