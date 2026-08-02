# Fixtures

Respuestas **reales** capturadas de proveedores, anonimizadas.

Regla (CLAUDE.md 2): aqui no se inventa nada. Una fixture escrita a mano describe la API que
uno imagina, no la que existe, y hace que los tests pasen contra un adaptador roto.

Procedimiento para anadir una:

1. Llamar al endpoint real una vez.
2. Guardar la respuesta cruda tal cual.
3. Sustituir cualquier clave o identificador personal por un valor ficticio evidente.
4. Anotar en el propio archivo la fecha de captura y la version de la API.

Vacio en Fase 0: no se ha llamado a ninguna API.
