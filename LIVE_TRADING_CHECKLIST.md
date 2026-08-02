# Checklist de activación de trading real

`SPEC.md` §15, §30. **Ninguna casilla se marca por adelantado.** Cada una exige evidencia
verificable — salida de comando, consulta a base de datos o captura del dashboard — que se
archiva en `docs/live-activation/<fecha>/`.

> **Estado actual: 🔒 BLOQUEADO.** `ENABLE_LIVE_TRADING=false`, `SIGNER_MODE=disabled`.
> El `ExecutionEngine` no existe todavía (Fase 6). Este documento es el contrato de lo que hará
> falta, no un procedimiento ya ejecutable.

---

## A. Requisitos de código

- [ ] Toda la suite de tests en verde (`make check`), con la salida real archivada.
- [ ] Type-check en modo strict sin errores, con todas las dependencias instaladas
      (`ignore_missing_imports = false`).
- [ ] Lint sin avisos.
- [ ] Cobertura de la lógica crítica: `RiskEngine`, `ExecutionEngine`, sizing, kill switches y
      signer.
- [ ] Cada kill switch tiene un test que demuestra que **dispara** y otro que demuestra que
      **bloquea** la compra siguiente.
- [ ] Test que demuestra que un `ADD_FORBIDDEN` no puede ser sobrescrito.
- [ ] Test que demuestra que ninguna salida de LLM puede alterar un importe o un límite.

## B. Requisitos de simulación

- [ ] Mínimo **1 000 operaciones simuladas** completadas.
- [ ] Muestra fuera de entrenamiento, separada temporalmente.
- [ ] Costes reales incluidos: fees, priority fees, slippage y transacciones fallidas.
- [ ] Profit factor por encima del mínimo configurado.
- [ ] Drawdown máximo dentro del límite.
- [ ] Los resultados **no dependen de uno o dos outliers** (verificado eliminando el mejor 1 %).
- [ ] Los resultados sobreviven a escenarios de latencia y slippage peores.
- [ ] Stress tests superados: rug, venta de whale, caída de RPC, desaparición de liquidez,
      congestión de Solana, caída instantánea del 50 %, 10 transacciones fallidas seguidas,
      datos inconsistentes.

## C. Requisitos de infraestructura

- [ ] Reconciliación con el estado on-chain probada tras un reinicio forzado.
- [ ] Recuperación desde checkpoint verificada.
- [ ] Reconexión de WebSocket verificada con corte real de red.
- [ ] Fallback de RPC verificado tirando el proveedor primario.
- [ ] Idempotencia verificada: la misma intención de orden enviada dos veces produce **una**
      transacción.
- [ ] Alertas llegando por al menos un canal.
- [ ] Dashboards de Grafana mostrando latencia, errores y exposición.
- [ ] `audit_logs` permite reconstruir una operación simulada de principio a fin.

## D. Requisitos de wallet y firma

- [ ] Wallet **dedicada y nueva**, sin relación con la wallet principal del usuario.
- [ ] Capital limitado y asumible como pérdida total.
- [ ] Clave cifrada en reposo; contraseña fuera del repositorio.
- [ ] La seed phrase **no** está en el sistema, en ningún formato.
- [ ] `SIGNER_MODE` distinto de `disabled` y signer arrancando en su propio contenedor.
- [ ] Puerto del signer **no publicado** al host.
- [ ] `SIGNER_PROGRAM_ALLOWLIST` rellenada con los program IDs mínimos necesarios.
- [ ] `SIGNER_MAX_DAILY_SOL` configurado y probado: el signer rechaza al superarlo.
- [ ] Probado que el signer rechaza un programa fuera de la allowlist.
- [ ] Probado que el signer rechaza una transferencia a destino arbitrario.
- [ ] Cada solicitud de firma queda registrada.

## E. Requisitos de configuración

- [ ] Todos los límites de `RISK_POLICY.md` revisados uno a uno y aceptados.
- [ ] `LIVE_TRADING_MAX_ORDER_SOL` al mínimo operable.
- [ ] `LIVE_TRADING_MAX_OPEN_POSITIONS=1`.
- [ ] `LIVE_TRADING_MAX_DAILY_SOL` muy reducido.
- [ ] Kill switch manual probado desde el dashboard.
- [ ] `configuration_versions` registra la configuración exacta con la que se activa.

## F. Activación

- [ ] Pruebas previas en devnet o con importes mínimos en mainnet.
- [ ] `ENABLE_LIVE_TRADING=true`.
- [ ] Confirmación explícita en la interfaz.
- [ ] PIN o segundo factor introducido.
- [ ] Revisión final del usuario, con nombre y fecha.

---

## Después de activar

No termina aquí. Durante las primeras operaciones reales:

1. **Una** posición simultánea, con el importe mínimo.
2. Límite diario muy reducido.
3. **Revisión manual después de cada operación**: comparar lo ejecutado contra lo simulado —
   precio, slippage, fee, latencia.
4. Si la ejecución real se desvía sistemáticamente de la simulada, se vuelve a `PAPER` y se
   recalibra el simulador. Una desviación no es mala suerte: es un modelo equivocado.
5. Cualquier anomalía → kill switch primero, investigar después.

---

## Firma de activación

```
Fecha:              ____________________
Responsable:        ____________________
Commit:             ____________________
Capital asignado:   ____________ SOL
Evidencia:          docs/live-activation/____________/
```

Sin este bloque relleno y archivado, la activación no es válida.
