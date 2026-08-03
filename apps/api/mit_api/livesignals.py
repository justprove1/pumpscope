"""Senales de comportamiento observadas en vivo: ballenas, prerrebotes y flujo.

Todo lo de aqui es OBSERVABLE, no predictivo. Se mide lo que esta pasando en la cadena; no
se afirma lo que va a pasar. La distincion es la misma de siempre: una senal, no una promesa.

Lo que NO esta aqui, y se dice claro: aprender patrones de miles de tokens para predecir es
el modelo de ML (Fase 5), y necesita el corpus que aun se esta capturando. Este modulo da
comparacion descriptiva, no un pronostico entrenado.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from mit_pumpfun.events import TradeEvent


@dataclass(frozen=True, slots=True)
class WhaleAlert:
    """Actividad de una wallet grande, observada."""

    present: bool
    wallet: str
    sol_amount: float
    share_of_volume: float
    direction: str  # "acumulando" | "vendiendo"
    detail: str


def detect_whale(events: list[TradeEvent]) -> WhaleAlert:
    """Detecta si una wallet mueve un tamano desproporcionado.

    "Ballena" no es un tamano absoluto: en un token de 30 SOL de liquidez, 2 SOL ya mueve el
    precio. Se mide RELATIVO a la operacion tipica del propio token. Una compra 5 veces mayor
    que la mediana, o una wallet que concentra buena parte del volumen reciente, es senal.
    """
    if len(events) < 4:
        return WhaleAlert(False, "", 0.0, 0.0, "", "muestra insuficiente")

    amounts = [e.sol_amount for e in events if e.sol_amount > 0]
    if not amounts:
        return WhaleAlert(False, "", 0.0, 0.0, "", "sin importes")
    median = statistics.median(amounts)
    total = sum(amounts)

    # Volumen por wallet en la ventana observada.
    by_wallet: dict[str, float] = {}
    dir_by_wallet: dict[str, int] = {}
    for e in events:
        by_wallet[e.user] = by_wallet.get(e.user, 0.0) + e.sol_amount
        dir_by_wallet[e.user] = dir_by_wallet.get(e.user, 0) + (1 if e.is_buy else -1)

    wallet = max(by_wallet, key=lambda w: by_wallet[w])
    volume = by_wallet[wallet]
    share = volume / total if total > 0 else 0.0
    biggest = max(events, key=lambda e: e.sol_amount)

    is_whale = share >= 0.35 or (median > 0 and biggest.sol_amount >= median * 5)
    if not is_whale:
        return WhaleAlert(False, "", 0.0, 0.0, "", "sin concentracion anomala")

    direction = "acumulando" if dir_by_wallet.get(wallet, 0) > 0 else "vendiendo"
    return WhaleAlert(
        present=True,
        wallet=wallet,
        sol_amount=round(volume / 1_000_000_000, 6),
        share_of_volume=round(share * 100, 1),
        direction=direction,
        detail=(
            f"{wallet[:8]}... {direction}, {share * 100:.0f}% del volumen reciente "
            f"({volume / 1_000_000_000:.3f} SOL)"
        ),
    )


@dataclass(frozen=True, slots=True)
class PreBounce:
    """Patron de posible rebote: cayo, toco fondo y vuelve a haber compras."""

    present: bool
    drop_pct: float
    recovery_pct: float
    detail: str


def detect_pre_bounce(events: list[TradeEvent]) -> PreBounce:
    """Detecta el patron de PRERREBOTE, observado, no predicho.

    La forma: el precio hizo un minimo local, y despues de ese minimo las compras han vuelto.
    NO dice que va a rebotar —un token puede seguir cayendo—; dice que AHORA tiene la forma de
    los que rebotan. Es informacion para el ojo, con su cifra.
    """
    if len(events) < 6:
        return PreBounce(False, 0.0, 0.0, "muestra insuficiente para un patron")

    ordered = sorted(events, key=lambda e: e.timestamp)
    prices = [
        e.virtual_sol_reserves / e.virtual_token_reserves
        for e in ordered
        if e.virtual_token_reserves > 0
    ]
    if len(prices) < 6:
        return PreBounce(False, 0.0, 0.0, "sin precios suficientes")

    peak = max(prices)
    trough_index = min(range(len(prices)), key=lambda i: prices[i])
    trough = prices[trough_index]
    current = prices[-1]

    drop = (peak - trough) / peak if peak > 0 else 0.0
    recovery = (current - trough) / trough if trough > 0 else 0.0

    # El minimo no puede ser justo el ultimo punto (entonces sigue cayendo, no rebota).
    recent_buys = sum(1 for e in ordered[trough_index:] if e.is_buy)
    recent_total = max(1, len(ordered) - trough_index)
    buy_ratio = recent_buys / recent_total

    is_bounce = (
        drop >= 0.15 and recovery >= 0.05 and buy_ratio >= 0.55 and trough_index < len(prices) - 2
    )
    if not is_bounce:
        return PreBounce(
            False, round(drop * 100, 1), round(recovery * 100, 1), "sin patron de rebote"
        )
    return PreBounce(
        present=True,
        drop_pct=round(drop * 100, 1),
        recovery_pct=round(recovery * 100, 1),
        detail=(
            f"cayo {drop * 100:.0f}% hasta un minimo y recupera {recovery * 100:.0f}% con "
            f"{buy_ratio * 100:.0f}% de compras desde el fondo"
        ),
    )


def flow_metrics(events: list[TradeEvent]) -> dict[str, object]:
    """Metricas de flujo: todo lo observable que quepa, con sus cifras."""
    if not events:
        return {}
    ordered = sorted(events, key=lambda e: e.timestamp)
    buys = [e for e in events if e.is_buy]
    sells = [e for e in events if not e.is_buy]
    sol_in = sum(e.sol_amount for e in buys) / 1_000_000_000
    sol_out = sum(e.sol_amount for e in sells) / 1_000_000_000
    span = max(1, ordered[-1].timestamp - ordered[0].timestamp)

    # Aceleracion: operaciones en la ultima mitad frente a la primera.
    mid = ordered[len(ordered) // 2].timestamp
    recent = sum(1 for e in ordered if e.timestamp >= mid)
    early = max(1, len(ordered) - recent)
    acceleration = recent / early

    biggest = max(events, key=lambda e: e.sol_amount)
    return {
        "net_flow_sol": round(sol_in - sol_out, 6),
        "sol_in": round(sol_in, 6),
        "sol_out": round(sol_out, 6),
        "buy_sell_ratio": round(len(buys) / max(1, len(sells)), 2),
        "trades_per_minute": round(len(events) / (span / 60), 1),
        "acceleration": round(acceleration, 2),
        "largest_trade_sol": round(biggest.sol_amount / 1_000_000_000, 6),
        "largest_trade_side": "compra" if biggest.is_buy else "venta",
        "avg_trade_sol": round(sum(e.sol_amount for e in events) / len(events) / 1_000_000_000, 6),
        "window_seconds": span,
    }
