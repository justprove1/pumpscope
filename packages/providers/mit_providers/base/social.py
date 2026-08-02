"""Fuentes sociales y de noticias (SPEC.md 4.F).

Todo lo de este modulo requiere credenciales y NO tiene alternativa on-chain
(DATA_PROVIDERS.md 5). Sin estos proveedores el sistema sigue funcionando: los pesos del
OpportunityScore se renormalizan sobre lo disponible y el DataConfidenceScore baja. Es una
degradacion declarada, no un fallo silencioso.

INTERFACES ABSTRACTAS, SIN IMPLEMENTACION.
"""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import AsyncIterator, Sequence
from datetime import datetime

from mit_data_models import NewsItem, Observation, SocialPost

from mit_providers.base.common import Provider


class SocialProvider(Provider):
    """Menciones en una plataforma social."""

    @abstractmethod
    async def search_mentions(
        self,
        query: str,
        since: datetime | None = None,
        limit: int = 100,
    ) -> Observation[list[SocialPost]]:
        """Busca menciones."""

    @abstractmethod
    def stream_mentions(self, queries: Sequence[str]) -> AsyncIterator[Observation[SocialPost]]:
        """Menciones en tiempo real, si la plataforma lo permite."""

    @abstractmethod
    async def get_author_metadata(self, author_id: str) -> Observation[dict[str, object]]:
        """Metadatos del autor: seguidores, antiguedad, verificacion.

        La antiguedad de la cuenta es lo que separa atencion real de amplificacion
        artificial. Sin ella, `SocialAuthenticityScore` no se puede calcular.
        """


class NewsProvider(Provider):
    """Noticias y comunicados."""

    @abstractmethod
    async def get_recent(
        self, since: datetime | None = None, limit: int = 100
    ) -> Observation[list[NewsItem]]:
        """Noticias recientes."""

    @abstractmethod
    async def search(self, query: str, limit: int = 50) -> Observation[list[NewsItem]]:
        """Busca noticias."""
