"""Wash trading, volumen artificial y creator dumping (SPEC.md 8)."""

from __future__ import annotations

from collections import defaultdict

from mit_strategies.manipulation.types import Finding, Severity, TokenContext

MIN_ROUNDTRIPS = 3


def detect_self_trading(context: TokenContext) -> list[Finding]:
    """Wallets que compran y venden repetidamente el mismo token.

    Genera volumen sin cambiar de manos. El volumen es la metrica que mas se mira y la mas
    facil de fabricar.
    """
    roundtrips: dict[str, dict[str, int]] = defaultdict(lambda: {"buy": 0, "sell": 0})
    for trade in context.trades:
        roundtrips[trade.wallet][trade.side] += 1

    findings: list[Finding] = []
    total_volume = sum(t.sol_amount for t in context.trades)
    for wallet, sides in roundtrips.items():
        cycles = min(sides["buy"], sides["sell"])
        if cycles < MIN_ROUNDTRIPS:
            continue
        volume = sum(t.sol_amount for t in context.trades if t.wallet == wallet)
        pct = (volume / total_volume * 100) if total_volume > 0 else 0.0
        findings.append(
            Finding(
                detector="self_trading",
                severity=Severity.HIGH if pct >= 30 else Severity.MEDIUM,
                reason=(
                    f"La wallet {wallet[:8]}... hizo {cycles} ciclos compra-venta y concentra "
                    f"el {pct:.0f}% del volumen"
                ),
                evidence={"wallet": wallet, "roundtrips": cycles, "volume_pct": round(pct, 2)},
            )
        )
    return findings


def detect_concentrated_volume(context: TokenContext) -> list[Finding]:
    """Volumen inicial concentrado en muy pocas wallets.

    Produce la frase del ejemplo de SPEC.md 8: "62% del volumen inicial proviene de 4 wallets".
    """
    if not context.trades:
        return []
    by_wallet: dict[str, int] = defaultdict(int)
    for trade in context.trades:
        by_wallet[trade.wallet] += trade.sol_amount
    total = sum(by_wallet.values())
    if total <= 0:
        return []

    top = sorted(by_wallet.values(), reverse=True)[:4]
    pct = sum(top) / total * 100
    if pct < 50 or len(by_wallet) < 4:
        return []
    return [
        Finding(
            detector="concentrated_volume",
            severity=Severity.HIGH if pct >= 70 else Severity.MEDIUM,
            reason=f"El {pct:.0f}% del volumen inicial proviene de 4 wallets",
            evidence={"top4_pct": round(pct, 2), "wallets": len(by_wallet)},
        )
    ]


def detect_creator_dumping(context: TokenContext) -> list[Finding]:
    """El creador vendiendo su propia bolsa.

    Es la senal mas directa que existe y no admite interpretacion benevolente: quien lanza un
    token y lo vende en los primeros minutos no esta construyendo nada.
    """
    creator_sells = [t for t in context.sells if t.wallet == context.creator]
    if not creator_sells:
        return []

    proceeds = sum(t.sol_amount for t in creator_sells)
    first = min(t.block_time for t in creator_sells)
    minutes = max(0.0, (first - context.created_at).total_seconds() / 60)

    severity = (
        Severity.CRITICAL
        if minutes <= 10
        else (Severity.HIGH if minutes <= 60 else Severity.MEDIUM)
    )
    return [
        Finding(
            detector="creator_dumping",
            severity=severity,
            reason=(
                f"El creador vendio {len(creator_sells)} veces por {proceeds / 1e9:.3f} SOL; "
                f"la primera venta a los {minutes:.0f} minutos del lanzamiento"
            ),
            evidence={
                "sell_count": len(creator_sells),
                "sol_proceeds": round(proceeds / 1e9, 6),
                "minutes_after_launch": round(minutes, 1),
            },
        )
    ]


def detect_creator_history(context: TokenContext) -> list[Finding]:
    """Historial del creador en lanzamientos anteriores.

    Produce la frase del ejemplo de SPEC.md 8: "el creador vendio agresivamente en 5 de sus
    ultimos 7 tokens".
    """
    if context.creator_previous_tokens < 2:
        return []
    dumps = context.creator_previous_dumps
    if dumps == 0:
        return []
    ratio = dumps / context.creator_previous_tokens
    if ratio < 0.4:
        return []
    return [
        Finding(
            detector="creator_history",
            severity=Severity.CRITICAL if ratio >= 0.7 else Severity.HIGH,
            reason=(
                f"El creador vendio agresivamente en {dumps} de sus ultimos "
                f"{context.creator_previous_tokens} tokens"
            ),
            evidence={
                "previous_tokens": context.creator_previous_tokens,
                "dumps": dumps,
                "ratio": round(ratio, 3),
            },
        )
    ]
