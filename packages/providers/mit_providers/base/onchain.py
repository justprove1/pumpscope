"""Lectura y streaming on-chain (SPEC.md 4.A).

Es la unica dependencia realmente critica del sistema. Todo lo demas es enriquecimiento.

INTERFACES ABSTRACTAS, SIN IMPLEMENTACION.
"""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import AsyncIterator, Sequence
from datetime import datetime

from mit_data_models import Observation, SimulationResult, TradeEvent

from mit_providers.base.common import Provider


class OnChainReadProvider(Provider):
    """Lectura puntual de estado de la cadena.

    Todo devuelve `Observation[...]`: el envelope no es opcional ni siquiera para datos
    on-chain, porque la latencia entre el slot y nuestra lectura tambien importa.
    """

    @abstractmethod
    async def get_slot(self) -> Observation[int]:
        """Slot confirmado mas reciente."""

    @abstractmethod
    async def get_account_data(self, address: str) -> Observation[bytes | None]:
        """Datos crudos de una cuenta. `None` si no existe."""

    @abstractmethod
    async def get_multiple_accounts(
        self, addresses: Sequence[str]
    ) -> Observation[list[bytes | None]]:
        """Lectura por lotes. Preserva el orden de `addresses`."""

    @abstractmethod
    async def get_token_supply(self, mint: str) -> Observation[int]:
        """Supply total en unidades base."""

    @abstractmethod
    async def get_token_largest_accounts(
        self, mint: str, limit: int = 20
    ) -> Observation[list[tuple[str, int]]]:
        """Mayores tenedores como (direccion, cantidad), de mayor a menor."""

    @abstractmethod
    async def get_transaction(self, signature: str) -> Observation[dict[str, object] | None]:
        """Transaccion confirmada, ya decodificada por el proveedor.

        El tipo de retorno es deliberadamente laxo: la forma exacta depende del proveedor y
        se normaliza en `mit_pumpfun`, no aqui.
        """

    @abstractmethod
    async def get_signatures_for_address(
        self,
        address: str,
        limit: int = 1000,
        before: str | None = None,
        until: str | None = None,
    ) -> Observation[list[str]]:
        """Firmas que tocaron una cuenta, de mas reciente a mas antigua.

        Es la primitiva con la que se reconstruye el historial de un creador y el grafo de
        financiacion de wallets.
        """

    @abstractmethod
    async def simulate_transaction(self, serialized_tx: bytes) -> Observation[SimulationResult]:
        """Simula una transaccion SIN enviarla.

        Obligatoria antes de cualquier compra: simular la venta es la unica deteccion fiable
        de honeypot (veto `sell_simulation_failed`).
        """

    @abstractmethod
    async def get_priority_fee_estimate(self, program_ids: Sequence[str]) -> Observation[int]:
        """Priority fee sugerido, en microlamports por unidad de computo."""


class EventStreamProvider(Provider):
    """Streaming de eventos en tiempo real (WebSocket).

    Todos los metodos devuelven `AsyncIterator`, no callbacks: el consumidor controla la
    contrapresion. Con callbacks, un consumidor lento acumula memoria en silencio hasta que
    el proceso muere.

    El contrato incluye reconexion transparente: el iterador NO termina porque se caiga la
    conexion. Se reconecta con backoff y sigue. Solo termina si se cancela desde fuera.
    """

    @abstractmethod
    def subscribe_program_logs(
        self, program_id: str
    ) -> AsyncIterator[Observation[dict[str, object]]]:
        """Logs de un programa. Es como se detectan los tokens nuevos."""

    @abstractmethod
    def subscribe_account(self, address: str) -> AsyncIterator[Observation[bytes]]:
        """Cambios en una cuenta. Es como se sigue la bonding curve sin hacer polling."""

    @abstractmethod
    def subscribe_slots(self) -> AsyncIterator[Observation[int]]:
        """Slots nuevos. Sirve de heartbeat: si dejan de llegar, la conexion esta muerta
        aunque el socket siga abierto.
        """

    @abstractmethod
    async def replay_since(
        self, program_id: str, since: datetime
    ) -> AsyncIterator[Observation[TradeEvent]]:
        """Reproduce eventos perdidos tras una desconexion.

        Sin esto, cada corte de red deja un agujero permanente en los datos y el backtest
        posterior miente (SPEC.md 25: event replay y checkpoints).
        """
