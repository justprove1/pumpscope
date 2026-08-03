"""Indicador de traccion: cuanto EMPUJE tiene un token ahora mismo.

**No es una prediccion de exito.** Que un token llegue a graduarse o se haga famoso depende
de cosas que no se pueden medir on-chain: que un influencer lo mencione, la narrativa del
momento, suerte. Un modelo que dijera "este llega a 500 SOL" estaria inventando.

Lo que SI se puede medir es si un token se COMPORTA ahora como los que despegan: compras
rapidas, mas compras que ventas, muchos compradores distintos (no cuatro bots), y avance real
hacia la graduacion. Eso es traccion observada, y es lo unico honesto que se puede dar sin un
modelo entrenado.

Devuelve un 0-100 con su desglose. Un valor alto dice "tiene empuje AHORA", no "va a ganar".
La distincion no es un matiz: es la diferencia entre una senal y una promesa.
"""

from __future__ import annotations

from dataclasses import dataclass

from mit_pumpfun.curve import CurveState, progress_pct, sol_to_complete
from mit_pumpfun.events import TradeEvent


@dataclass(frozen=True, slots=True)
class TractionSignal:
    name: str
    value: float
    weight: float
    detail: str

    @property
    def contribution(self) -> float:
        return self.value * self.weight


@dataclass(frozen=True, slots=True)
class TractionEstimate:
    """Estimacion de traccion con su desglose completo."""

    score: float
    label: str
    signals: tuple[TractionSignal, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "score": round(self.score, 1),
            "label": self.label,
            "signals": [
                {
                    "name": s.name,
                    "value": round(s.value, 3),
                    "weight": s.weight,
                    "detail": s.detail,
                }
                for s in self.signals
            ],
        }


def _label(score: float) -> str:
    if score >= 70:
        return "empuje fuerte"
    if score >= 50:
        return "empuje moderado"
    if score >= 30:
        return "empuje debil"
    return "sin traccion"


def estimate_traction(events: list[TradeEvent], curve: CurveState | None) -> TractionEstimate:
    """Combina senales observables en un 0-100.

    Con menos de tres operaciones no se estima: un token de cinco segundos no tiene traccion
    que medir, y ponerle un numero seria inventar.
    """
    if len(events) < 3:
        return TractionEstimate(
            score=0.0,
            label="sin datos suficientes",
            signals=(
                TractionSignal(
                    "muestra",
                    0.0,
                    0.0,
                    f"solo {len(events)} operaciones: aun no hay traccion que medir",
                ),
            ),
        )

    ordered = sorted(events, key=lambda e: e.timestamp)
    span = max(1, ordered[-1].timestamp - ordered[0].timestamp)
    buys = sum(1 for e in events if e.is_buy)
    sells = len(events) - buys
    traders = len({e.user for e in events})

    # 1. Velocidad de operaciones por minuto. Se satura en 60/min: mas ya es frenesi.
    trades_per_min = len(events) / (span / 60)
    velocity = min(1.0, trades_per_min / 60)

    # 2. Presion compradora. 1.0 = solo compras, 0.0 = solo ventas.
    pressure = buys / len(events)

    # 3. Diversidad de traders. Cuatro compradores no son una comunidad; treinta empiezan a
    #    serlo. Es la senal que mas separa un lanzamiento real de un bundle.
    diversity = min(1.0, traders / 30)

    # 4. Avance hacia la graduacion: cuanto de la curva ya se ha recorrido de verdad.
    graduation = 0.0
    graduation_detail = "sin datos de curva"
    if curve is not None:
        pct = float(progress_pct(curve)) / 100
        graduation = min(1.0, pct)
        remaining = sol_to_complete(curve) / 1_000_000_000
        graduation_detail = f"{pct * 100:.1f}% recorrido, faltan {remaining:.1f} SOL"

    # 5. Sostenibilidad: que las compras no se hayan parado. Se compara la ultima mitad de la
    #    ventana con la primera. Un token que compraba mucho y ya no, esta perdiendo empuje.
    mid = ordered[len(ordered) // 2].timestamp
    recent = sum(1 for e in ordered if e.timestamp >= mid and e.is_buy)
    early = sum(1 for e in ordered if e.timestamp < mid and e.is_buy)
    momentum = min(1.0, recent / early) if early > 0 else (1.0 if recent else 0.0)

    signals = (
        TractionSignal("velocidad", velocity, 0.25, f"{trades_per_min:.0f} operaciones/min"),
        TractionSignal("presion_compradora", pressure, 0.25, f"{buys} compras / {sells} ventas"),
        TractionSignal("diversidad", diversity, 0.20, f"{traders} traders distintos"),
        TractionSignal("graduacion", graduation, 0.15, graduation_detail),
        TractionSignal("sostenibilidad", momentum, 0.15, "compras recientes vs iniciales"),
    )
    score = sum(s.contribution for s in signals) * 100
    return TractionEstimate(score=score, label=_label(score), signals=signals)
