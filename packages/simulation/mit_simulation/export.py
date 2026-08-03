"""Exportacion reproducible de resultados (SPEC.md 18, 21).

Un backtest que no se puede repetir bit a bit no es evidencia de nada, asi que cada export
lleva la semilla, el modo y el numero de operaciones. Sin esos tres datos, el CSV es un
grafico bonito sin procedencia.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from mit_shared.types import LAMPORTS_PER_SOL

from mit_simulation.engine import SimulationResult
from mit_simulation.metrics import BacktestMetrics


def equity_curve_csv(result: SimulationResult) -> str:
    """Equity curve y drawdown en CSV."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["at", "equity_lamports", "equity_sol", "drawdown_fraction"])
    for point in result.equity_curve:
        writer.writerow(
            [
                point.at.isoformat(),
                point.equity_lamports,
                f"{point.equity_lamports / LAMPORTS_PER_SOL:.9f}",
                f"{point.drawdown_fraction:.6f}",
            ]
        )
    return buffer.getvalue()


def trades_csv(result: SimulationResult) -> str:
    """Una fila por operacion, con su coste desglosado."""
    buffer = io.StringIO()
    if not result.trades:
        return "mint,opened_at,closed_at,net_pnl_lamports\n"
    rows = [trade.as_dict() for trade in result.trades]
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def result_json(result: SimulationResult, metrics: BacktestMetrics) -> str:
    """Resultado completo en JSON, con la procedencia necesaria para repetirlo."""
    payload: dict[str, Any] = {
        "reproducibility": {
            "mode": result.mode.value,
            "seed": result.seed,
            "initial_capital_lamports": result.initial_capital_lamports,
            "trade_count": len(result.trades),
            "note": ("Misma semilla y mismos eventos reproducen este resultado bit a bit."),
        },
        "metrics": metrics.as_dict(),
        "failed_fills": result.failed_fills,
        "stuck_positions": result.stuck_positions,
        "trades": [trade.as_dict() for trade in result.trades],
        "equity_curve": [
            {
                "at": point.at.isoformat(),
                "equity_lamports": point.equity_lamports,
                "drawdown_fraction": round(point.drawdown_fraction, 6),
            }
            for point in result.equity_curve
        ],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)
