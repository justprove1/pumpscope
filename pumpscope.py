#!/usr/bin/env python3
"""pumpscope — analisis probabilistico de tokens de pump.fun.

Uso:
    python3 pumpscope.py <link-o-mint> [opciones]

Opciones:
    --horizonte H   horizonte en horas (por defecto 6)
    --why           desglosa como se construyo cada probabilidad
    --explicar X    explicacion en prosa de un escenario: subida|rango|bajada
    --json          salida JSON en vez de informe
    --buscar        busca memecoins en posible tendencia alcista (sin mint)
    --live          analiza una vez y deja el precio corriendo en directo
    --watch N       reanaliza cada N segundos
    --no-color      sin codigos ANSI
"""

import json
import sys
import time

from ps import analyze, report, resolve, sources


def _parse(argv):
    opts = {"target": None, "horizonte": 6.0, "why": False,
            "json": False, "watch": 0, "color": True, "live": False,
            "buscar": False, "limite": 8,
            "explicar": None}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("-h", "--help"):
            print(__doc__)
            sys.exit(0)
        elif a == "--why":
            opts["why"] = True
        elif a == "--explicar":
            i += 1
            opts["explicar"] = {"subida": "SUBIDA", "rango": "RANGO",
                                "bajada": "BAJADA"}.get(argv[i].lower())
            if not opts["explicar"]:
                print("--explicar admite: subida, rango o bajada", file=sys.stderr)
                sys.exit(2)
        elif a == "--buscar":
            opts["buscar"] = True
        elif a == "--limite":
            i += 1
            opts["limite"] = int(argv[i])
        elif a == "--live":
            opts["live"] = True
        elif a == "--json":
            opts["json"] = True
        elif a == "--no-color":
            opts["color"] = False
        elif a == "--horizonte":
            i += 1
            opts["horizonte"] = float(argv[i])
        elif a == "--watch":
            i += 1
            opts["watch"] = int(argv[i])
        elif a.startswith("-"):
            print("opcion desconocida: %s" % a, file=sys.stderr)
            sys.exit(2)
        else:
            opts["target"] = a
        i += 1
    return opts


def _jsonable(a):
    """Informe compacto en JSON, sin las series crudas."""
    return {
        "mint": a["mint"],
        "nombre": a["coin"].get("name"),
        "simbolo": a["coin"].get("symbol"),
        "precio_usd": a["price"],
        "mcap_usd": a["mcap_now"],
        "regimen": a["pred"]["regime"],
        "confianza": round(a["pred"]["confidence"], 3),
        "horizonte_h": a["horizon_h"], "horizonte_pedido_h": a["horizon_pedido_h"], "horizonte_recortado": a["horizon_recortado"],
        "movimiento_1sigma_pct": round(a["expected_move_pct"], 2),
        "curva": {
            "graduado": a["curve"].get("complete"),
            "progreso": a["curve"].get("progress"),
            "precio_graduacion_usd": a["curve"].get("grad_price_sol_usd"),
            "mcap_graduacion_usd": a["curve"].get("grad_mcap_usd"),
            "sol_faltantes": a["curve"].get("sol_to_grad"),
        },
        "escenarios": [
            {k: v for k, v in s.items() if v is not None} for s in a["scenarios"]
        ],
        "soportes": [{"precio": l["price"], "mcap": l.get("mcap"),
                      "tipo": l.get("kind")} for l in a["supports"][:5]],
        "resistencias": [{"precio": l["price"], "mcap": l.get("mcap"),
                          "tipo": l.get("kind")} for l in a["resistances"][:5]],
        "flujo": {
            "trades": a["flow"]["n"],
            "desbalance": round(a["flow"]["imbalance"], 4),
            "wallets": a["flow"]["wallets"],
            "hhi": round(a["flow"]["hhi"], 4),
            "dev_vendio_usd": a["flow"]["dev_sold_usd"],
        },
        "liquidez_usd": a["liq"]["liq_usd"],
        "señales": {k: round(v, 3) for k, v in a["pred"]["signals"].items() if v},
        "avisos": a["warnings"],
    }


def run_buscar(opts):
    """Ranking de candidatos en posible tendencia alcista."""
    from ps import report as rp, scan

    rows, errs = scan.find(limit=opts["limite"])
    C = rp._c
    print("\n%sMEMECOINS EN POSIBLE TENDENCIA ALCISTA%s" % (C("b"), C("r")))
    print("%s%s%s" % (C("dim"), "-" * 68, C("r")))
    if not rows:
        print("  Ningun candidato supera los minimos ahora mismo.")
        print("  %s(se exige liquidez >$6k y volumen >$3k/h: casi nada los pasa)%s"
              % (C("dim"), C("r")))
    for i, r in enumerate(rows, 1):
        sc = r["score"]
        col = C("g") if sc >= 3.5 else (C("y") if sc >= 2 else C("dim"))
        print("\n%s%2d. %-18s%s  %sscore %+.2f%s   %sh1 %+.1f%%  liq %s  vol1h %s%s" % (
            C("b"), i, r["name"][:18], C("r"), col, sc, C("r"),
            C("dim"), r["h1"], scan._money(r["liq"]),
            scan._money(r["vol_h1"]), C("r")))
        print("    %s%s%s" % (C("dim"), r["mint"], C("r")))
        for x in r["reasons"]:
            print("      %s+%s %s" % (C("g"), C("r"), x))
        for x in r["penalties"]:
            print("      %s-%s %s" % (C("red"), C("r"), x))
    for e in errs:
        print("\n  %saviso: %s%s" % (C("y"), e, C("r")))
    print("\n%sEl score NO es una probabilidad: ordena candidatos para mirarlos"
          "\nde cerca. Analiza el que te interese antes de hacer nada.%s"
          % (C("dim"), C("r")))
    print("%s%s%s" % (C("dim"), "-" * 68, C("r")))


def run_live(mint, opts, a):
    """Analisis una vez, y luego el precio en directo en una sola linea.

    El modelo no se recalcula: lo que se mueve es el precio (una llamada ligera
    a pump.fun cada ~1,5s). Cuando se aleja mucho del precio del analisis, se
    avisa de que toca recalcular.
    """
    from ps import live, report as rp

    base = a["price"]
    # Objetivos en capitalizacion: fijos, del analisis. Lo que se mueve en vivo
    # es la distancia desde la capitalizacion actual hasta cada uno.
    tg = {s_["name"]: s_ for s_ in a["scenarios"]}
    mc_up = (tg.get("SUBIDA") or {}).get("mcap")
    mc_dn = (tg.get("BAJADA") or {}).get("mcap")
    rg_ = tg.get("RANGO") or {}
    mc_lo, mc_hi = rg_.get("mcap_lo"), rg_.get("mcap_hi")
    if mc_up or mc_dn:
        print("\n  %sobjetivos en mcap:  techo %s   rango %s-%s   suelo %s%s" % (
            rp._c("dim"), rp.money_full(mc_up), rp.money_full(mc_lo),
            rp.money_full(mc_hi), rp.money_full(mc_dn), rp._c("r")))
    print("\n%sprecio en directo — Ctrl-C para salir%s" % (rp._c("dim"), rp._c("r")))
    prev = base
    while True:
        try:
            v = live.tick(mint)
        except sources.SourceError as e:
            sys.stdout.write("\r  %sfuente caida: %s%s\033[K" % (rp._c("y"), e, rp._c("r")))
            sys.stdout.flush()
            time.sleep(3)
            continue

        px = v.get("price")
        if px:
            ch = ((px / base) - 1.0) * 100.0 if base else 0.0
            arrow = "▲" if (prev and px > prev) else ("▼" if (prev and px < prev) else "·")
            col = rp._c("g") if ch > 0 else (rp._c("red") if ch < 0 else rp._c("dim"))
            extra = ""
            if v.get("progress") is not None and not v.get("complete"):
                extra += "   curva %.1f%%" % (v["progress"] * 100)
            if abs(ch) > 15:
                extra += "   %s(movimiento grande, recalcula)%s" % (rp._c("y"), rp._c("r"))
            mcv = v.get("mcap")
            dist = ""
            if mcv:
                if mc_up:
                    dist += "  %s↑%+.1f%%%s" % (rp._c("g"), (mc_up / mcv - 1) * 100,
                                                rp._c("r"))
                if mc_lo and mc_hi:
                    dist += ("  %s◆dentro%s" % (rp._c("y"), rp._c("r"))
                             if mc_lo <= mcv <= mc_hi else "  %s◆fuera%s"
                             % (rp._c("dim"), rp._c("r")))
                if mc_dn:
                    dist += "  %s↓%+.1f%%%s" % (rp._c("red"), (mc_dn / mcv - 1) * 100,
                                                rp._c("r"))
            sys.stdout.write("\r  %s %s   mcap %s%s   %s%+.2f%%%s%s\033[K" % (
                arrow, rp.money(px), rp.money_full(mcv), dist, col, ch,
                rp._c("r"), extra))
            sys.stdout.flush()
            prev = px
        time.sleep(1.5)


def run_once(mint, opts):
    a = analyze.analyze(mint, horizon_h=opts["horizonte"])
    if opts["json"]:
        print(json.dumps(_jsonable(a), indent=2, ensure_ascii=False))
    else:
        print(report.render(a, show_why=opts["why"],
                            explicar=opts["explicar"]))
    return a


def main(argv):
    opts = _parse(argv)
    report.set_color(opts["color"] and sys.stdout.isatty())

    if opts["buscar"]:
        try:
            run_buscar(opts)
            return 0
        except sources.SourceError as e:
            print("Error de datos: %s" % e, file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            return 130

    if not opts["target"]:
        print(__doc__)
        return 2

    try:
        mint = resolve.extract_mint(opts["target"])
    except ValueError as e:
        print("Error: %s" % e, file=sys.stderr)
        return 2

    try:
        if opts["watch"] > 0:
            while True:
                print("\033[2J\033[H" if opts["color"] else "")
                try:
                    run_once(mint, opts)
                except sources.SourceError as e:
                    print("fallo de red: %s" % e, file=sys.stderr)
                print("\nactualizando cada %ds — Ctrl-C para salir" % opts["watch"])
                time.sleep(opts["watch"])
        else:
            a = run_once(mint, opts)
            if opts["live"] and not opts["json"]:
                run_live(mint, opts, a)
    except KeyboardInterrupt:
        print("\ncortado.")
        return 130
    except sources.SourceError as e:
        print("Error de datos: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
