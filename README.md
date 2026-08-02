# Memecoin Intelligence Terminal

Plataforma para **detectar, analizar, simular y (eventualmente) operar** memecoins de
Pump.fun y PumpSwap en tiempo real sobre Solana.

> **Estado: Fase 0 — andamiaje.** No hay lógica de negocio implementada. No hay conexiones
> reales a ninguna API. Todos los proveedores son interfaces abstractas. Ver
> [Qué funciona hoy](#qué-funciona-hoy).

El documento autoritativo del *qué* es [`SPEC.md`](SPEC.md). Las reglas de *cómo* trabajar y los
guardarraíles inviolables están en [`CLAUDE.md`](CLAUDE.md). Ante conflicto, gana `CLAUDE.md`.

---

## Las tres reglas que no se rompen

1. **Arranca siempre en SIMULATION/PAPER.** `LIVE` existe pero está bloqueado por defecto y
   requiere completar [`LIVE_TRADING_CHECKLIST.md`](LIVE_TRADING_CHECKLIST.md).
2. **Ningún LLM firma transacciones, cambia límites de riesgo ni toca claves.** Las decisiones
   monetarias las toma un `RiskEngine` determinista. El LLM solo clasifica, agrupa, resume y
   explica, y devuelve JSON validado.
3. **El backend principal nunca ve la clave privada.** Firma un servicio aislado con allowlist de
   programas y límite diario de SOL. Ver [`SECURITY.md`](SECURITY.md).

Esto no promete rentabilidad. Es instrumental de análisis y control de riesgo.

---

## Estructura

```
/apps            api (FastAPI) · web (Next.js) · worker (jobs) · signer (aislado)
/packages        solana · pumpfun · providers · data-models · features · strategies
                 risk · execution · simulation · ml · narratives · notifications
                 observability · shared
/infrastructure  docker · grafana · prometheus · migrations (Alembic)
/tests           unit · integration · e2e · load · fixtures · replay
/docs            documentación de apoyo
```

Los directorios usan kebab-case (SPEC.md §26); el módulo Python importable dentro de cada uno
lleva prefijo `mit_` (`packages/data-models/mit_data_models/`), porque `data-models` no es un
identificador válido de Python. Detalle en [`ARCHITECTURE.md`](ARCHITECTURE.md).

---

## Puesta en marcha

### Requisitos

| Herramienta | Versión | Notas |
|---|---|---|
| Python | **3.12+** | SPEC.md §3. La 3.9 del sistema macOS **no** sirve. |
| Node.js | 20.11+ | Solo para `apps/web`. |
| Docker + Compose | v2 | Postgres, Redis, Prometheus, Grafana. |
| PostgreSQL | 16 + TimescaleDB | Lo levanta `docker compose`. |

### Arranque local

```bash
cp .env.example .env          # rellena lo que tengas; nada es obligatorio en Fase 0
make install-dev              # instala el paquete + ruff/mypy/pytest
make up                       # postgres, redis, prometheus, grafana
make migrate                  # aplica el esquema (SPEC.md §23)
make check                    # lint + type-check + tests
```

`make help` lista todos los objetivos.

### Verificar que el esquema está bien aplicado

```bash
docker compose exec postgres psql -U mit -d mit -c "\dt"
```

Deben aparecer las 31 tablas de SPEC.md §23. Las hypertables de TimescaleDB se listan con:

```bash
docker compose exec postgres psql -U mit -d mit -c "SELECT hypertable_name FROM timescaledb_information.hypertables;"
```

---

## Qué funciona hoy

Fase 0 entrega **estructura y contratos**, no comportamiento.

| Elemento | Estado |
|---|---|
| Árbol del monorepo (SPEC.md §26) | ✅ creado |
| Documentos obligatorios (SPEC.md §27) | ✅ escritos |
| Esquema de BD (SPEC.md §23) como migraciones Alembic | ✅ **aplicado y verificado** contra PostgreSQL 16 + TimescaleDB 2.17.2: 31 tablas, 18 hypertables, 111 índices. Reversible (`downgrade base` → `upgrade head`) |
| Invariantes de seguridad en la BD | ✅ verificados en vivo: `audit_logs` rechaza UPDATE/DELETE, `features` rechaza lookback futuro, `live_orders` rechaza modo PAPER, config activa exige aprobación |
| Interfaces abstractas de proveedores (SPEC.md §4) | ✅ definidas, sin implementación |
| Modelos de contrato (Pydantic v2) | ✅ definidos |
| Infraestructura local | ✅ `postgres`, `redis`, `prometheus`, `grafana` arrancan limpios |
| Detección de tokens, scoring, features | ❌ Fase 1–2 |
| Simulador, backtesting | ❌ Fase 3 |
| Señales, RiskEngine, alertas | ❌ Fase 4 |
| Modelos ML | ❌ Fase 5 |
| ExecutionEngine, signer | ❌ Fase 6 |
| Trading LIVE | 🔒 bloqueado hasta Fase 7 y checklist completo |

Todo lo marcado como no implementado lleva en el código un comentario `# STUB:` con la fase que
lo cubre. No hay código ficticio presentado como funcional (CLAUDE.md §2).

---

## Documentación

| Documento | Contenido |
|---|---|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Diagrama de capas y responsabilidad de cada paquete |
| [`SECURITY.md`](SECURITY.md) | Modelo de amenazas, arquitectura de firma, gestión de claves |
| [`RISK_POLICY.md`](RISK_POLICY.md) | Límites, sizing, stops y kill switches |
| [`DATA_PROVIDERS.md`](DATA_PROVIDERS.md) | Qué necesita credenciales y qué se resuelve on-chain |
| [`LIVE_TRADING_CHECKLIST.md`](LIVE_TRADING_CHECKLIST.md) | Requisitos para desbloquear dinero real |
| [`BACKTESTING.md`](BACKTESTING.md) | Metodología, métricas y criterios de validez |
| [`SIMULATION.md`](SIMULATION.md) | Modelo de latencia, slippage y fills |
| [`API.md`](API.md) | Contrato HTTP/WebSocket de `apps/api` |

---

## Proyecto anterior en este repositorio

Este directorio contenía `pumpscope`, una herramienta CLI independiente de análisis de tokens de
Pump.fun (`pumpscope.py`, `serve.py`, `ps/`). Sigue intacta y su README se conserva en
[`README.pumpscope.md`](README.pumpscope.md). No forma parte del árbol de SPEC.md §26 y no se
importa desde ningún paquete. Pendiente de decisión: archivarla en `legacy/` o portar sus
estimadores (Garman-Klass, escalado de volatilidad tipo Hurst, bootstrap por bloques) a
`packages/features`.

---

## Licencia

Propietario. Uso privado.
