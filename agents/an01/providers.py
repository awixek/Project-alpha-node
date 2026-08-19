"""Provider abstraction for AN-02, routed exclusively through Shared APIRouter."""
from __future__ import annotations

from abc import abstractmethod
from typing import Iterable

from shared.api_router import AIProvider, APIRouter
from shared.exceptions import AllProvidersFailedError, APIProviderError

from .models import EvidenceItem


class FactVerificationProvider(AIProvider[str, tuple[EvidenceItem, ...]]):
    """Provider adapter contract. Vendor-specific code belongs in adapters."""

    @abstractmethod
    def call(self, request: str) -> tuple[EvidenceItem, ...]:
        """Return normalized evidence for one claim."""
        raise NotImplementedError


class FactVerificationProviderRegistry:
    """Thread-safe provider routing boundary owned by the Shared APIRouter."""

    def __init__(
        self,
        *,
        router: APIRouter[str, tuple[EvidenceItem, ...]] | None = None,
    ) -> None:
        self._router = router or APIRouter()
        self._providers: dict[str, FactVerificationProvider] = {}

    @property
    def provider_names(self) -> tuple[str, ...]:
        return tuple(self._providers)

    def register(self, provider: FactVerificationProvider, *, priority: int = 10) -> None:
        if provider.name in self._providers:
            raise APIProviderError(
                f"Fact verification provider '{provider.name}' is already registered.",
                context={"provider": provider.name},
            )
        self._providers[provider.name] = provider
        self._router.register_provider(provider, priority=priority)

    def verify_all(self, claim: str) -> tuple[dict[str, tuple[EvidenceItem, ...]], dict[str, str]]:
        """Query every configured provider independently and degrade gracefully."""
        responses: dict[str, tuple[EvidenceItem, ...]] = {}
        failures: dict[str, str] = {}

        for provider_name in self.provider_names:
            try:
                excluded = set(self._providers) - {provider_name}
                response = self._router.call(claim, exclude=excluded)
                responses[provider_name] = response
            except AllProvidersFailedError as exc:
                failures[provider_name] = "provider verification failed"
            except Exception as exc:  # noqa: BLE001 - provider isolation boundary
                failures[provider_name] = "provider verification failed"

        return responses, failures

    @classmethod
    def from_providers(
        cls,
        providers: Iterable[tuple[FactVerificationProvider, int]],
        *,
        router: APIRouter[str, tuple[EvidenceItem, ...]] | None = None,
    ) -> "FactVerificationProviderRegistry":
        registry = cls(router=router)
        for provider, priority in providers:
            registry.register(provider, priority=priority)
        return registry
