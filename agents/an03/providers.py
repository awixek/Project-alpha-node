"""Provider abstraction for AN-03, backed by the frozen Shared API Router."""
from __future__ import annotations

from abc import abstractmethod
from typing import Iterable

from shared.api_router import AIProvider, APIRouter
from shared.exceptions import APIProviderError

from .models import ScriptGenerationRequest, ScriptGenerationResponse


class ScriptGenerationProvider(AIProvider[ScriptGenerationRequest, ScriptGenerationResponse]):
    """Vendor-neutral contract for script generation adapters."""

    @abstractmethod
    def call(self, request: ScriptGenerationRequest) -> ScriptGenerationResponse:
        raise NotImplementedError


class ScriptGenerationProviderRegistry:
    """Thread-safe registration/routing boundary delegated to APIRouter."""

    def __init__(self, *, router: APIRouter[ScriptGenerationRequest, ScriptGenerationResponse] | None = None) -> None:
        self._router = router or APIRouter()
        self._providers: dict[str, ScriptGenerationProvider] = {}

    @property
    def provider_names(self) -> tuple[str, ...]:
        return tuple(self._providers)

    def register(self, provider: ScriptGenerationProvider, *, priority: int = 10) -> None:
        if provider.name in self._providers:
            raise APIProviderError(
                f"Script generation provider '{provider.name}' is already registered.",
                context={"provider": provider.name},
            )
        self._providers[provider.name] = provider
        self._router.register_provider(provider, priority=priority)

    def generate(self, request: ScriptGenerationRequest) -> ScriptGenerationResponse:
        return self._router.call(request)

    @classmethod
    def from_providers(
        cls,
        providers: Iterable[tuple[ScriptGenerationProvider, int]],
        *,
        router: APIRouter[ScriptGenerationRequest, ScriptGenerationResponse] | None = None,
    ) -> "ScriptGenerationProviderRegistry":
        registry = cls(router=router)
        for provider, priority in providers:
            registry.register(provider, priority=priority)
        return registry
