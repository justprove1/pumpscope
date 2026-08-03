"""Wallets financiadas por una misma fuente: sybil y wallet splitting (SPEC.md 8).

Se recorre hacia atras el grafo de financiacion. Si veinte wallets "independientes" recibieron
su primer SOL de la misma direccion, no son veinte participantes: son uno.

Es el detector que produce la frase del ejemplo de SPEC.md 8: "31% del supply pertenece a
wallets financiadas por la misma direccion".
"""

from __future__ import annotations

from collections import defaultdict

from mit_strategies.manipulation.types import Finding, Severity, TokenContext

MIN_CLUSTER_SIZE = 3


def _clusters_by_funder(context: TokenContext) -> dict[str, set[str]]:
    clusters: dict[str, set[str]] = defaultdict(set)
    for address, info in context.wallets.items():
        if info.funded_by:
            clusters[info.funded_by].add(address)
    return clusters


def detect_common_funding(context: TokenContext) -> list[Finding]:
    """Wallets con la misma fuente de financiacion que ademas tienen supply."""
    total_supply = sum(context.holders.values())
    findings: list[Finding] = []

    for funder, members in _clusters_by_funder(context).items():
        if len(members) < MIN_CLUSTER_SIZE:
            continue
        held = sum(context.holders.get(w, 0) for w in members)
        pct = (held / total_supply * 100) if total_supply > 0 else 0.0
        if pct < 5:
            continue
        severity = (
            Severity.CRITICAL if pct >= 30 else (Severity.HIGH if pct >= 15 else Severity.MEDIUM)
        )
        findings.append(
            Finding(
                detector="common_funding_cluster",
                severity=severity,
                reason=(
                    f"{pct:.0f}% del supply pertenece a {len(members)} wallets financiadas "
                    f"por la misma direccion ({funder[:8]}...)"
                ),
                evidence={
                    "funder": funder,
                    "wallets": len(members),
                    "supply_pct": round(pct, 2),
                },
            )
        )
    return findings


def detect_creator_funded_buyers(context: TokenContext) -> list[Finding]:
    """Compradores financiados por el propio creador. Es autocompra disfrazada."""
    buyers = {t.wallet for t in context.buys}
    funded = [w for w in buyers if context.wallet_info(w).funded_by == context.creator]
    if not funded:
        return []
    pct = len(funded) / len(buyers) * 100
    return [
        Finding(
            detector="creator_funded_buyers",
            severity=Severity.CRITICAL if pct >= 25 else Severity.HIGH,
            reason=(
                f"{len(funded)} de {len(buyers)} compradores ({pct:.0f}%) fueron financiados "
                f"por el propio creador"
            ),
            evidence={"funded": len(funded), "buyers": len(buyers), "pct": round(pct, 2)},
        )
    ]
