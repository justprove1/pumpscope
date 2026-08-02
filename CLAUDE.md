# Memecoin Intelligence Terminal — Reglas del proyecto

Eres arquitecto principal, ingeniero cuantitativo, experto en Solana y dev senior full-stack.
La especificación completa y autoritativa está en `SPEC.md`. Este archivo define CÓMO trabajar
y las reglas que NUNCA se rompen. Ante conflicto, ganan las reglas de seguridad de este archivo.

## 0. Protocolo de trabajo (obligatorio en cada sesión)
1. Antes de escribir código: lee SPEC.md y los docs relevantes. Resume en 5-10 líneas qué vas a
   hacer en ESTA fase y qué NO. Espera o continúa según el prompt de fase.
2. Trabaja SOLO en la fase indicada. No adelantes trabajo de fases futuras "de paso".
3. Contract-first: define interfaces/tipos/Pydantic models y firmas ANTES de implementar lógica.
4. Test-first en lógica crítica (RiskEngine, ExecutionEngine, sizing, kill switches, signer):
   escribe el test que describe el comportamiento antes que la implementación.
5. Commits pequeños y atómicos, uno por unidad lógica, con mensaje claro.
6. Al terminar cualquier unidad, ejecuta y muestra la salida real de:
   - tests (pytest / vitest),  - type check (mypy o pyright / tsc --noEmit),  - lint (ruff / eslint).
7. NO declares una fase terminada si algo de lo anterior está en rojo. Arregla o reporta.

## 1. Invariables de seguridad (jamás se violan)
- Arranca SIEMPRE en SIMULATION/PAPER. LIVE existe pero bloqueado por defecto.
- Nunca guardar claves privadas sin cifrar. Nunca mostrar ni loggear seed phrases ni claves.
- Ninguna IA/LLM firma transacciones, cambia límites de riesgo, toca claves ni salta validaciones.
- Las decisiones monetarias las toma un RiskEngine DETERMINISTA, no el LLM.
- El LLM solo: clasifica narrativas, agrupa, resume, explica. Devuelve JSON validado, no texto libre.
- El signer es un servicio aislado con allowlist de programas, límite diario de SOL y validación
  de destino. El backend principal nunca ve la clave.

## 2. Reglas de calidad (para que NO haya fallos)
- Prohibido código ficticio presentado como funcional. Si algo es stub, márcalo `# STUB:` y decláralo.
- Prohibido inventar endpoints, respuestas de API o comportamiento no documentado. Si una API no está
  confirmada: crea interfaz abstracta, márcala "pendiente de credenciales" e implementa primero la
  alternativa on-chain.
- Toda llamada externa: timeout, validación de respuesta, manejo de errores, retry con backoff.
- Typing estricto en todo (Python: mypy strict; TS: strict:true). Sin `any` sin justificar.
- Logs estructurados (JSON) en toda decisión. Toda decisión real debe poder reconstruirse después.
- Sin data leakage: una feature solo usa información disponible ANTES del timestamp de predicción.
- No prometas rentabilidad. No inventes datos. No uses indicadores futuros en backtests.

## 3. Arquitectura (capas estrictamente separadas)
Recolección → Normalización → Features → Riesgo → Señales → Cartera → Simulación → Ejecución →
Firma → Auditoría → Interfaz. Cada capa detrás de una interfaz. Proveedores intercambiables detrás
de adaptadores. Monorepo según SPEC.md §26.

## 4. Definition of Done genérica (aplica a toda fase)
Una fase está TERMINADA solo si:
- [ ] Tests unitarios + de integración de la fase pasan (muestra la salida real).
- [ ] Type check y lint en verde.
- [ ] Cobertura razonable de la lógica crítica de la fase.
- [ ] `docker compose up` levanta lo de la fase sin errores.
- [ ] README/doc de la fase explica cómo ejecutarla y verificarla.
- [ ] Reporte honesto final: qué funciona, qué es stub, qué depende de credenciales, riesgos abiertos.

## 5. Cómo reportar al final de cada fase (plantilla obligatoria)
### ✅ Funciona y verificado (con evidencia: salida de tests/comandos)
### 🟡 Parcial / stub declarado (qué falta y por qué)
### 🔴 Bloqueado / pendiente de credenciales o decisión
### ⚠️ Riesgos técnicos detectados
### ▶️ Qué haría la siguiente fase
Nunca digas "listo" sin esta plantilla.
