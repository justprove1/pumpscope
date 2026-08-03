"""Cola de analisis: enriquece, puntua y decide (Fases 2-4 cableadas al bucle en vivo).

**Por que una COLA y no analisis en linea.** Enriquecer un token cuesta ~15 llamadas RPC y
nacen ~25 tokens por minuto. Con el endpoint publico (4 req/s) analizar todo en vivo es
imposible: la cola se llenaria hasta reventar y el 429 llegaria en minutos.

Asi que se encola con PRIORIDAD y se descarta lo que no cabe. Descartar de forma explicita y
contada es honesto; intentar con todo y que el proveedor te corte es peor, porque entonces se
pierde tambien lo que si importaba.

El analisis NO genera ordenes. Produce scores, riesgo y una senal — y la senal lleva importe
cero salvo que el RiskEngine diga otra cosa. Aqui no se mueve dinero.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from mit_pumpfun.detector import DetectedToken
from mit_solana.rpc import SolanaRpc
from mit_strategies.eligibility import EligibilityInputs, evaluate
from mit_strategies.manipulation import analyze
from mit_strategies.scores import TokenScores, opportunity_score
from mit_strategies.signals import generate

from mit_worker.enrichment import EnrichmentLimits, enrich

LOGGER = logging.getLogger("mit.analysis")
CHANNEL_ANALYSIS = "mit:tokens.analysis"

# Cola corta a proposito: si se acumula, el analisis va tan por detras que deja de ser util.
# Mejor descartar y decirlo que servir un veredicto de hace veinte minutos.
QUEUE_SIZE = 12


@dataclass
class AnalysisStats:
    queued: int = 0
    dropped: int = 0
    analyzed: int = 0
    failed: int = 0


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    """Veredicto completo de un token."""

    mint: str
    opportunity: float
    manipulation_risk: float
    holders: int
    top10_pct: float | None
    signal: str
    eligible: bool
    reasons: tuple[str, ...]
    partial: bool
    analyzed_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def as_dict(self) -> dict[str, object]:
        return {
            "mint": self.mint,
            "opportunity": round(self.opportunity, 1),
            "manipulation_risk": self.manipulation_risk,
            "holders": self.holders,
            "top10_pct": round(self.top10_pct, 1) if self.top10_pct is not None else None,
            "signal": self.signal,
            "eligible": self.eligible,
            "reasons": list(self.reasons),
            "partial": self.partial,
            "analyzed_at": self.analyzed_at.isoformat(),
        }


def _scores_from(
    report_score: float, metrics: object, holders: int, *, curve_known: bool = False
) -> TokenScores:
    """Traduce lo medido a los 13 scores.

    Solo se rellenan los que se pueden MEDIR con datos on-chain. Los que dependen de datos
    sociales quedan a 0 en vez de inventarse un valor neutro: "sin datos" y "medio" son cosas
    distintas, y rellenarlos falsearia el OpportunityScore hacia arriba.
    """
    top10 = float(getattr(metrics, "top10_pct", 0.0) or 0.0)
    distribution = max(0.0, 100.0 - top10)
    holder_quality = min(100.0, holders * 2.0)
    return TokenScores(
        # La liquidez inicial de la curva es conocida y comparable entre tokens.
        liquidity=80.0 if curve_known else 0.0,
        distribution=distribution,
        holder_quality=holder_quality,
        manipulation_risk=float(report_score),
        # RugRisk aun no tiene modulo propio: se deriva del riesgo de manipulacion, que es
        # su mejor proxy observable. Queda declarado como aproximacion.
        rug_risk=float(report_score) * 0.8,
        # La confianza sube con lo que se pudo medir DE VERDAD. Un token recien nacido no
        # tiene censo de holders enumerable todavia, y eso NO es lo mismo que un token
        # opaco: la curva es un dato solido y verificado aunque el censo aun no exista.
        # Con la version anterior todo token nuevo caia por falta de confianza y el
        # veredicto no distinguia nada.
        data_confidence=(60.0 if holders else 45.0) + (15.0 if curve_known else 0.0),
    )


class AnalysisPipeline:
    """Consume tokens detectados y produce veredictos."""

    def __init__(self, rpc: SolanaRpc, redis: object | None = None) -> None:
        self._rpc = rpc
        self._redis = redis
        self._queue: asyncio.Queue[DetectedToken] = asyncio.Queue(maxsize=QUEUE_SIZE)
        self.stats = AnalysisStats()

    def submit(self, token: DetectedToken) -> None:
        """Encola sin bloquear. Si esta llena, DESCARTA y lo cuenta."""
        try:
            self._queue.put_nowait(token)
            self.stats.queued += 1
        except asyncio.QueueFull:
            self.stats.dropped += 1

    async def _analyze(self, token: DetectedToken) -> AnalysisResult:
        result = await enrich(
            self._rpc,
            token.mint,
            token.event.creator,
            token.received_timestamp,
            name=token.event.name,
            symbol=token.event.symbol,
            uri=token.event.uri,
            # Presupuesto MINIMO: el analisis de fondo es secundario frente al visor en
            # vivo. Con menos llamadas por token, el RPC publico no se satura y lo que el
            # usuario mira carga sin competir.
            limits=EnrichmentLimits(max_transactions=3, max_creator_transactions=2),
        )
        report = analyze(result.context)
        metrics = result.concentration_metrics
        holders = metrics.holder_count if metrics else 0
        scores = _scores_from(
            report.score,
            metrics,
            holders,
            curve_known=token.event.virtual_sol_reserves > 0,
        )
        breakdown = opportunity_score(scores)

        eligibility = evaluate(
            EligibilityInputs(
                data_confidence=scores.data_confidence,
                rug_risk=scores.rug_risk,
                manipulation_risk=scores.manipulation_risk,
                top10_pct_adjusted=float(metrics.top10_pct) if metrics else 0.0,
                # La liquidez sale de las reservas virtuales del propio CreateEvent, que ya
                # tenemos sin una sola llamada extra. Dejarla en cero hacia que TODO token
                # cayera por el mismo veto y el veredicto no distinguiera nada.
                liquidity_lamports=token.event.virtual_sol_reserves,
            )
        )
        signal = generate(
            timestamp=datetime.now(UTC),
            mint=token.mint,
            breakdown=breakdown,
            eligibility=eligibility,
            confidence=0.5,
            recommended_size_lamports=0,
        )
        return AnalysisResult(
            mint=token.mint,
            opportunity=breakdown.opportunity,
            manipulation_risk=report.score,
            holders=holders,
            top10_pct=float(metrics.top10_pct) if metrics else None,
            signal=signal.signal_type.value,
            eligible=eligibility.eligible,
            reasons=tuple(report.reasons) + tuple(v.reason for v in eligibility.vetoes),
            partial=result.partial,
        )

    async def run(self) -> None:
        """Bucle de consumo. Un fallo en un token no para la cola.

        Con una pausa deliberada entre tokens: el analisis de fondo va despacio a proposito
        para dejar el RPC libre al visor en vivo, que es lo que el usuario esta mirando.
        """
        while True:
            token = await self._queue.get()
            await asyncio.sleep(3.0)
            try:
                verdict = await self._analyze(token)
                self.stats.analyzed += 1
                LOGGER.info(json.dumps({"event": "token_analyzed", **verdict.as_dict()}))
                if self._redis is not None:
                    with contextlib.suppress(Exception):
                        await self._redis.publish(  # type: ignore[attr-defined]
                            CHANNEL_ANALYSIS, json.dumps(verdict.as_dict())
                        )
            except Exception:
                self.stats.failed += 1
                LOGGER.exception("fallo analizando %s", token.mint)
            finally:
                self._queue.task_done()
