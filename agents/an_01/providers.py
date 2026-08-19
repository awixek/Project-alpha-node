"""Provider abstraction and shared API Router integration for AN-01."""
from __future__ import annotations

from abc import abstractmethod
from typing import Iterable

from shared.api_router import AIProvider, APIRouter
from shared.exceptions import AllProvidersFailedError, APIProviderError

from .models import ProviderSearchRequest, ProviderSearchResponse


class ResearchProvider(AIProvider[ProviderSearchRequest, ProviderSearchResponse]):
    """Abstract provider adapter; concrete vendor code belongs outside AN-01 core."""

    @abstractmethod
    def call(self, request: ProviderSearchRequest) -> ProviderSearchResponse:
        raise NotImplementedError


class ResearchProviderRegistry:
    """Registers research providers and routes calls through Shared APIRouter."""

    def __init__(self, *, router: APIRouter[ProviderSearchRequest, ProviderSearchResponse] | None = None) -> None:
        self._router = router or APIRouter()
        self._providers: dict[str, ResearchProvider] = {}

    @property
    def router(self) -> APIRouter[ProviderSearchRequest, ProviderSearchResponse]:
        return self._router

    @property
    def provider_names(self) -> tuple[str, ...]:
        return tuple(self._providers)

    def register(self, provider: ResearchProvider, *, priority: int = 10) -> None:
        if provider.name in self._providers:
            raise APIProviderError(f"Research provider '{provider.name}' is already registered.")
        self._providers[provider.name] = provider
        self._router.register_provider(provider, priority=priority)

    def search_provider(self, provider_name: str, request: ProviderSearchRequest) -> ProviderSearchResponse:
        """Invoke exactly one registered provider while retaining shared retry semantics."""
        if provider_name not in self._providers:
            raise APIProviderError(
                f"Research provider '{provider_name}' is not registered.",
                context={"provider": provider_name},
            )
        excluded = set(self._providers) - {provider_name}
        response = self._router.call(request, exclude=excluded)
        if response.provider != provider_name:
            raise APIProviderError(
                "Shared API Router returned an unexpected research provider.",
                context={"requested_provider": provider_name, "returned_provider": response.provider},
            )
        return response

    def search_all(self, request: ProviderSearchRequest) -> tuple[dict[str, ProviderSearchResponse], dict[str, str]]:
        responses: dict[str, ProviderSearchResponse] = {}
        failures: dict[str, str] = {}
        for provider_name in self.provider_names:
            try:
                responses[provider_name] = self.search_provider(provider_name, request)
            except AllProvidersFailedError as exc:
                failures[provider_name] = str(exc)
            except Exception as exc:  # provider boundary; coordinator decides whether to degrade
                failures[provider_name] = str(exc)
        return responses, failures

    @classmethod
    def from_providers(
        cls,
        providers: Iterable[tuple[ResearchProvider, int]],
        *,
        router: APIRouter[ProviderSearchRequest, ProviderSearchResponse] | None = None,
    ) -> "ResearchProviderRegistry":
        registry = cls(router=router)
        for provider, priority in providers:
            registry.register(provider, priority=priority)
        return registry
