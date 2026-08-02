# API

Contrato HTTP y WebSocket de `apps/api`.

> **Estado: no implementada.** Fase 1 expone el subconjunto de solo lectura. Este documento es el
> contrato acordado por adelantado (`CLAUDE.md` §0.3, contract-first), no documentación de algo
> que ya funcione. Ninguna ruta de las de abajo responde todavía.

---

## Principios

- **Solo lectura hasta Fase 4.** Nada en la API abre ni cierra posiciones.
- La API **no calcula**. Lee lo que los workers han persistido. La única excepción prevista es la
  simulación de compra/venta bajo demanda del detalle de token.
- Todas las respuestas llevan el envelope de observación cuando el dato viene de un proveedor:
  `provider`, `provider_timestamp`, `received_timestamp`, `confidence`, `latency_ms`.
- Errores en formato RFC 7807 (`application/problem+json`).
- Toda ruta que muta estado exige confirmación explícita y queda en `audit_logs`.
- Versionado por prefijo: `/v1`.

---

## Autenticación

Sesión local del dashboard. Las rutas marcadas 🔐 exigen además PIN o segundo factor en el
momento de la llamada, no solo sesión válida.

---

## REST

### Sistema

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/health` | Vivo/muerto. Sin autenticación. |
| `GET` | `/v1/status` | Modo (`DRY_RUN`/`PAPER`/`LIVE`), uptime, versión, commit |
| `GET` | `/v1/providers/health` | Estado, latencia y tasa de error por proveedor |

### Tokens

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/v1/tokens` | Radar. Filtros por edad, score, liquidez, riesgo, estado |
| `GET` | `/v1/tokens/{mint}` | Ficha completa |
| `GET` | `/v1/tokens/{mint}/price` | Serie de precio, con rango y resolución |
| `GET` | `/v1/tokens/{mint}/holders` | Distribución y concentración |
| `GET` | `/v1/tokens/{mint}/clusters` | Wallets relacionadas y coordinación detectada |
| `GET` | `/v1/tokens/{mint}/trades` | Operaciones recientes con wallet |
| `GET` | `/v1/tokens/{mint}/curve` | Estado de la bonding curve y progreso de graduación |
| `GET` | `/v1/tokens/{mint}/scores` | Los 13 scores y su evolución |
| `GET` | `/v1/tokens/{mint}/impact` | Price impact simulado por tamaño de orden |

### Narrativas

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/v1/narratives` | Narrativas activas con estado y score |
| `GET` | `/v1/narratives/{id}` | Detalle, entidades y tokens vinculados |

### Señales y operaciones

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/v1/signals` | Señales generadas, con sus razones |
| `GET` | `/v1/positions` | Posiciones abiertas |
| `GET` | `/v1/orders` | Órdenes simuladas y reales |
| `GET` | `/v1/orders/{id}` | Traza completa: señal origen, features, precios, fees, explicación |
| `GET` | `/v1/portfolio` | Saldo, PnL diario y acumulado, drawdown, riesgo utilizado |

### Simulación

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/v1/backtests` | Ejecuciones registradas |
| `GET` | `/v1/backtests/{id}` | Métricas, equity curve y desglose |
| `POST` | `/v1/backtests` | Lanza una ejecución (Fase 3) |

### Control 🔐

| Método | Ruta | Descripción |
|---|---|---|
| `POST` | `/v1/control/kill-switch` | **Detiene todas las compras.** Sin confirmación adicional: parar siempre es barato |
| `POST` | `/v1/control/resume` | 🔐 Reanuda. Requiere revisión explícita |
| `GET` | `/v1/config` | Configuración vigente, con secretos redactados |
| `PUT` | `/v1/config` | 🔐 Cambia límites. Crea versión en `configuration_versions` |
| `POST` | `/v1/control/enable-live` | 🔐 Desbloquea LIVE. Exige checklist completo + PIN. Rechaza si `ENABLE_LIVE_TRADING=false` |

Nunca existirá una ruta que envíe una orden a mano desde la interfaz. Las órdenes las origina el
motor de señales y las aprueba el `RiskEngine`.

---

## WebSocket

`/v1/stream`, con suscripción por canal:

| Canal | Emite |
|---|---|
| `tokens.new` | Token nuevo detectado |
| `tokens.{mint}` | Precio, trades y métricas de un token |
| `scores` | Recálculo de scores |
| `signals` | Señal nueva |
| `positions` | Cambios de posición |
| `alerts` | Alertas |
| `providers` | Cambios de salud de proveedores |
| `system` | Cambio de modo, kill switch |

Mensajes JSON con `channel`, `event`, `timestamp`, `payload`. Heartbeat cada 15 s; el cliente
reconecta con backoff y solicita el estado perdido por REST.

---

## Contratos

Los esquemas de petición y respuesta son los modelos Pydantic v2 de
`packages/data-models/mit_data_models/`. No se duplican aquí: el esquema OpenAPI se genera desde
el código y se sirve en `/docs`. Este documento describe la **forma** de la API; la **verdad**
está en los modelos.
