"""Metricas de concentracion de holders (SPEC.md 7).

Todas son funciones puras sobre una lista de saldos: mismos saldos, mismo resultado, siempre.
Eso las hace testeables con property tests y reproducibles en una auditoria.

**La decision que mas cambia el resultado: que se excluye.** Los pools de liquidez, los
programas y las cuentas identificadas NO son holders en el sentido que importa. Incluir el
pool en la concentracion la subestima siempre, y hace pasar por sano un token cuyo supply
esta en tres manos. Por eso las funciones reciben los saldos YA filtrados y existe
`exclude_known_accounts` para hacerlo explicito.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

PERCENT = Decimal(100)


@dataclass(frozen=True, slots=True)
class ConcentrationMetrics:
    """Distribucion del supply entre holders.

    Los tres indices miden cosas distintas y por eso se reportan los tres:

    - **HHI**: sensible a los MUY grandes. Detecta una ballena dominante.
    - **Gini**: mide desigualdad global. Detecta muchos pequenos y pocos enormes.
    - **Entropia**: mide dispersion. Cae en picado cuando el supply se agrupa.

    Un token puede tener Gini alto y HHI bajo (muchos medianos) o al reves. Reducirlos a un
    solo numero pierde justo la informacion que distingue los dos casos.
    """

    holder_count: int
    top1_pct: Decimal
    top5_pct: Decimal
    top10_pct: Decimal
    top20_pct: Decimal
    hhi: Decimal
    gini: Decimal
    entropy: Decimal
    normalized_entropy: Decimal

    def as_dict(self) -> dict[str, float]:
        return {
            "holder_count": float(self.holder_count),
            "top1_pct": float(self.top1_pct),
            "top5_pct": float(self.top5_pct),
            "top10_pct": float(self.top10_pct),
            "top20_pct": float(self.top20_pct),
            "hhi": float(self.hhi),
            "gini": float(self.gini),
            "entropy": float(self.entropy),
            "normalized_entropy": float(self.normalized_entropy),
        }


def exclude_known_accounts(balances: Mapping[str, int], excluded: Iterable[str]) -> dict[str, int]:
    """Quita pools, programas y cuentas identificadas antes de medir concentracion."""
    blocked = set(excluded)
    return {address: amount for address, amount in balances.items() if address not in blocked}


def _shares(balances: Sequence[int]) -> list[Decimal]:
    """Fracciones del total, descendentes. Ignora saldos no positivos."""
    positive = sorted((b for b in balances if b > 0), reverse=True)
    total = sum(positive)
    if total <= 0:
        return []
    return [Decimal(b) / Decimal(total) for b in positive]


def top_n_pct(balances: Sequence[int], n: int) -> Decimal:
    """Porcentaje del supply en manos de los `n` mayores."""
    shares = _shares(balances)
    if not shares:
        return Decimal(0)
    return sum(shares[:n], Decimal(0)) * PERCENT


def herfindahl(balances: Sequence[int]) -> Decimal:
    """Indice Herfindahl-Hirschman: suma de cuadrados de las fracciones.

    Rango (0, 1]. Un unico holder da 1. Mil holders iguales dan 0,001.
    """
    shares = _shares(balances)
    if not shares:
        return Decimal(0)
    return sum((s * s for s in shares), Decimal(0))


def gini(balances: Sequence[int]) -> Decimal:
    """Coeficiente de Gini. Rango [0, 1]: 0 es reparto perfecto, 1 concentracion total.

    Formula por rangos sobre los saldos ordenados de forma ASCENDENTE, que es exacta y no
    requiere integrar la curva de Lorenz.
    """
    positive = sorted(b for b in balances if b > 0)
    n = len(positive)
    if n <= 1:
        # Con un solo holder la desigualdad es maxima; con ninguno, no esta definida.
        return Decimal(1) if n == 1 else Decimal(0)
    total = Decimal(sum(positive))
    weighted = sum(Decimal(i + 1) * Decimal(b) for i, b in enumerate(positive))
    value = (2 * weighted) / (Decimal(n) * total) - Decimal(n + 1) / Decimal(n)
    return max(Decimal(0), min(Decimal(1), value))


def entropy(balances: Sequence[int]) -> Decimal:
    """Entropia de Shannon en bits sobre la distribucion de saldos."""
    shares = _shares(balances)
    if not shares:
        return Decimal(0)
    total = Decimal(0)
    for share in shares:
        if share > 0:
            total -= share * Decimal(math.log2(float(share)))
    return total


def normalized_entropy(balances: Sequence[int]) -> Decimal:
    """Entropia dividida por su maximo posible. Rango [0, 1], comparable entre tokens.

    La entropia cruda depende del numero de holders, asi que comparar dos tokens con censos
    distintos usando la cruda no dice nada. Esta si es comparable.
    """
    shares = _shares(balances)
    if len(shares) <= 1:
        return Decimal(0)
    maximum = Decimal(math.log2(len(shares)))
    if maximum <= 0:
        return Decimal(0)
    return max(Decimal(0), min(Decimal(1), entropy(balances) / maximum))


def concentration(balances: Sequence[int]) -> ConcentrationMetrics:
    """Calcula todas las metricas de una vez."""
    positive = [b for b in balances if b > 0]
    return ConcentrationMetrics(
        holder_count=len(positive),
        top1_pct=top_n_pct(positive, 1),
        top5_pct=top_n_pct(positive, 5),
        top10_pct=top_n_pct(positive, 10),
        top20_pct=top_n_pct(positive, 20),
        hhi=herfindahl(positive),
        gini=gini(positive),
        entropy=entropy(positive),
        normalized_entropy=normalized_entropy(positive),
    )
