# Seguridad

Este documento cubre el manejo de claves, la arquitectura de firma y el modelo de amenazas.
Sus reglas derivan de `CLAUDE.md` §1 y `SPEC.md` §16, y **prevalecen sobre cualquier otra
consideración** (rendimiento, comodidad, latencia).

---

## 1. Invariables

Estas afirmaciones deben ser ciertas en todo momento y en toda rama del código:

1. La aplicación arranca en `DRY_RUN`. `LIVE` requiere acción humana explícita.
2. No existe ninguna ruta de código en la que una clave privada llegue a `apps/api`,
   `apps/worker` o `apps/web`.
3. No existe ninguna ruta de código en la que una seed phrase se lea, almacene, registre o
   transmita. El sistema no la necesita: opera con una clave de una wallet dedicada.
4. Ninguna clave, token o secreto aparece en logs, trazas, mensajes de error, alertas ni
   respuestas de la API. El logger aplica redacción por lista de claves sensibles.
5. Ningún LLM firma, ni decide importes, ni modifica límites, ni salta validaciones.
6. Toda solicitud de firma queda registrada antes de ejecutarse, con su resultado.

Si una de estas deja de cumplirse, es un incidente de seguridad, no un bug.

---

## 2. Arquitectura de firma

```
┌──────────────────┐        tx sin firmar        ┌──────────────────┐
│  ExecutionEngine │  ─────────────────────────▶ │   apps/signer    │
│  (apps/worker)   │  ◀───────────────────────── │  proceso aislado │
└──────────────────┘        tx firmada           └────────┬─────────┘
        │                                                 │
        │ NO tiene acceso                                  │ única lectura
        │ al material criptográfico                        ▼
        │                                        ┌──────────────────┐
        └───────────────────────────────────────▶│  clave cifrada   │
                       nunca                     │  libsodium/KMS   │
                                                 └──────────────────┘
```

- **Transporte:** HTTP sobre red interna de Docker. El puerto del signer **no se publica** al
  host ni a internet.
- **Autenticación:** HMAC de cuerpo + timestamp con `SIGNER_SHARED_SECRET`, con ventana
  anti-replay. Un HMAC válido no basta: la transacción se valida igualmente.
- **Modos** (`SIGNER_MODE`): `disabled` (por defecto) · `local_encrypted` (libsodium) ·
  `keychain` (macOS) · `hardware` · `kms` (fases avanzadas).

### Validaciones del signer

El signer rechaza toda transacción que no cumpla **todas** estas condiciones. Las evalúa él
mismo; no confía en que el llamante ya las haya comprobado:

| # | Validación |
|---|---|
| 1 | Origen autenticado y es el `ExecutionEngine` autorizado |
| 2 | Todos los `program_id` invocados están en `SIGNER_PROGRAM_ALLOWLIST` |
| 3 | El importe de la orden no supera `LIVE_TRADING_MAX_ORDER_SOL` |
| 4 | El acumulado del día no supera `SIGNER_MAX_DAILY_SOL` (contador propio, persistente) |
| 5 | No hay transferencia de SOL o SPL a direcciones fuera de la allowlist de destinos |
| 6 | No se crean ni delegan autoridades (`SetAuthority`, `Approve`, `CloseAccount` inesperados) |
| 7 | La transacción no incluye instrucciones desconocidas o no decodificables |
| 8 | El `recent_blockhash` es reciente y la transacción no ha sido firmada ya (idempotencia) |
| 9 | El modo global es `LIVE` y `ENABLE_LIVE_TRADING=true` |

El contador diario es del signer, **no** del backend. Si el backend se compromete, el límite
sigue en pie.

---

## 3. Gestión de claves

**Permitido**

- Wallet dedicada, exclusiva de trading, con capital limitado y desechable.
- Clave cifrada en reposo con libsodium; contraseña en archivo con permisos `0400` fuera del
  árbol del repositorio, o inyectada como secreto de Docker.
- Keychain del sistema operativo.
- Hardware wallet, si permite firma automática.
- KMS/HSM.

**Prohibido**

- Seed phrase en cualquier forma, en cualquier lugar.
- Clave en texto plano en disco, variable de entorno, base de datos o imagen de contenedor.
- Reutilizar la wallet principal del usuario.
- Copiar la clave a la máquina de desarrollo o a CI.
- Enviar la clave, cifrada o no, por cualquier canal de red que no sea el arranque del signer.

**Rotación:** ante cualquier sospecha, se genera wallet nueva, se mueve el capital y se revoca
la anterior. No hay procedimiento de recuperación: la wallet es desechable por diseño.

---

## 4. Modelo de amenazas

| Amenaza | Vector | Mitigación |
|---|---|---|
| Exfiltración de clave | Compromiso del backend o de una dependencia | El backend nunca la tiene. Aislamiento de proceso y contenedor. |
| Firma no autorizada | Atacante alcanza el signer | HMAC + allowlist de programas + límite por orden + límite diario + allowlist de destinos |
| Prompt injection vía LLM | Metadatos de token, nombre, URI o post social manipulados | La salida del LLM se valida contra esquema JSON y solo alimenta `NarrativeScore`. No toca riesgo, importes ni firma. |
| Drenaje por token malicioso | Honeypot, freeze authority, transfer hook | Reglas de elegibilidad §12 + simulación de venta obligatoria antes de comprar |
| Doble gasto por reintentos | Timeout de red durante el envío | Idempotency key por intención de orden; reintentos acotados; reconciliación on-chain |
| Proveedor comprometido o mintiendo | API de tercero devuelve datos falsos | Nunca una sola fuente para ejecutar. Divergencia entre fuentes reduce confianza y puede activar kill switch. |
| MEV / sandwich | Mempool pública | `max_price_impact`, `min_expected_output`, `simulateTransaction` previa, priority fees adaptativos |
| Fuga de secretos en logs | Log accidental de payload completo | Redacción por lista en el logger; test que verifica que patrones de clave no aparecen en la salida |
| Secretos en el repositorio | Commit accidental de `.env` | `.gitignore` explícito + escaneo de secretos en CI |
| Escalada por dependencia | Paquete malicioso en la cadena de suministro | Versiones fijadas, `pip-audit`/`npm audit` en CI, signer con el mínimo de dependencias |

---

## 5. Secretos en desarrollo y CI

- `.env` está en `.gitignore`. Solo se versiona `.env.example`, sin valores.
- CI **no** tiene credenciales de proveedores ni claves de wallet. Los tests que las requerirían
  se marcan `@pytest.mark.live` y no se ejecutan nunca en CI.
- Los tests usan fixtures capturadas de respuestas reales y anonimizadas, nunca respuestas
  inventadas (`CLAUDE.md` §2).
- Las imágenes de contenedor no llevan secretos horneados; se inyectan en tiempo de ejecución.

---

## 6. Reporte de vulnerabilidades

Proyecto privado. Si encuentras un fallo que afecte al manejo de claves o al signer: detén el
trading (`ENABLE_LIVE_TRADING=false`), documenta el hallazgo en `docs/` y avisa al responsable
antes de escribir el arreglo.
