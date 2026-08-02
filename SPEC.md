# Memecoin Intelligence Terminal — Especificación (SPEC)

> Documento de referencia autoritativo del proyecto (el "qué"). Las reglas de ejecución y los
> guardarraíles (el "cómo") están en `CLAUDE.md`. Los prompts de ejecución están en los archivos
> `FASE-*.md` y se usan de uno en uno. Ante conflicto de seguridad, gana `CLAUDE.md`.

Actúa como arquitecto principal de software, ingeniero cuantitativo, experto en Solana,
especialista en sistemas de baja latencia y desarrollador senior full-stack.
Quiero que construyas una plataforma profesional para detectar, analizar, simular,
comprar y vender memecoins de Pump.fun y PumpSwap en tiempo real.

NOMBRE TEMPORAL DEL PROYECTO:
Memecoin Intelligence Terminal

IMPORTANTE:
No quiero únicamente un script ni una demo visual.
Quiero un sistema funcional, modular, auditable y preparado para funcionar 24/7.
La plataforma debe comenzar obligatoriamente en modo SIMULATION/PAPER TRADING.
El modo LIVE debe existir, pero permanecer bloqueado hasta que:
1. Pasen todos los tests.
2. Se completen suficientes operaciones simuladas.
3. El usuario active manualmente varios controles de seguridad.
4. Se configure una wallet independiente con capital limitado.

No prometas rentabilidad.
No inventes datos.
No uses indicadores futuros para los backtests.
No guardes nunca claves privadas sin cifrar.
No muestres ni envíes frases semilla.
No permitas que una IA generativa firme directamente transacciones.

==================================================
1. OBJETIVO GENERAL
==================================================
Crear una plataforma capaz de:
1. Detectar inmediatamente nuevos tokens de Pump.fun.
2. Monitorizar la bonding curve, PumpSwap y otros DEX de Solana.
3. Analizar datos on-chain, sociales, narrativos y de mercado.
4. Detectar rugs, insiders, bundles, wallets conectadas y volumen artificial.
5. Calcular un score dinámico para cada token.
6. Simular entradas y salidas con latencia, comisiones y slippage realistas.
7. Aprender cuáles de sus señales funcionan y cuáles no.
8. Ejecutar compras y ventas reales solo cuando el sistema esté validado.
9. Gestionar automáticamente stop-loss, take-profit y trailing stop.
10. Detenerse automáticamente si supera límites de riesgo.
11. Explicar claramente por qué abrió o cerró cada operación.
12. Mostrar el rendimiento en un dashboard profesional.

==================================================
2. PRINCIPIOS DEL SISTEMA
==================================================
El sistema debe separar estrictamente:
- Recolección de datos.
- Normalización.
- Creación de características.
- Evaluación de riesgos.
- Generación de señales.
- Gestión de cartera.
- Simulación.
- Ejecución real.
- Firma de transacciones.
- Auditoría.
- Interfaz.

La inteligencia artificial no debe decidir libremente cuánto dinero gastar.
Las decisiones monetarias deben estar limitadas por un motor determinista
de gestión de riesgo.

El LLM puede:
- Clasificar narrativas.
- Agrupar noticias.
- Detectar significado semántico.
- Resumir datos.
- Explicar decisiones.

El LLM no puede:
- Firmar transacciones.
- Cambiar límites de riesgo.
- Acceder directamente a claves privadas.
- Saltarse validaciones.
- Ejecutar una operación únicamente por intuición textual.

==================================================
3. TECNOLOGÍAS
==================================================
Usa preferentemente:

BACKEND:
- Python 3.12.
- FastAPI.
- asyncio.
- WebSockets.
- Pydantic v2.
- SQLAlchemy.
- PostgreSQL.
- TimescaleDB si resulta apropiado.
- Redis para caché, colas y estado temporal.
- Celery, Dramatiq o Arq para trabajos asíncronos.
- Polars para procesamiento rápido.
- Pandas solamente cuando sea necesario.
- NumPy.
- scikit-learn.
- LightGBM o XGBoost para modelos tabulares.
- Optuna para ajuste de parámetros.

SOLANA:
- solders.
- solana-py.
- Helius RPC/WebSocket/Webhooks.
- Pump.fun public program documentation.
- Jupiter Swap API.
- Transacciones versionadas de Solana.
- Simulación previa mediante simulateTransaction.
- Compute budget y priority fees adaptativos.

FRONTEND:
- Next.js.
- TypeScript estricto.
- React.
- Tailwind CSS.
- shadcn/ui.
- TradingView Lightweight Charts.
- WebSockets para actualizaciones en tiempo real.

INFRAESTRUCTURA:
- Docker.
- Docker Compose.
- GitHub Actions.
- Prometheus.
- Grafana.
- Sentry.
- OpenTelemetry.
- Nginx o Caddy.
- Variables de entorno.
- Secret manager compatible.

TESTING:
- pytest.
- pytest-asyncio.
- Vitest.
- Playwright.
- Tests unitarios.
- Tests de integración.
- Tests end-to-end.
- Tests de carga.
- Tests de resiliencia y desconexión.

==================================================
4. FUENTES DE DATOS
==================================================
Crea una capa de proveedores intercambiables.

A. DATOS DIRECTOS DE SOLANA
Usar:
- Helius WebSocket.
- Helius Webhooks.
- RPC Solana de respaldo.
- Lectura directa de cuentas y transacciones.
- Logs de los programas de Pump.fun y PumpSwap.
- Suscripciones a cuentas relevantes.
- Procesamiento de bloques y transacciones confirmadas.
Detectar:
- Creación de token.
- Primera compra.
- Compras y ventas.
- Migración.
- Cambios en bonding curve.
- Transferencias de holders importantes.
- Cambios de liquidez.
- Autoridad mint.
- Autoridad freeze.
- Token supply.
- Cuentas asociadas.
- Wallet del creador.
- Wallets financiadoras.
- Wallets relacionadas.
- Uso de bundles.
- Compras coordinadas.

B. PUMP.FUN Y PUMPSWAP
Implementar un adaptador basado en:
- Documentación pública del programa.
- Decodificación de instrucciones y cuentas.
- Lectura directa on-chain.
- Como respaldo opcional, proveedores externos configurables.
No depender exclusivamente de scraping HTML.
Registrar:
- Mint.
- Nombre.
- Símbolo.
- URI.
- Creador.
- Timestamp exacto.
- Progreso de bonding curve.
- SOL acumulado.
- Market cap estimada.
- Estado de migración.
- Pool posterior.
- Precio.
- Volumen.
- Número de operaciones.
- Compradores y vendedores únicos.

C. JUPITER
Usar Jupiter para:
- Cotizaciones.
- Rutas de swap.
- Price impact.
- Slippage.
- Construcción de transacciones.
- Comparación entre rutas.
No confiar en una sola cotización.
Antes de operar, solicitar una cotización nueva y verificar:
- input amount.
- output amount.
- price impact.
- ruta.
- antigüedad de la cotización.
- liquidez efectiva.

D. DEX SCREENER
Usar como fuente secundaria para:
- Pares.
- Liquidez.
- Volumen.
- FDV.
- Market cap.
- Transacciones.
- Token profiles.
- Boosts.
- Enlaces sociales.
- DEX y pool.
No usarlo como única fuente de verdad para ejecutar operaciones.

E. OTROS PROVEEDORES OPCIONALES
Diseñar adaptadores configurables para:
- Birdeye.
- Bitquery.
- Solana Tracker.
- QuickNode Metis.
- PumpPortal.
- RugCheck.
- Bubblemaps, cuando exista acceso legítimo.
- CoinGecko/GeckoTerminal.
- GMGN únicamente si existe una API permitida y documentada.
No violar términos de servicio.
No hacer scraping agresivo.
Añadir rate limiting, caché, backoff y circuit breakers.

F. DATOS SOCIALES Y NARRATIVOS
Integrar, cuando las credenciales estén disponibles:
- X API.
- Reddit API.
- Telegram mediante bots o canales permitidos.
- Discord mediante bots autorizados.
- GDELT.
- Google Trends mediante proveedor permitido.
- RSS de Reuters, AP, medios tecnológicos y fuentes oficiales.
- Comunicados de empresas.
- Truth Social solo mediante una fuente legal y permitida.
- YouTube Data API.
Registrar:
- Número de menciones.
- Velocidad de menciones.
- Cuentas únicas.
- Seguidores potencialmente alcanzados.
- Engagement.
- Ratio de cuentas nuevas.
- Ratio de bots estimado.
- Sentimiento.
- Idioma.
- País.
- Tema.
- Entidades mencionadas.
- Influencers relevantes.
- Primera aparición.
- Aceleración de la conversación.

==================================================
5. DATA PIPELINE
==================================================
Construye un pipeline con estas etapas:
1. Ingesta.
2. Validación.
3. Eliminación de duplicados.
4. Normalización temporal.
5. Resolución de identidades.
6. Enriquecimiento.
7. Feature engineering.
8. Persistencia.
9. Publicación en tiempo real.
10. Evaluación de señales.

Toda observación debe tener:
- provider.
- provider_timestamp.
- received_timestamp.
- blockchain_slot si aplica.
- confidence.
- latency_ms.
- raw_reference.
- normalized_value.

No mezclar datos con timestamps incompatibles.
Medir constantemente:
- retraso de cada proveedor;
- porcentaje de errores;
- datos ausentes;
- divergencia entre fuentes.
Si dos fuentes discrepan significativamente, reducir la confianza.

==================================================
6. DETECCIÓN DE TOKENS NUEVOS
==================================================
Crear un servicio NewTokenDetector que:
- Escuche continuamente los programas de Pump.fun.
- Detecte el mint lo antes posible.
- Registre la primera transacción.
- Identifique al creador.
- Analice quién financió la wallet del creador.
- Compruebe tokens anteriores del creador.
- Calcule el historial del creador.
- Detecte si varios creadores están conectados.
- Detecte compras en los primeros bloques.
- Reconstruya la primera cohorte de compradores.
- Calcule concentración y coordinación.

Objetivo de latencia:
- registrar un nuevo token en menos de 1 segundo desde que el evento llegue
  al proveedor;
- actualizar métricas críticas cada bloque o con la menor latencia razonable.

==================================================
7. ANÁLISIS ON-CHAIN
==================================================
Calcular para cada token:

IDENTIDAD:
- Edad exacta.
- Creador.
- Historial del creador.
- Fuente de financiación.
- Número de tokens creados anteriormente.
- Éxito y fracaso de tokens anteriores.
- Ventas anteriores del creador.
- Wallets relacionadas.

HOLDERS:
- Holders totales.
- Holders nuevos por minuto.
- Holders que abandonan por minuto.
- Top 1, Top 5, Top 10, Top 20.
- Concentración excluyendo pools y cuentas identificadas.
- Índice Herfindahl-Hirschman.
- Entropía de distribución.
- Gini coefficient.
- Porcentaje en clusters conectados.
- Wallets nuevas frente a antiguas.

FLUJO:
- Compras por segundo.
- Ventas por segundo.
- SOL de entrada.
- SOL de salida.
- Ratio buy/sell.
- Compradores únicos.
- Vendedores únicos.
- Compra media.
- Compra mediana.
- Venta media.
- Venta mediana.
- Tamaño máximo.
- Aceleración de entradas.
- Aceleración de salidas.

WHALES:
- Número de whales.
- Porcentaje de supply.
- Cost basis estimado.
- Beneficio no realizado.
- Tiempo desde entrada.
- Transferencias recientes.
- Ventas parciales.
- Porcentaje vendido.
- Movimiento hacia wallets o programas relacionados con swaps.

BUNDLES Y COORDINACIÓN:
- Compras en el mismo slot.
- Wallets financiadas por una misma fuente.
- Patrones de cantidades idénticas.
- Wallets creadas recientemente.
- Secuencia coordinada.
- Cohortes persistentes.
- Bundles Jito cuando puedan inferirse.
- Probabilidad de insider cluster.

LIQUIDEZ:
- Liquidez estimada.
- Profundidad efectiva.
- Price impact para:
  - 0,01 SOL
  - 0,05 SOL
  - 0,1 SOL
  - 0,25 SOL
  - 0,5 SOL
  - 1 SOL
- Slippage simulado.
- Variación de liquidez.
- Riesgo de salida.

==================================================
8. DETECCIÓN DE MANIPULACIÓN
==================================================
Crear módulos separados para:
- Wash trading.
- Self-trading.
- Volumen falso.
- Wallet splitting.
- Sybil wallets.
- Insider clusters.
- Bundled launches.
- Creator dumping.
- Liquidity extraction.
- Artificial social amplification.
- Bot swarm.
- Honeypot o imposibilidad práctica de venta.
- Token metadata fraud.
- Impersonation de marcas o figuras públicas.

Calcular un ManipulationRiskScore entre 0 y 100.
Guardar las razones concretas:
Ejemplo:
- 31% del supply pertenece a wallets financiadas por la misma dirección.
- 62% del volumen inicial proviene de 4 wallets.
- 8 de los primeros 10 compradores han coincidido en 17 lanzamientos.
- El creador vendió agresivamente en 5 de sus últimos 7 tokens.

==================================================
9. MOTOR DE NARRATIVAS
==================================================
Crear un NarrativeEngine.
Debe:
1. Detectar temas emergentes sin una lista fija.
2. Agrupar menciones semánticamente equivalentes.
3. Extraer entidades:
   - personas;
   - empresas;
   - países;
   - productos;
   - eventos;
   - memes;
   - frases.
4. Comparar nombres, símbolos e imágenes de tokens con narrativas.
5. Medir crecimiento y agotamiento.
6. Separar atención real de spam.
7. Detectar cuál token domina una narrativa.

Estados de narrativa:
- NASCENT.
- EMERGING.
- ACCELERATING.
- VIRAL.
- SATURATED.
- DECELERATING.
- EXHAUSTED.
- REVIVING.

Calcular:
- NarrativeScore 0–100.
- MentionVelocity.
- MentionAcceleration.
- UniqueAuthorGrowth.
- InfluencerScore.
- NewsQualityScore.
- CrossPlatformSpread.
- SpamProbability.
- NarrativeAge.
- NarrativeHalfLife estimada.
- TokenNarrativeFit.
- DominanceWithinNarrative.

El LLM debe devolver JSON estructurado, no texto libre.
Ejemplo:
{
  "narrative": "Tesla humanoid robotics",
  "state": "ACCELERATING",
  "score": 87,
  "confidence": 0.76,
  "reasons": [
    "Menciones únicas aumentan 320% en 20 minutos",
    "Tres cuentas verificadas publicaron contenido relacionado",
    "El tema apareció en dos fuentes oficiales"
  ]
}
No considerar una narrativa confirmada basándose únicamente en publicaciones
del creador del token.

==================================================
10. FEATURE ENGINEERING
==================================================
Generar características por ventanas:
- 5 segundos.
- 15 segundos.
- 30 segundos.
- 1 minuto.
- 3 minutos.
- 5 minutos.
- 15 minutos.
- 1 hora.

Incluir:
- Returns.
- Log returns.
- Volatilidad realizada.
- Momentum.
- Aceleración.
- Drawdown.
- Distancia al ATH.
- Distancia al mínimo.
- Volumen relativo.
- Buy pressure.
- Sell pressure.
- Holder velocity.
- Holder retention.
- Whale inflow/outflow.
- Price impact.
- Liquidity change.
- Narrative momentum.
- Social acceleration.
- Creator risk.
- Cluster risk.
- Token age.
- Bonding curve progress.
- Migration probability.
- Spread aproximado.
- Latencia de datos.
- Calidad de las fuentes.

Evitar data leakage.
Todas las características deben usar únicamente información disponible antes
del timestamp de la predicción.

==================================================
11. SCORE DEL TOKEN
==================================================
Crear varios scores independientes:
- NarrativeScore.
- MomentumScore.
- LiquidityScore.
- HolderQualityScore.
- DistributionScore.
- CreatorScore.
- WhaleScore.
- SocialAuthenticityScore.
- ExitLiquidityScore.
- ManipulationRiskScore.
- RugRiskScore.
- ExecutionQualityScore.
- DataConfidenceScore.

Crear un OpportunityScore final entre 0 y 100.
No utilizar inicialmente pesos arbitrarios permanentes.
Implementar dos modos:

A. HEURISTIC MODE
Pesos configurables y explicables.

B. MODEL MODE
Modelo entrenado con datos históricos y validación walk-forward.

Ejemplo inicial orientativo:
OpportunityScore =
  0.18 * NarrativeScore +
  0.15 * MomentumScore +
  0.12 * LiquidityScore +
  0.10 * HolderQualityScore +
  0.10 * DistributionScore +
  0.08 * CreatorScore +
  0.07 * WhaleScore +
  0.08 * SocialAuthenticityScore +
  0.07 * ExitLiquidityScore +
  0.05 * DataConfidenceScore
  - penalizaciones de manipulación y rug.

No comprar únicamente porque el score es alto.
Exigir también reglas de elegibilidad.

==================================================
12. REGLAS DE ELEGIBILIDAD
==================================================
Un token no puede comprarse si:
- DataConfidenceScore < umbral.
- RugRiskScore > umbral.
- ManipulationRiskScore > umbral.
- Liquidez efectiva insuficiente.
- No existe una ruta de salida verificable.
- La simulación de venta falla.
- El impacto estimado supera el máximo.
- La wallet del creador tiene historial crítico.
- Top holders ajustados superan el límite.
- Existe un cluster dominante peligroso.
- El token ya ha subido por encima del límite configurado.
- La narrativa ya está agotada.
- El spread o slippage es excesivo.
- Los datos están atrasados.
- Existe divergencia grave entre fuentes.
- El sistema no dispone de suficiente SOL para compra, comisiones y salida.
- El límite diario de riesgo está alcanzado.

==================================================
13. MOTOR DE SEÑALES
==================================================
Generar señales:
- WATCH.
- PREPARE.
- ENTER_SMALL.
- ENTER.
- ADD_FORBIDDEN por defecto.
- REDUCE.
- TAKE_PROFIT.
- EXIT.
- EMERGENCY_EXIT.
- IGNORE.

Cada señal debe incluir:
- timestamp;
- token;
- score;
- confidence;
- features principales;
- riesgos;
- cantidad recomendada por el motor de riesgo;
- precio esperado;
- slippage esperado;
- invalidation conditions;
- duración máxima;
- salida prevista.

No permitir averaging down automático en la primera versión.

==================================================
14. GESTIÓN DE RIESGO
==================================================
Construir un RiskEngine completamente determinista.

Configuración inicial conservadora:
- Riesgo por operación: 0,25%–1% del capital.
- Exposición máxima por token: 2%–5%.
- Exposición total simultánea: máximo configurable.
- Número máximo de operaciones abiertas.
- Pérdida diaria máxima.
- Pérdida semanal máxima.
- Máximo drawdown.
- Máximo de operaciones consecutivas perdedoras.
- Cooldown tras pérdidas.
- Cooldown por token.
- Prohibición de reentrada impulsiva.
- Reserva mínima de SOL para fees.

Para una cuenta pequeña, nunca arriesgar todo el saldo.
El tamaño de posición debe depender de:
- saldo;
- distancia al stop;
- liquidez;
- volatilidad;
- slippage;
- confianza;
- pérdida diaria acumulada;
- correlación con otras posiciones.

Implementar:
- hard stop.
- soft stop.
- trailing stop.
- time stop.
- liquidity stop.
- narrative stop.
- whale exit stop.
- partial take profit.
- break-even stop.

Ejemplo de salida parcial configurable:
- 25% al +20%.
- 25% al +40%.
- 25% al +80%.
- 25% con trailing stop.
Esto debe optimizarse en simulación, no asumirse como estrategia ganadora.

KILL SWITCHES:
Detener todas las compras si:
- pérdida diaria > límite;
- drawdown > límite;
- proveedor crítico falla;
- latencia excede límite;
- tasas de error aumentan;
- divergencia de precios;
- wallet balance anómalo;
- transacciones duplicadas;
- exposición inesperada;
- firma no autorizada;
- cambios de configuración no aprobados.

==================================================
15. EJECUCIÓN DE OPERACIONES
==================================================
Crear un ExecutionEngine con tres modos:
1. DRY_RUN.
2. PAPER.
3. LIVE.

DRY_RUN:
- Solo genera señales.
PAPER:
- Simula entrada y salida.
LIVE:
- Envía transacciones reales.

El modo LIVE debe requerir:
- variable ENABLE_LIVE_TRADING=true;
- confirmación en interfaz;
- PIN o segundo factor;
- wallet de trading separada;
- límite máximo de capital;
- checklist de seguridad;
- tests superados;
- mínimo de operaciones simuladas;
- rendimiento mínimo configurable;
- revisión explícita del usuario.

Para cada compra o venta:
1. Obtener cotización actual.
2. Verificar antigüedad.
3. Estimar slippage.
4. Estimar priority fee.
5. Construir transacción.
6. Simular transacción.
7. Validar saldo.
8. Validar límites de riesgo.
9. Firmar mediante un servicio local aislado.
10. Enviar.
11. Confirmar.
12. Reconciliar tokens y SOL reales.
13. Registrar la operación completa.
14. Verificar que no haya quedado exposición residual inesperada.

No enviar múltiples órdenes duplicadas ante timeouts.
Utilizar idempotency keys.
Implementar reintentos limitados y seguros.
No perseguir indefinidamente una compra si el precio se mueve.

Configurar:
- max_quote_age_ms;
- max_price_impact;
- max_slippage_bps;
- max_priority_fee;
- max_retries;
- transaction_timeout;
- min_expected_output.

==================================================
16. SEGURIDAD DE LA WALLET
==================================================
Crear una arquitectura de firma separada.
Nunca almacenar la seed phrase.
Opciones aceptables:
- Wallet dedicada con clave cifrada mediante libsodium.
- Keychain del sistema operativo.
- Hardware wallet, si la firma automática lo permite.
- Servicio signer local aislado.
- KMS/HSM en fases avanzadas.

El backend principal no debe exponer la clave.
El servicio signer solo acepta transacciones que:
- provengan del ExecutionEngine autorizado;
- cumplan límites de importe;
- usen programas permitidos;
- no transfieran fondos a direcciones arbitrarias;
- no creen autoridades inesperadas;
- no excedan el límite diario.

Añadir una allowlist de programas.
Añadir un límite absoluto de SOL que puede gastar por día.
Registrar cada solicitud de firma.

==================================================
17. SIMULADOR DE TRADING
==================================================
Construir un simulador event-driven.
No debe limitarse a:
precio final - precio inicial.

Debe simular:
- latencia de detección;
- latencia de decisión;
- latencia de construcción;
- latencia de firma;
- latencia de inclusión en bloque;
- slippage;
- price impact;
- prioridad;
- comisiones;
- transacciones fallidas;
- cotizaciones caducadas;
- MEV adverso;
- cambios de precio antes de confirmación;
- liquidez real disponible;
- ventas parciales;
- imposibilidad de salida;
- competencia de otros bots.

Modos:
A. HISTORICAL REPLAY
Reproducir eventos históricos en orden temporal.
B. PAPER LIVE
Consumir datos reales sin enviar transacciones.
C. MONTE CARLO
Variar:
- latencia;
- slippage;
- fills;
- prioridad;
- fallos;
- price impact.
D. STRESS TEST
Simular:
- rug;
- venta de whale;
- caída de RPC;
- liquidez desapareciendo;
- congestión de Solana;
- 50% de caída instantánea;
- 10 transacciones fallidas;
- datos inconsistentes.

==================================================
18. BACKTESTING
==================================================
Implementar:
- Walk-forward validation.
- Train/validation/test separados por tiempo.
- Purged cross-validation cuando sea necesario.
- No usar datos futuros.
- No optimizar sobre todo el histórico.
- Costes reales.
- Slippage variable.
- Latencia variable.
- Survivorship bias control.
- Tokens fallidos incluidos.
- Rug pulls incluidos.
- Tokens sin liquidez incluidos.

Métricas:
- Total return.
- Net return.
- Win rate.
- Average win.
- Average loss.
- Profit factor.
- Expectancy.
- Sharpe.
- Sortino.
- Calmar.
- Maximum drawdown.
- Value at Risk.
- Expected shortfall.
- Consecutive losses.
- Recovery factor.
- Fill rate.
- Failed transaction rate.
- Slippage average/p95/p99.
- Rendimiento por narrativa.
- Rendimiento por market cap inicial.
- Rendimiento por edad.
- Rendimiento por score.
- Rendimiento por proveedor.
- Rendimiento fuera de muestra.

No declarar una estrategia válida basándose únicamente en win rate.
Una estrategia solo se considera candidata si:
- Es rentable neta de costes.
- Tiene profit factor aceptable.
- Mantiene resultados fuera de muestra.
- No depende de uno o dos outliers.
- Sobrevive a pruebas de latencia y slippage peores.
- Mantiene drawdown dentro del límite.
- Tiene suficiente número de operaciones.

==================================================
19. MODELOS DE MACHINE LEARNING
==================================================
No empezar con deep learning innecesario.

Primera fase:
- Logistic regression como baseline.
- Random Forest.
- LightGBM.
- XGBoost.
- Calibration de probabilidades.
- SHAP para explicabilidad.

Objetivos posibles:
- Probabilidad de graduación.
- Probabilidad de alcanzar +20% antes de -10%.
- Probabilidad de alcanzar +50% antes de -20%.
- Probabilidad de supervivencia a 5, 15 y 60 minutos.
- Probabilidad de venta de whale.
- Probabilidad de rug.
- Expected maximum favorable excursion.
- Expected maximum adverse excursion.

No predecir únicamente "subirá o bajará".
Usar triple-barrier labeling y horizontes temporales explícitos.
Calibrar probabilidades.
Mostrar:
- probabilidad;
- intervalo de confianza;
- calidad del modelo;
- fecha de entrenamiento;
- degradación reciente.

Detectar concept drift.
Desactivar modelos degradados automáticamente.

==================================================
20. APRENDIZAJE Y OPTIMIZACIÓN
==================================================
Registrar para cada decisión:
- señales disponibles;
- features;
- scores;
- decisión;
- operación;
- resultado;
- máximo favorable;
- máximo adverso;
- razón de salida;
- qué habría ocurrido con otras salidas.

Crear un StrategyLab para comparar:
- estrategias;
- filtros;
- stops;
- take profits;
- tamaños;
- ventanas;
- scores.

No permitir que el sistema modifique autónomamente el riesgo real.
Las nuevas estrategias deben:
1. Probarse históricamente.
2. Probarse fuera de muestra.
3. Probarse en paper trading.
4. Ser aprobadas manualmente.
5. Desplegarse como versión nueva.
6. Poder revertirse.

==================================================
21. DASHBOARD
==================================================
Crear un dashboard profesional con:

PÁGINA PRINCIPAL:
- Saldo.
- PnL diario.
- PnL acumulado.
- Drawdown.
- Riesgo utilizado.
- Posiciones.
- Estado del bot.
- Estado de proveedores.
- Alertas.
- Kill switch.

RADAR:
- Tokens nuevos.
- OpportunityScore.
- NarrativeScore.
- RugRisk.
- ManipulationRisk.
- Liquidez.
- Holders.
- Edad.
- Market cap.
- Volumen.
- Estado.

TOKEN DETAIL:
- Gráfico en tiempo real.
- Operaciones.
- Bonding curve.
- Liquidez.
- Holders.
- Clusters.
- Creator history.
- Whales.
- Narrativa.
- Social mentions.
- Score timeline.
- Razones de entrada/salida.
- Simulación de compra y venta.
- Price impact por tamaño.

SIMULACIÓN:
- Capital inicial.
- Configuración.
- Reproducción de operaciones.
- Equity curve.
- Drawdown.
- Métricas.
- Comparación de estrategias.
- Monte Carlo.
- Exportación CSV/JSON.

OPERACIONES:
- Historial.
- PnL realizado.
- PnL no realizado.
- Fees.
- Slippage.
- Tiempo de ejecución.
- Señal original.
- Explicación.

CONFIGURACIÓN:
- APIs.
- Límites.
- Wallet.
- Simulación.
- Estrategias.
- Notificaciones.
- Modo live.
- Kill switches.

==================================================
22. ALERTAS
==================================================
Implementar alertas por:
- Telegram.
- Discord.
- Email.
- Web push.

Tipos:
- Token con score alto.
- Nueva narrativa.
- Narrativa acelerando.
- Whale comprando.
- Whale vendiendo.
- Creador vendiendo.
- Liquidez cayendo.
- Manipulación detectada.
- Entrada simulada.
- Salida simulada.
- Entrada real.
- Salida real.
- Stop-loss.
- Take-profit.
- Kill switch.
- Proveedor caído.
- Error de wallet.
- Drawdown.
- Límite diario.

Cada alerta debe incluir datos verificables, no mensajes vagos.

==================================================
23. BASE DE DATOS
==================================================
Diseñar tablas para:
- tokens;
- token_metadata;
- creators;
- wallets;
- wallet_relationships;
- transactions;
- swaps;
- holders_snapshots;
- liquidity_snapshots;
- price_snapshots;
- bonding_curve_snapshots;
- social_posts;
- news_items;
- narratives;
- token_narrative_links;
- features;
- scores;
- signals;
- simulated_orders;
- live_orders;
- fills;
- positions;
- portfolio_snapshots;
- strategies;
- strategy_versions;
- model_versions;
- backtest_runs;
- alerts;
- provider_health;
- audit_logs;
- configuration_versions.

Usar migraciones Alembic.
Crear índices adecuados para series temporales y consultas por mint.

==================================================
24. OBSERVABILIDAD
==================================================
Registrar:
- ingesta por segundo;
- eventos descartados;
- latencia p50/p95/p99;
- latencia por proveedor;
- señales generadas;
- cotizaciones;
- transacciones;
- errores;
- retries;
- balance;
- exposición;
- slippage;
- price impact;
- divergencias.

Añadir dashboards Grafana.
Añadir trazas distribuidas.
Todas las decisiones reales deben poder reconstruirse posteriormente.

==================================================
25. RESILIENCIA
==================================================
Implementar:
- reconexión WebSocket;
- heartbeats;
- fallback RPC;
- circuit breakers;
- exponential backoff;
- rate limiters;
- deduplicación;
- event replay;
- checkpoints;
- idempotencia;
- recovery tras reinicio;
- reconciliación con estado on-chain.

Si el sistema pierde conexión:
- no abrir posiciones nuevas;
- mantener vigilancia de posiciones mediante proveedor de respaldo;
- activar salida segura si corresponde;
- avisar al usuario.

==================================================
26. ESTRUCTURA DEL REPOSITORIO
==================================================
Crea un monorepo:
/apps
  /api
  /web
  /worker
  /signer
/packages
  /solana
  /pumpfun
  /providers
  /data-models
  /features
  /strategies
  /risk
  /execution
  /simulation
  /ml
  /narratives
  /notifications
  /observability
  /shared
/infrastructure
  /docker
  /grafana
  /prometheus
  /migrations
/tests
  /unit
  /integration
  /e2e
  /load
  /fixtures
  /replay
/docs

==================================================
27. ARCHIVOS OBLIGATORIOS
==================================================
Crear:
- README.md
- ARCHITECTURE.md
- SECURITY.md
- RISK_POLICY.md
- DATA_PROVIDERS.md
- LIVE_TRADING_CHECKLIST.md
- BACKTESTING.md
- SIMULATION.md
- API.md
- .env.example
- docker-compose.yml
- Makefile
- pyproject.toml
- package.json
- GitHub Actions workflows
- database migrations
- seed/demo data
- sample configuration

==================================================
28. VARIABLES DE ENTORNO
==================================================
Incluir como mínimo:
APP_ENV=
DATABASE_URL=
REDIS_URL=
HELIUS_API_KEY=
HELIUS_RPC_URL=
HELIUS_WSS_URL=
JUPITER_API_KEY=
DEXSCREENER_BASE_URL=
X_API_KEY=
GDELT_ENABLED=
TELEGRAM_BOT_TOKEN=
DISCORD_WEBHOOK_URL=
ENABLE_LIVE_TRADING=false
LIVE_TRADING_MAX_DAILY_SOL=
LIVE_TRADING_MAX_ORDER_SOL=
LIVE_TRADING_MAX_TOTAL_EXPOSURE_SOL=
SIGNER_MODE=
ENCRYPTED_KEY_PATH=
KEY_ENCRYPTION_PASSWORD_FILE=
MAX_SLIPPAGE_BPS=
MAX_PRICE_IMPACT_PERCENT=
MAX_DAILY_LOSS_PERCENT=
MAX_DRAWDOWN_PERCENT=
No incluir claves reales.

==================================================
29. FASES DE DESARROLLO
==================================================
FASE 1:
- Arquitectura.
- Base de datos.
- Helius/WebSockets.
- Detector de tokens.
- Dashboard básico.
- Sin operaciones.
FASE 2:
- Métricas on-chain.
- Holders.
- Creadores.
- Whales.
- Riesgo.
- Narrativas.
FASE 3:
- Simulador.
- Paper trading.
- Slippage realista.
- Equity curve.
- Backtesting.
FASE 4:
- Estrategias heurísticas.
- Alertas.
- Gestión de riesgo.
- Explicabilidad.
FASE 5:
- Modelos ML.
- Walk-forward validation.
- Model monitoring.
FASE 6:
- ExecutionEngine.
- Jupiter/Pump.fun.
- Simulación de transacciones.
- Servicio signer.
- Live deshabilitado.
FASE 7:
- Auditoría de seguridad.
- Testnet/devnet cuando sea posible.
- Pruebas con importes mínimos.
- Activación manual de LIVE.
No avances a una fase si los tests de la anterior fallan.

==================================================
30. CRITERIOS PARA ACTIVAR LIVE
==================================================
El sistema no debe permitir LIVE hasta que:
- Se hayan simulado como mínimo 1.000 operaciones.
- Exista una muestra fuera de entrenamiento.
- Los costes estén incluidos.
- El profit factor y drawdown cumplan límites configurables.
- Los resultados no dependan de pocos outliers.
- Se hayan superado stress tests.
- La reconciliación on-chain funcione.
- Los kill switches estén verificados.
- La wallet esté limitada.
- El usuario complete el checklist.
- ENABLE_LIVE_TRADING sea true.
- El usuario confirme explícitamente en la interfaz.

Incluso después:
- comenzar con la cantidad mínima;
- máximo una posición simultánea;
- límite diario muy reducido;
- revisión después de cada operación.

==================================================
31. PRIMER ENTREGABLE
==================================================
No intentes generar todo de golpe sin organización.
Primero:
1. Analiza los requisitos.
2. Propón arquitectura.
3. Identifica riesgos técnicos.
4. Identifica APIs que necesitan credenciales.
5. Identifica funciones que pueden hacerse directamente on-chain.
6. Crea el árbol del repositorio.
7. Crea el esquema de base de datos.
8. Crea los contratos/interfaces de proveedores.
9. Implementa el detector de nuevos tokens.
10. Implementa un dashboard que muestre datos reales en modo lectura.
11. Añade tests.
12. Documenta cómo ejecutarlo.
Después continúa iterativamente.

==================================================
32. REGLAS DE CALIDAD
==================================================
- No uses código ficticio como si funcionara.
- No dejes TODOs críticos sin explicar.
- No simules respuestas reales de APIs.
- No inventes endpoints.
- Verifica cada endpoint en documentación actual.
- Aísla APIs no oficiales detrás de adaptadores.
- Añade timeouts.
- Añade validación de respuestas.
- Añade manejo de errores.
- Añade typing estricto.
- Añade tests.
- Añade logs estructurados.
- Añade documentación.
- Explica cada decisión importante.
Cuando una API no esté confirmada:
- no inventes su funcionamiento;
- crea una interfaz abstracta;
- marca el proveedor como pendiente de configuración;
- implementa primero una alternativa basada en datos on-chain.

==================================================
33. RESULTADO ESPERADO
==================================================
Quiero terminar con una plataforma que:
- descubre tokens en tiempo real;
- analiza miles de señales;
- detecta riesgos;
- puntúa oportunidades;
- explica sus decisiones;
- simula operaciones de forma realista;
- mide si una estrategia tiene ventaja;
- protege el capital;
- puede ejecutar operaciones reales;
- nunca depende de una sola API;
- nunca entrega el control directo de la wallet a un LLM;
- y puede detenerse instantáneamente ante una anomalía.
