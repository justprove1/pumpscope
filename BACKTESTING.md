# Backtesting

`SPEC.md` §18. Metodología, métricas y criterios para que una estrategia se considere candidata.

> **Estado: no implementado.** Fase 3. Este documento fija las reglas antes de escribir el
> código, para que el código no pueda «elegir» una metodología conveniente después.

---

## 1. La regla que lo gobierna todo

**Ningún cálculo puede usar información no disponible en el momento de la decisión.**

Esto no es una advertencia genérica; es una restricción verificable. Cada feature declara su
ventana de mirada atrás, y el motor de backtest rechaza toda feature cuyo timestamp de origen sea
posterior al timestamp de predicción. El data leakage no se detecta revisando código: se hace
imposible por construcción.

Fuentes habituales de leakage en este dominio, todas prohibidas:

- Usar el precio de cierre de la vela en curso.
- Usar el resultado de la graduación para filtrar tokens de entrenamiento.
- Normalizar features con estadísticas calculadas sobre todo el histórico.
- Etiquetar con un horizonte que se solapa con el de la muestra siguiente sin purgar.
- Excluir tokens que murieron por no tener datos suficientes.

---

## 2. Partición temporal

```
│──────── train ────────│─ purge ─│── validation ──│─ purge ─│─── test ───│
                          ▲                          ▲
                          └── embargo = horizonte máximo de etiquetado ──┘
```

- Separación **estrictamente temporal**. Nunca aleatoria.
- **Purga** entre particiones igual al horizonte máximo de etiquetado, para que ninguna etiqueta
  de train se solape con datos de validation.
- **Embargo** adicional tras cada partición.
- **Walk-forward**: la ventana avanza y el modelo se reentrena. Un único split no es evidencia.
- El conjunto de test se toca **una vez**. Si se optimiza contra él, deja de ser test.

---

## 3. Universo y sesgo de supervivencia

El universo debe incluir, obligatoriamente:

- Tokens que hicieron rug.
- Tokens que nunca tuvieron liquidez.
- Tokens que murieron en minutos.
- Tokens que fallaron la graduación.
- Tokens con datos incompletos (marcados, no eliminados).

Un backtest sobre tokens «que llegaron a algo» mide la selección posterior, no la estrategia.
Si el universo se filtra por cualquier criterio, ese criterio se declara y se justifica.

---

## 4. Costes

Sin costes, cualquier estrategia de alta frecuencia parece rentable. Se modelan siempre:

| Coste | Modelo |
|---|---|
| Fee base de red | Real por transacción |
| Priority fee | Distribución observada, no media |
| Fee del protocolo | Según el programa |
| Slippage | Derivado de la curva y de la liquidez del momento, no constante |
| Price impact | Simulado sobre la invariante, por tamaño de orden |
| Transacciones fallidas | Con su probabilidad observada; se paga el fee, no se obtiene el fill |
| Latencia | Distribución por etapa (ver `SIMULATION.md`) |

Se reporta siempre **retorno neto**. El retorno bruto no aparece en ningún informe.

---

## 5. Etiquetado

**Triple-barrier**, con horizonte temporal explícito. Nada de «subirá o bajará».

```
barrera superior:  +X %      → etiqueta 1
barrera inferior:  −Y %      → etiqueta 0
barrera temporal:  T minutos → etiqueta según posición final
```

Objetivos previstos (`SPEC.md` §19): probabilidad de graduación; de +20 % antes de −10 %; de
+50 % antes de −20 %; de supervivencia a 5/15/60 min; de venta de whale; de rug; excursión
favorable y adversa máximas esperadas.

Las probabilidades se **calibran** (isotónica o Platt) y se reporta la curva de calibración. Un
modelo con buen AUC y mala calibración es inútil para sizing.

---

## 6. Métricas

Se reportan todas. Ninguna se presenta aislada.

**Retorno:** total, neto, win rate, ganancia media, pérdida media, profit factor, expectancy.

**Ajustadas a riesgo:** Sharpe, Sortino, Calmar, drawdown máximo, VaR, expected shortfall,
pérdidas consecutivas máximas, recovery factor.

**Ejecución:** fill rate, tasa de transacciones fallidas, slippage medio / p95 / p99, latencia
por etapa.

**Desglose:** por narrativa, por market cap inicial, por edad del token, por tramo de score, por
proveedor de datos, dentro y fuera de muestra.

---

## 7. Criterios de aceptación

Una estrategia es **candidata** — no «buena», candidata — solo si cumple **todos**:

- [ ] Rentable **neta** de todos los costes.
- [ ] Profit factor por encima del mínimo configurado.
- [ ] Resultados fuera de muestra consistentes con los de dentro.
- [ ] **No depende de uno o dos outliers**: sigue siendo rentable eliminando el mejor 1 % de las
      operaciones.
- [ ] Sobrevive a un escenario de latencia y slippage peores (percentil 90 en vez de mediana).
- [ ] Drawdown máximo dentro del límite de `RISK_POLICY.md`.
- [ ] Número de operaciones suficiente para que el resultado no sea ruido.
- [ ] Estabilidad entre ventanas de walk-forward, no un único periodo bueno.

**Win rate alto no es criterio.** Una estrategia con 90 % de aciertos y una cola izquierda que se
lleva el año entero es una forma cara de perder dinero.

---

## 8. Reproducibilidad

Cada ejecución queda registrada en `backtest_runs` con: commit, versión de estrategia, versión de
modelo, semilla, ventana temporal, universo, configuración de costes y hash del dataset. Un
backtest que no se puede repetir bit a bit no es evidencia de nada.

---

## 9. Del backtest al dinero real

```
backtest histórico  →  fuera de muestra  →  paper trading en vivo  →  aprobación manual
                                                                   →  LIVE con importe mínimo
```

Ningún paso se salta. El paso de paper trading en vivo es el que detecta lo que el backtest no
puede ver: competencia de otros bots, MEV, y liquidez que desaparece justo cuando la necesitas.
