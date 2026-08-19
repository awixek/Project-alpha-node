"""Provider abstraction for optional AN-04 SEO enrichment."""
from __future__ import annotations

from abc import abstractmethod
import threading
from typing import Iterable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from shared.api_router import AIProvider, APIRouter
from shared.exceptions import APIProviderError


class SEOGenerationRequest(BaseModel):
    """Vendor-neutral request for optional SEO text enrichment."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    mission_id: UUID
    script_title: str = Field(..., min_length=1)
    corpus: str = Field(..., min_length=1)
    optimized_title: str = Field(..., min_length=1)
    language: str = Field(default="en", min_length=1, max_length=32)


class SEOGenerationResponse(BaseModel):
    """Normalized provider response."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    provider: str = Field(..., min_length=1)
    description: str = ""
    alternative_titles: list[str] = Field(default_factory=list)


class SEOGenerationProvider(AIProvider[SEOGenerationRequest, SEOGenerationResponse]):
    """Vendor-neutral provider contract."""

    @abstractmethod
    def call(self, request: SEOGenerationRequest) -> SEOGenerationResponse:
        raise NotImplementedError


class SEOGenerationProviderRegistry:
    """Thread-safe routing boundary delegated to the Shared API Router."""

    def __init__(self, *, router: APIRouter[SEOGenerationRequest, SEOGenerationResponse] | None = None) -> None:
        self._router = router or APIRouter()
        self._providers: dict[str, SEOGenerationProvider] = {}
        self._lock = threading.RLock()

    @property
    def provider_names(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._providers)

    def register(self, provider: SEOGenerationProvider, *, priority: int = 10) -> None:
        with self._lock:
            if provider.name in self._providers:
                raise APIProviderError(
                    f"SEO provider '{provider.name}' is already registered.",
                    context={"provider": provider.name},
                )
            self._providers[provider.name] = provider
            self._router.register_provider(provider, priority=priority)

    def generate(self, request: SEOGenerationRequest) -> SEOGenerationResponse:
        return self._router.call(request)

    def request_type(self, **kwargs) -> SEOGenerationRequest:
        return SEOGenerationRequest(**kwargs)

    @classmethod
    def from_providers(
        cls,
        providers: Iterable[tuple[SEOGenerationProvider, int]],
        *,
        router: APIRouter[SEOGenerationRequest, SEOGenerationResponse] | None = None,
    ) -> "SEOGenerationProviderRegistry":
        registry = cls(router=router)
        for provider, priority in providers:
            registry.register(provider, priority=priority)
        return registry
