# Política de riesgo

Implementación de `SPEC.md` §12 y §14. El `RiskEngine` que aplica estas reglas es
**determinista**: mismos datos de entrada → misma decisión, siempre. Sin ML, sin LLM, sin
aleatoriedad, sin estado oculto.

> Estas cifras son un punto de partida conservador, **no una estrategia validada**. Deben
> optimizarse en simulación (§17) y sobrevivir a validación fuera de muestra (§18) antes de
> considerarse otra cosa.

---

## 1. Advertencia

Operar memecoins de baja capitalización tiene una probabilidad alta de pérdida total del capital
comprometido. Este sistema no promete rentabilidad. Su objetivo primario es **limitar pérdidas y
dejar registro de por qué se hizo cada cosa**, no maximizar retorno.

---

## 2. Límites por defecto

Valores iniciales; todos configurables por entorno y auditados al cambiar.

### Por operación

| Parámetro | Valor | Variable |
|---|---|---|
| Riesgo por operación | 0,5 % del capital | `RISK_PER_TRADE_PERCENT` |
| Exposición máxima por token | 3 % del capital | `MAX_EXPOSURE_PER_TOKEN_PERCENT` |
| Tamaño máximo de orden | 0,05 SOL | `LIVE_TRADING_MAX_ORDER_SOL` |
| Slippage máximo | 250 bps | `MAX_SLIPPAGE_BPS` |
| Price impact máximo | 3,0 % | `MAX_PRICE_IMPACT_PERCENT` |
| Antigüedad máxima de cotización | 1500 ms | `MAX_QUOTE_AGE_MS` |

### Por cartera

| Parámetro | Valor | Variable |
|---|---|---|
| Exposición total simultánea | 0,2 SOL | `LIVE_TRADING_MAX_TOTAL_EXPOSURE_SOL` |
| Posiciones abiertas simultáneas | 1 (al activar LIVE) | `LIVE_TRADING_MAX_OPEN_POSITIONS` |
| Gasto diario máximo | 0,5 SOL | `LIVE_TRADING_MAX_DAILY_SOL` |
| Pérdida diaria máxima | 3 % | `MAX_DAILY_LOSS_PERCENT` |
| Pérdida semanal máxima | 7 % | *(config)* |
| Drawdown máximo | 10 % | `MAX_DRAWDOWN_PERCENT` |
| Pérdidas consecutivas máximas | 4 | `MAX_CONSECUTIVE_LOSSES` |
| Reserva mínima de SOL para fees | 0,02 SOL | `MIN_SOL_FEE_RESERVE` |

### Cooldowns

| Situación | Efecto |
|---|---|
| Tras una pérdida | 15 min sin nuevas entradas |
| Tras `MAX_CONSECUTIVE_LOSSES` | Parada hasta revisión manual |
| Por token, tras salir | 4 h sin reentrada en el mismo mint |
| Reentrada impulsiva | Prohibida: no se reentra en un token cerrado en pérdida el mismo día |

---

## 3. Cálculo del tamaño de posición

El tamaño es el **mínimo** de todas las restricciones. Nunca el máximo permitido por una sola.

```
tamaño = min(
    capital × RISK_PER_TRADE_PERCENT / distancia_al_stop,
    capital × MAX_EXPOSURE_PER_TOKEN_PERCENT,
    LIVE_TRADING_MAX_ORDER_SOL,
    liquidez_efectiva × fracción_máxima_de_liquidez,
    tamaño_que_mantiene_price_impact ≤ MAX_PRICE_IMPACT_PERCENT,
    presupuesto_diario_restante,
    saldo_disponible − MIN_SOL_FEE_RESERVE − coste_estimado_de_salida
)
```

Modulado a la baja por: volatilidad realizada alta, `DataConfidenceScore` bajo, pérdida diaria
acumulada y correlación con posiciones abiertas (misma narrativa o mismo creador cuentan como
correlacionadas).

Si el resultado es menor que el mínimo operable, **no se opera**. No se redondea hacia arriba.

---

## 4. Vetos de elegibilidad

Reglas duras de `SPEC.md` §12. Cualquiera que se cumpla produce `IGNORE`. No hay puntuación que
las compense: un `OpportunityScore` de 100 no anula un veto.

| # | Veto |
|---|---|
| 1 | `DataConfidenceScore` por debajo del umbral |
| 2 | `RugRiskScore` por encima del umbral |
| 3 | `ManipulationRiskScore` por encima del umbral |
| 4 | Liquidez efectiva insuficiente |
| 5 | Sin ruta de salida verificable |
| 6 | La simulación de venta falla |
| 7 | Impacto estimado por encima del máximo |
| 8 | Historial crítico en la wallet del creador |
| 9 | Concentración de top holders por encima del límite |
| 10 | Cluster dominante peligroso |
| 11 | El token ya subió por encima del límite configurado |
| 12 | Narrativa agotada (`SATURATED` / `EXHAUSTED`) |
| 13 | Spread o slippage excesivo |
| 14 | Datos atrasados por encima del umbral de latencia |
| 15 | Divergencia grave entre fuentes |
| 16 | SOL insuficiente para compra + fees + salida |
| 17 | Límite diario de riesgo alcanzado |

Cada veto activado se registra con su valor concreto y su umbral, para que la decisión sea
reconstruible después.

---

## 5. Stops

Nueve mecanismos, evaluados en cada tick. El primero que dispara, manda.

| Stop | Dispara cuando |
|---|---|
| **Hard stop** | Precio alcanza el nivel de invalidación absoluto |
| **Soft stop** | Se degrada la tesis (score cae por debajo del umbral de entrada) |
| **Trailing stop** | Retroceso desde el máximo favorable alcanzado |
| **Time stop** | Se supera la duración máxima de la señal sin alcanzar objetivo |
| **Liquidity stop** | La liquidez cae por debajo del mínimo para salir con el impacto permitido |
| **Narrative stop** | La narrativa pasa a `DECELERATING` / `EXHAUSTED` |
| **Whale exit stop** | Un whale o el creador empiezan a vender de forma significativa |
| **Break-even stop** | Tras la primera toma parcial, el stop sube a coste |
| **Partial take profit** | Escalado configurable de salidas parciales |

Escalado inicial orientativo de salidas parciales — **a optimizar en simulación, no asumido como
ganador**: 25 % a +20 %, 25 % a +40 %, 25 % a +80 %, 25 % con trailing.

**Averaging down automático: prohibido** en esta versión. `ADD_FORBIDDEN` es el estado por
defecto de toda posición abierta.

---

## 6. Kill switches

Detienen **todas las compras nuevas** de inmediato. Las posiciones abiertas pasan a modo
vigilancia y salida segura. Reactivación siempre manual, nunca automática.

| Disparador | Condición |
|---|---|
| Pérdida diaria | Supera `MAX_DAILY_LOSS_PERCENT` |
| Drawdown | Supera `MAX_DRAWDOWN_PERCENT` |
| Pérdidas consecutivas | Supera `MAX_CONSECUTIVE_LOSSES` |
| Proveedor crítico caído | Helius o el RPC de respaldo no responden |
| Latencia | p95 por encima del límite configurado |
| Tasa de errores | Aumento sostenido de fallos de API o de transacciones |
| Divergencia de precios | Dos fuentes discrepan por encima del umbral |
| Saldo anómalo | El balance de la wallet no coincide con el esperado |
| Transacciones duplicadas | Se detecta la misma intención enviada dos veces |
| Exposición inesperada | Hay posición on-chain que el sistema no registró |
| Firma no autorizada | El signer registra una solicitud que no originó el ExecutionEngine |
| Configuración no aprobada | Cambio de límites sin registro de aprobación |

El kill switch también es accionable **manualmente desde el dashboard**, en un clic, sin
confirmación adicional. Parar siempre es barato; arrancar es lo que cuesta.

---

## 7. Cambios de política

Modificar cualquier límite de este documento es un cambio auditado:

1. Se registra en `configuration_versions` con autor, timestamp, valor anterior y nuevo.
2. Queda entrada en `audit_logs`.
3. Si el sistema está en `LIVE`, se requiere confirmación explícita en la interfaz.
4. El sistema **no puede modificar sus propios límites de riesgo real** (`SPEC.md` §20). Una
   estrategia nueva se prueba en histórico, fuera de muestra y en paper, y se aprueba a mano.
