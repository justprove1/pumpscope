"""Backtest de trailing stop sobre las estampidas grabadas.

Lee `data/training/stampedes.jsonl`, recorre la serie de precios REAL de cada caso y barre
varios niveles de trailing. No elige un nivel: los muestra todos, porque quedarse con el mejor
de un punado de casos es sobreajustar.

Uso:
    python infrastructure/scripts/backtest_stampedes.py [--size 0.5]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "packages" / "simulation"))

from mit_simulation.trailing import CostModel, backtest_series, sweep

CORPUS = Path(__file__).resolve().parents[2] / "data" / "training" / "stampedes.jsonl"
LEVELS = (0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40)


def load() -> tuple[list[list[tuple[float, float]]], list[str]]:
    if not CORPUS.exists():
        return ([], [])
    series: list[list[tuple[float, float]]] = []
    names: list[str] = []
    for line in CORPUS.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        points = record.get("cap_series") or []
        if len(points) < 2:
            continue
        series.append([(float(t), float(c)) for t, c in points])
        names.append(record.get("symbol") or record.get("mint", "")[:8])
    return (series, names)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size", type=float, default=0.5, help="SOL por operacion")
    args = parser.parse_args()

    cases, names = load()
    if not cases:
        print("Sin series de precios en el corpus todavia.")
        print("El worker las graba al resolverse cada estampida; hace falta dejarlo correr.")
        return 1

    costs = CostModel()
    print(f"Casos con serie completa: {len(cases)}   ·   {args.size} SOL por operacion")
    print("Costes: 1% comision + 1,25% deslizamiento por lado, mas comisiones de cadena.\n")

    header = (
        f"{'trailing':>9} {'casos':>6} {'gana':>5} {'pierde':>7} "
        f"{'total':>10} {'mediana':>10} {'peor':>9} {'llego al pico':>14}"
    )
    print(header)
    print("-" * len(header))
    for row in sweep(cases, levels=LEVELS, costs=costs, size_sol=args.size):
        print(
            f"{row.trailing * 100:>8.0f}% {row.cases:>6} {row.winners:>5} {row.losers:>7} "
            f"{row.total_sol:>+9.4f} {row.median_sol:>+9.4f} {row.worst_sol:>+8.4f} "
            f"{row.captured_peak:>9}/{row.cases}"
        )

    # Detalle con un nivel intermedio, para ver caso a caso.
    print("\n--- detalle con trailing 20% ---")
    for name, case in zip(names, cases, strict=True):
        result = backtest_series(case, trailing=0.20, costs=costs, size_sol=args.size)
        flag = "" if result.captured_peak else "  <- el stop cerro ANTES del maximo"
        print(
            f"  {name[:10]:<10} pico {result.peak_multiple:>5.2f}x  salida {result.multiple:>5.2f}x"
            f"  {result.profit_sol:>+8.4f} SOL  ({result.seconds_held:>5.1f}s){flag}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
