# Simulación

`SPEC.md` §17. Modelo de latencia, slippage y fills del simulador event-driven.

> **Estado: no implementado.** Fase 3.

---

## 1. Qué NO es este simulador

```
❌  pnl = precio_final − precio_inicial
```

Esa fórmula ignora todo lo que decide el resultado real en un memecoin: que llegaste tarde, que
te llenaron peor, que la transacción falló, y que al salir no había nadie al otro lado. Un
simulador que la usa produce curvas de equity preciosas y falsas.

El simulador es **event-driven**: reproduce el flujo de eventos en orden temporal y toma
decisiones con la información disponible **en ese instante**, no con la de la vela cerrada.

---

## 2. Cadena de latencia

Cada operación acumula latencia en seis etapas. Cada una es una distribución medida, no una
constante.

```
evento on-chain
   │
   ├─ t1  detección          proveedor → nuestro proceso
   ├─ t2  decisión           features + scores + riesgo
   ├─ t3  cotización         petición de quote y respuesta
   ├─ t4  construcción       armado de la transacción
   ├─ t5  firma              ida y vuelta al signer
   └─ t6  inclusión          envío → confirmación en bloque
   ▼
precio efectivo = precio en (t0 + Σt), no en t0
```

Durante `Σt` el precio se mueve. En un token de dos minutos, esa diferencia suele ser mayor que
el margen de la operación. **La latencia no es un detalle de implementación: es la variable
dominante.**

---

## 3. Modelo de fill

Un fill no es automático ni completo. Se simula:

| Factor | Tratamiento |
|---|---|
| Slippage | Derivado de la curva y de la liquidez en el instante del fill |
| Price impact | Calculado sobre la invariante `x·y=k` para el tamaño real |
| Liquidez disponible | Puede ser menor que la vista al cotizar |
| Fills parciales | Si la liquidez no cubre la orden |
| Cotización caducada | Si `Σt` supera `max_quote_age_ms`, se rechaza y se recotiza |
| Transacción fallida | Con su probabilidad observada; se paga el fee, no hay fill |
| Priority fee | Determina la probabilidad de inclusión en el bloque objetivo |
| MEV adverso | Desplazamiento del precio en contra durante la inclusión |
| Competencia de otros bots | Reduce la liquidez disponible en los primeros slots |
| Imposibilidad de salida | La venta simula y falla → la posición queda atrapada |

La última fila es la más importante y la que casi nadie modela: **una posición que no se puede
vender vale cero**, no vale su precio de mercado.

---

## 4. Modos

### A. `HISTORICAL_REPLAY`
Reproduce eventos almacenados en orden temporal, con sus timestamps originales. Determinista:
misma entrada y misma semilla → mismo resultado, bit a bit.

### B. `PAPER_LIVE`
Consume datos reales en vivo, toma decisiones reales, **no envía transacciones**. Es el único
modo que revela competencia real y condiciones de mercado actuales. Es también la fuente de las
1 000 operaciones que exige el checklist de activación.

### C. `MONTE_CARLO`
Varía latencia, slippage, fills, prioridad, fallos y price impact sobre el mismo histórico. Se
reporta la **distribución** de resultados, no la media. Si la mediana es rentable pero el
percentil 10 es ruina, la estrategia no es viable.

### D. `STRESS_TEST`
Escenarios adversos inyectados deliberadamente:

- Rug inmediato tras la entrada.
- Venta completa de un whale.
- Caída del RPC primario a mitad de operación.
- Liquidez que desaparece antes de poder salir.
- Congestión de Solana con priority fees disparados.
- Caída instantánea del 50 %.
- Diez transacciones fallidas consecutivas.
- Dos fuentes de datos en desacuerdo.

El criterio no es «gana dinero» sino **«los kill switches disparan y la pérdida queda acotada»**.

---

## 5. Calibración contra la realidad

El simulador no se da por bueno porque parezca razonable. Se calibra:

1. En `PAPER_LIVE` se registra lo que el simulador **predijo** (precio, slippage, latencia, fee).
2. Cuando llegue `LIVE`, se registra lo que **ocurrió**.
3. Se compara la distribución de ambos por etapa.
4. Si el simulador es sistemáticamente optimista, se corrige **antes** de seguir operando.

Un simulador optimista es peor que no tener simulador, porque produce confianza injustificada.

---

## 6. Salidas

Cada ejecución produce: equity curve, drawdown, distribución de retornos por operación,
histograma de slippage y latencia, tasa de fills y de fallos, y la traza completa de cada
operación (qué señal, qué features, qué precio esperado, qué precio efectivo, por qué se salió).

Todo exportable a CSV/JSON y reproducible desde `backtest_runs`.
