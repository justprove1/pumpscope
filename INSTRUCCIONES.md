# Cómo usar estos archivos

## Preparación (una sola vez)
1. Crea una carpeta vacía para el proyecto y ábrela con Claude Code.
2. Copia dentro `SPEC.md` y `CLAUDE.md`. (Claude Code lee `CLAUDE.md` solo, en cada sesión.)
3. Los archivos `FASE-*.md` NO van en el repo: son los prompts que pegas tú.

## Ejecución (de una fase por sesión)
1. Empieza una sesión limpia de Claude Code.
2. Abre `FASE-0-arranque.md`, copia TODO su contenido y pégalo como mensaje.
3. Deja que termine y lea su reporte final (la plantilla de CLAUDE.md §5).
4. Revisa. Si está bien, empieza OTRA sesión limpia y pega `FASE-1-...`. Y así sucesivamente.

## Reglas de oro
- Nunca pegues `SPEC.md` entero como orden de "constrúyelo todo". Solo vive en el repo como referencia.
- Una fase por sesión. No mezcles fases.
- No avances si los tests de la fase anterior están en rojo.
- La Fase 0 no escribe lógica (solo arquitectura y esqueleto). Es intencional.
- LIVE (dinero real) queda bloqueado hasta cumplir el checklist de SPEC.md §30, ya en la Fase 6+.

## Orden de las fases
- FASE 0 — Arranque: arquitectura, repo, base de datos, interfaces. Sin lógica.
- FASE 1 — Detección de tokens en tiempo real + dashboard de solo lectura.
- FASE 2 — Análisis on-chain, detección de manipulación y narrativas.
- FASE 3 — Simulador realista + backtesting.
- FASE 4 — Señales, gestión de riesgo determinista y alertas.
- FASE 5 — Modelos de machine learning + monitorización.
- FASE 6 — ExecutionEngine + signer aislado (LIVE deshabilitado).
