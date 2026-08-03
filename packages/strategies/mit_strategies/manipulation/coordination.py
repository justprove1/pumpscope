"""Bundles y compras coordinadas (SPEC.md 8).

Todo se deduce de datos on-chain: no hace falta ninguna API de pago.

La senal mas fuerte de un lanzamiento amanado es temporal: varias wallets comprando en el
MISMO slot. Un slot dura ~400 ms; que cinco desconocidos coincidan ahi por casualidad, en el
primer instante de un token que nadie conoce todavia, no es casualidad.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from mit_strategies.manipulation.types import Finding, Severity, TokenContext

# Un slot de Solana dura ~400 ms. Coincidir en uno es coincidir en el mismo pestaneo.
MIN_WALLETS_SAME_SLOT = 3
MIN_IDENTICAL_AMOUNT_WALLETS = 3
FRESH_WALLET_SECONDS = 3600


def detect_same_slot_bundles(context: TokenContext) -> list[Finding]:
    """Varias wallets distintas comprando en el mismo slot."""
    by_slot: dict[int, set[str]] = defaultdict(set)
    for trade in context.buys:
        by_slot[trade.slot].add(trade.wallet)

    findings: list[Finding] = []
    for slot, wallets in sorted(by_slot.items()):
        if len(wallets) < MIN_WALLETS_SAME_SLOT:
            continue
        severity = Severity.HIGH if len(wallets) >= 5 else Severity.MEDIUM
        findings.append(
            Finding(
                detector="same_slot_bundle",
                severity=severity,
                reason=(
                    f"{len(wallets)} wallets distintas compraron en el mismo slot ({slot}); "
                    f"un slot dura ~400 ms"
                ),
                evidence={"slot": slot, "wallet_count": len(wallets)},
            )
        )
    return findings


def detect_identical_amounts(context: TokenContext) -> list[Finding]:
    """Importes idénticos repetidos desde wallets distintas.

    Un humano no compra dos veces exactamente 0,137492 SOL. Un script si.
    """
    counter: Counter[int] = Counter()
    wallets_by_amount: dict[int, set[str]] = defaultdict(set)
    for trade in context.buys:
        counter[trade.sol_amount] += 1
        wallets_by_amount[trade.sol_amount].add(trade.wallet)

    findings: list[Finding] = []
    for amount, wallets in wallets_by_amount.items():
        if len(wallets) < MIN_IDENTICAL_AMOUNT_WALLETS or amount <= 0:
            continue
        pct = len(wallets) / max(1, len({t.wallet for t in context.buys})) * 100
        findings.append(
            Finding(
                detector="identical_amounts",
                severity=Severity.MEDIUM if pct < 50 else Severity.HIGH,
                reason=(
                    f"{len(wallets)} wallets compraron exactamente el mismo importe "
                    f"({amount / 1e9:.6f} SOL), el {pct:.0f}% de los compradores"
                ),
                evidence={"lamports": amount, "wallet_count": len(wallets), "pct": round(pct, 2)},
            )
        )
    return findings


def detect_fresh_wallet_cohort(context: TokenContext) -> list[Finding]:
    """Compradores tempranos con wallets recien creadas."""
    buyers = {t.wallet for t in context.buys}
    if not buyers:
        return []

    fresh = []
    for wallet in buyers:
        info = context.wallet_info(wallet)
        if info.first_seen_at is None:
            continue
        age = (context.created_at - info.first_seen_at).total_seconds()
        if age < FRESH_WALLET_SECONDS:
            fresh.append(wallet)

    if not fresh:
        return []
    pct = len(fresh) / len(buyers) * 100
    if pct < 30:
        return []
    return [
        Finding(
            detector="fresh_wallet_cohort",
            severity=Severity.HIGH if pct >= 60 else Severity.MEDIUM,
            reason=(
                f"{len(fresh)} de {len(buyers)} compradores ({pct:.0f}%) usan wallets creadas "
                f"menos de {FRESH_WALLET_SECONDS // 60} minutos antes del lanzamiento"
            ),
            evidence={"fresh": len(fresh), "buyers": len(buyers), "pct": round(pct, 2)},
        )
    ]
