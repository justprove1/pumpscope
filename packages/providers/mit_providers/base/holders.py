"""Holders, wallets y relaciones entre ellas (SPEC.md 7, 8).

Todo lo que hay aqui se puede resolver 100% on-chain (DATA_PROVIDERS.md 4). La interfaz
existe para permitir un adaptador agregador mas rapido, no porque haga falta uno.

INTERFACES ABSTRACTAS, SIN IMPLEMENTACION.
"""

from __future__ import annotations

from abc import abstractmethod
from datetime import datetime

from mit_data_models import CreatorProfile, HolderDistribution, Observation

from mit_providers.base.common import Provider


class HolderProvider(Provider):
    """Distribucion de tenedores."""

    @abstractmethod
    async def get_distribution(self, mint: str) -> Observation[HolderDistribution]:
        """Distribucion actual.

        Los porcentajes ajustados deben EXCLUIR pools y cuentas identificadas. Incluir el
        pool en la concentracion la subestima siempre y hace pasar por sano un token que no
        lo esta.
        """

    @abstractmethod
    async def get_holders(self, mint: str, limit: int = 1000) -> Observation[list[tuple[str, int]]]:
        """Tenedores como (wallet, cantidad), de mayor a menor."""


class WalletGraphProvider(Provider):
    """Grafo de financiacion y relaciones entre wallets.

    Es la primitiva sobre la que se construye toda la deteccion de manipulacion: bundles,
    sybil, wallet splitting, clusters de insiders y cohortes persistentes entre lanzamientos.
    """

    @abstractmethod
    async def get_funding_source(self, wallet: str) -> Observation[str | None]:
        """Quien financio esta wallet por primera vez."""

    @abstractmethod
    async def get_funded_wallets(self, wallet: str, limit: int = 500) -> Observation[list[str]]:
        """Wallets financiadas por esta."""

    @abstractmethod
    async def get_first_activity(self, wallet: str) -> Observation[datetime | None]:
        """Primera actividad de la wallet.

        Una wallet creada minutos antes del lanzamiento es una senal fuerte por si sola.
        """

    @abstractmethod
    async def get_creator_profile(self, creator: str) -> Observation[CreatorProfile]:
        """Historial del creador: cuantos tokens hizo y como acabaron.

        `reputation_score` debe venir `None` si no hay historial suficiente. "Sin datos" y
        "riesgo medio" no son lo mismo, y confundirlos deja pasar creadores desconocidos como
        si estuvieran validados.
        """
