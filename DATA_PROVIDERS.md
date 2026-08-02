# Proveedores de datos

Qué necesita credenciales, qué no, y qué se puede resolver **100 % on-chain** sin depender de
ninguna API de tercero.

> **Regla de oro (`CLAUDE.md` §2 y `SPEC.md` §32):** en este documento no se da por confirmada
> ninguna ruta de endpoint concreta. Las URLs base son las públicas conocidas de cada servicio;
> **los paths exactos, parámetros y formatos de respuesta se verifican contra la documentación
> oficial vigente en el momento de implementar el adaptador**, no antes. Ningún adaptador se
> escribe a partir de una respuesta supuesta. Mientras un proveedor no esté verificado y
> configurado, su adaptador no existe: solo existe la interfaz abstracta.

---

## 1. Principio de dependencia

El sistema debe funcionar en modo degradado con **una sola** credencial: la de un RPC de Solana
con WebSocket. Todo lo demás es enriquecimiento. Ninguna decisión de compra o venta puede
depender de una única fuente externa (`SPEC.md` §4.D, §33).

Orden de preferencia al resolver un dato:

```
1. Lectura directa on-chain          ← verdad, sin intermediario
2. Derivación de datos on-chain      ← cálculo propio sobre lo anterior
3. API con credencial                ← conveniencia, latencia o cobertura
4. API sin credencial                ← corroboración secundaria
```

Un dato obtenido en el nivel 3 o 4 que contradiga al nivel 1 **pierde**, y la divergencia baja el
`DataConfidenceScore`.

---

## 2. Proveedores que requieren credencial

| Proveedor | Credencial | Para qué | Criticidad | Sustituible por on-chain |
|---|---|---|---|---|
| **Helius** | `HELIUS_API_KEY` | RPC de alto rendimiento, WebSocket de logs y cuentas, webhooks | 🔴 **Crítica** — es la única dependencia realmente necesaria | Parcialmente: un RPC público sirve, con latencia y rate limit peores |
| **X (Twitter)** | `X_BEARER_TOKEN` | Menciones, velocidad, autores únicos, influencers | 🟡 Opcional | ❌ No |
| **Reddit** | `REDDIT_CLIENT_ID` + secret | Menciones y sentimiento en subreddits | 🟡 Opcional | ❌ No |
| **YouTube Data API** | `YOUTUBE_API_KEY` | Menciones en vídeo y comentarios | 🟢 Baja | ❌ No |
| **Birdeye** | `BIRDEYE_API_KEY` | Precio, liquidez, holders agregados | 🟢 Baja | ✅ Sí |
| **Bitquery** | `BITQUERY_API_KEY` | Consultas históricas complejas | 🟢 Baja | ✅ Sí, con más trabajo |
| **Solana Tracker** | `SOLANATRACKER_API_KEY` | Datos agregados de tokens | 🟢 Baja | ✅ Sí |
| **QuickNode Metis** | `QUICKNODE_METIS_URL` | RPC alternativo con add-ons de swap | 🟢 Baja | ✅ Sí (RPC de respaldo) |
| **Telegram** | `TELEGRAM_BOT_TOKEN` | Alertas salientes; ingesta de canales permitidos | 🟡 Opcional | n/a — es salida |
| **Discord** | `DISCORD_WEBHOOK_URL` | Alertas salientes | 🟡 Opcional | n/a — es salida |
| **Sentry** | `SENTRY_DSN` | Errores | 🟢 Baja | n/a |

**Coste real de arranque:** una clave de Helius. Nada más es necesario para las Fases 1–3.

---

## 3. Proveedores sin credencial

| Proveedor | Base | Para qué | Límites |
|---|---|---|---|
| **RPC público de Solana** | `api.mainnet-beta.solana.com` | Fallback de lectura on-chain | Rate limit agresivo; no apto como primario |
| **Jupiter** | `quote-api.jup.ag` | Cotizaciones, rutas, price impact, construcción de swap | Plan gratuito sin key; la key sube el límite |
| **DexScreener** | `api.dexscreener.com` | Pares, liquidez, volumen, FDV, enlaces sociales | Rate limit público; **fuente secundaria, nunca para ejecutar** |
| **GeckoTerminal** | `api.geckoterminal.com` | OHLCV y trades recientes con wallet | Rate limit público |
| **GDELT** | `GDELT_ENABLED` | Noticias y eventos globales | Sin key |
| **RSS** | `NEWS_RSS_FEEDS` | Reuters, AP, medios tecnológicos, comunicados oficiales | Sin key |
| **PumpPortal** | `PUMPPORTAL_ENABLED` | Datos de Pump.fun como respaldo | No oficial: aislar tras adaptador |
| **RugCheck** | `RUGCHECK_ENABLED` | Segunda opinión de riesgo de token | Corroboración, nunca única fuente |

**Explícitamente descartados por ahora:** GMGN (solo si aparece API documentada y permitida),
Bubblemaps (solo con acceso legítimo), Truth Social (solo vía fuente legal permitida), y
**cualquier scraping de HTML** (`SPEC.md` §4.B, §4.E).

---

## 4. Lo que se resuelve 100 % on-chain

Esto es lo importante de este documento: **la mayor parte del análisis del sistema no necesita
ninguna API de pago.** Todo lo siguiente se deriva de leer cuentas, transacciones y logs de
programa, con un único RPC.

### Identidad y ciclo de vida del token

| Dato | Cómo |
|---|---|
| Detección del mint nuevo | Suscripción a logs del programa de Pump.fun |
| Timestamp exacto de creación | `block_time` del slot de la transacción de creación |
| Creador | Firmante de la instrucción de creación |
| Nombre, símbolo, URI | Cuenta de metadata del token |
| Mint authority / freeze authority | Lectura de la cuenta del mint |
| Supply total | Lectura de la cuenta del mint |
| Cuentas asociadas (ATA) | Derivación determinista de la dirección |
| Estado de migración a PumpSwap | Evento del programa + existencia del pool |

### Bonding curve y precio

| Dato | Cómo |
|---|---|
| Reservas virtuales y reales | Lectura de la cuenta de la curva |
| Precio spot | Derivado de las reservas (`x·y=k`) |
| Progreso de graduación | Reservas actuales contra el umbral **derivado de la propia curva**, no una cifra fija en dólares |
| Market cap | Supply × precio derivado |
| Price impact por tamaño | Simulado sobre la invariante de la curva, para 0,01 / 0,05 / 0,1 / 0,25 / 0,5 / 1 SOL |
| Liquidez efectiva y profundidad | Derivadas de las reservas |

> El umbral de graduación **no son 69 000 $**. Esa cifra es un artefacto de cuando SOL valía
> ~168 $. El umbral real está fijado en SOL y se deriva de la invariante de la curva. Se calcula
> en vivo desde las reservas de cada token. (Este hallazgo viene del proyecto `pumpscope`
> previo — ver `README.pumpscope.md`.)

### Flujo y participantes

| Dato | Cómo |
|---|---|
| Cada compra y venta, con wallet | Decodificación de las instrucciones del programa |
| Compras/ventas por segundo, ratio buy/sell | Agregación propia de lo anterior |
| SOL de entrada y salida | Cambios de balance dentro de cada transacción |
| Compradores y vendedores únicos | Conjunto de firmantes por ventana |
| Tamaños medio, mediano y máximo | Estadística propia |
| Primera cohorte de compradores | Transacciones de los primeros slots tras la creación |
| Holders totales y su distribución | Enumeración de cuentas de token del mint |
| Top 1/5/10/20, HHI, Gini, entropía | Cálculo propio sobre la distribución |
| Holders nuevos y salientes por minuto | Diferencia entre snapshots consecutivos |

### Detección de manipulación — **toda on-chain**

| Señal | Cómo |
|---|---|
| Compras en el mismo slot (bundles) | Agrupación de transacciones por slot |
| Wallets financiadas por una misma fuente | Recorrido hacia atrás del grafo de transferencias de SOL |
| Wallets creadas recientemente | Antigüedad de la primera transacción de cada cuenta |
| Cantidades idénticas repetidas | Patrón sobre los importes |
| Cohortes persistentes entre lanzamientos | Intersección de conjuntos de compradores tempranos en distintos tokens |
| Historial del creador | Todos los mints creados por esa wallet y su desenlace |
| Ventas anteriores del creador | Sus transacciones de venta en tokens previos |
| Creator dumping en curso | Transacciones de venta del creador en el token actual |
| Wash trading / self-trading | Ciclos en el grafo de trades entre wallets relacionadas |
| Wallet splitting / sybil | Componentes conexos del grafo de financiación |
| Extracción de liquidez | Cambios en las reservas de la curva o del pool |
| Movimiento de whales | Transferencias de cuentas grandes hacia programas de swap |
| Honeypot / imposibilidad de vender | `simulateTransaction` de una venta antes de comprar |

**Conclusión:** el `ManipulationRiskScore`, el `RugRiskScore`, el `CreatorScore`, el
`DistributionScore`, el `HolderQualityScore`, el `WhaleScore`, el `LiquidityScore`, el
`ExitLiquidityScore` y el `MomentumScore` se calculan **sin una sola API de pago**. La única
credencial necesaria es la del RPC.

---

## 5. Lo que sí necesita una API externa

Honestamente, esto no se puede derivar de la cadena:

| Función | Por qué no es on-chain | Proveedor |
|---|---|---|
| `NarrativeScore` | Las narrativas viven en redes sociales y noticias | X, Reddit, YouTube, GDELT, RSS |
| `SocialAuthenticityScore` | Ratio de bots y cuentas nuevas es un dato de la plataforma social | X, Reddit |
| Menciones, velocidad, aceleración | Ídem | X, Reddit, YouTube |
| Enlaces sociales de un token | Están en metadata off-chain o en agregadores | DexScreener, metadata URI |
| Rutas de swap óptimas entre DEX | Requiere el grafo de liquidez agregado | Jupiter |
| Precio en USD | Requiere un oráculo o mercado de referencia | Jupiter / GeckoTerminal / oráculo |

Sin credenciales sociales, el sistema sigue funcionando: el `OpportunityScore` se recalcula con
los pesos renormalizados sobre los componentes disponibles, y el `DataConfidenceScore` baja para
reflejar que falta información. Esto es una degradación declarada, no un fallo silencioso.

---

## 6. Contrato común de todo adaptador

Todo proveedor implementa las abstracciones de `packages/providers/mit_providers/base/` y
cumple, sin excepción (`SPEC.md` §4.E, §32):

- **Timeout** explícito en cada llamada.
- **Validación** de la respuesta contra el modelo Pydantic correspondiente. Respuesta que no
  valida es un error, no un dato.
- **Retry con backoff exponencial** y jitter, con número máximo de intentos.
- **Rate limiting** propio del adaptador, por debajo del límite documentado del proveedor.
- **Circuit breaker**: tras N fallos consecutivos se abre y deja de intentar.
- **Caché** con TTL acorde a la volatilidad del dato.
- **Health check** que alimenta la tabla `provider_health` y las métricas de Prometheus.
- **Envelope de observación** (`SPEC.md` §5): `provider`, `provider_timestamp`,
  `received_timestamp`, `blockchain_slot`, `confidence`, `latency_ms`, `raw_reference`,
  `normalized_value`.
- **Sin scraping agresivo** y sin violar términos de servicio.

---

## 7. Estado de implementación

| Fase | Estado |
|---|---|
| Interfaces abstractas | ✅ Fase 0 — escritas, sin implementación |
| Adaptadores concretos | ❌ Ninguno. `packages/providers/mit_providers/adapters/` está vacío a propósito |
| Verificación de endpoints contra documentación oficial | ⏳ Fase 1, antes de escribir cada adaptador |

`packages/providers/mit_providers/adapters/` permanecerá vacío hasta que cada endpoint esté
verificado en la documentación vigente del proveedor. No se escribe un adaptador «a ciegas».
