"""Integridad del token: autoridades, honeypot y metadata fraudulenta (SPEC.md 8)."""

from __future__ import annotations

import re

from mit_strategies.manipulation.types import Finding, Severity, TokenContext

# Marcas y figuras publicas que se suplantan constantemente en memecoins.
IMPERSONATION_PATTERNS = (
    r"\bofficial\b",
    r"\bverified\b",
    r"\belon\b",
    r"\btesla\b",
    r"\bspacex\b",
    r"\btrump\b",
    r"\bapple\b",
    r"\bnvidia\b",
    r"\bopenai\b",
    r"\bbinance\b",
)


def detect_impersonation(context: TokenContext) -> list[Finding]:
    """Nombre o simbolo que se apropian de una marca o figura publica.

    NO es prueba de fraude por si solo: hay memes legitimos sobre personajes publicos. Es una
    senal que suma, no un veto. Reclamar ser "oficial" o "verificado" si es especialmente
    sospechoso, porque un proyecto realmente oficial no necesita decirlo en su simbolo.
    """
    haystack = f"{context.name} {context.symbol}".lower()
    hits = [p for p in IMPERSONATION_PATTERNS if re.search(p, haystack)]
    if not hits:
        return []
    claims_authenticity = any(p in (r"\bofficial\b", r"\bverified\b") for p in hits)
    return [
        Finding(
            detector="impersonation",
            severity=Severity.MEDIUM if claims_authenticity else Severity.LOW,
            reason=(
                f"El nombre/simbolo ({context.name!r} / {context.symbol!r}) coincide con "
                f"{len(hits)} patron(es) de suplantacion de marca o figura publica"
            ),
            evidence={"matches": len(hits), "name": context.name, "symbol": context.symbol},
        )
    ]


def detect_metadata_anomalies(context: TokenContext) -> list[Finding]:
    """Metadata ausente o sospechosa."""
    findings: list[Finding] = []
    if not context.uri:
        findings.append(
            Finding(
                detector="metadata_missing",
                severity=Severity.LOW,
                reason="El token no declara URI de metadata",
                evidence={"uri": ""},
            )
        )
    elif not context.uri.startswith(("https://", "ipfs://", "ar://")):
        findings.append(
            Finding(
                detector="metadata_insecure_uri",
                severity=Severity.LOW,
                reason=f"La URI de metadata no usa un esquema seguro: {context.uri[:40]!r}",
                evidence={"uri": context.uri[:120]},
            )
        )
    return findings


# Por debajo de este censo, "el top 10 concentra el X%" es aritmetica, no manipulacion: con
# 12 holders el top 10 tiene por fuerza mas del 80%. Exigir un minimo evita el falso positivo
# que marcaria como sospechoso todo token recien creado.
MIN_HOLDERS_FOR_CONCENTRATION = 25


def detect_supply_concentration(context: TokenContext) -> list[Finding]:
    """Concentracion de supply excluyendo pools y programas.

    Se calcula sobre los holders REALES: incluir el pool subestima siempre.

    No dice nada si hay pocos holders. Un token de dos minutos con 12 tenedores siempre
    parecera concentrado, y acusarlo de manipulacion seria ruido que ademas entrenaria al
    operador a ignorar el detector.
    """
    real = {
        address: amount
        for address, amount in context.holders.items()
        if amount > 0
        and not context.wallet_info(address).is_pool
        and not context.wallet_info(address).is_program
    }
    total = sum(real.values())
    if total <= 0 or len(real) < MIN_HOLDERS_FOR_CONCENTRATION:
        return []

    ordered = sorted(real.values(), reverse=True)
    top10_pct = sum(ordered[:10]) / total * 100
    if top10_pct < 50:
        return []
    return [
        Finding(
            detector="supply_concentration",
            severity=Severity.CRITICAL if top10_pct >= 80 else Severity.HIGH,
            reason=(
                f"Los 10 mayores holders concentran el {top10_pct:.0f}% del supply "
                f"(excluyendo pools y programas)"
            ),
            evidence={"top10_pct": round(top10_pct, 2), "holders": len(real)},
        )
    ]
