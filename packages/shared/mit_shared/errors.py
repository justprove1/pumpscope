"""Jerarquia de errores del sistema.

Toda excepcion propia hereda de `MitError`, para que capturar errores del sistema nunca
capture por accidente un `KeyboardInterrupt` o un bug de programacion.
"""

from __future__ import annotations


class MitError(Exception):
    """Raiz de todos los errores del sistema."""


class ConfigurationError(MitError):
    """Configuracion ausente, incoherente o insegura."""


class ValidationError(MitError):
    """Un dato externo no cumple su contrato.

    Una respuesta de API que no valida es un error, no un dato (CLAUDE.md 2).
    """


class SafetyViolationError(MitError):
    """Se ha intentado cruzar un guardarrail de CLAUDE.md 1.

    Nunca se captura para continuar. Detiene la operacion y genera alerta critica.
    """
