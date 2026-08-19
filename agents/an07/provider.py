from __future__ import annotations

from abc import ABC, abstractmethod

from shared.api_router import AIProvider, APIRouter
from shared.config import get_config
from shared.retry import RetryPolicy

from .models import VoiceCoreConfig, VoiceProviderRequest, VoiceProviderResponse


class VoiceProvider(AIProvider[VoiceProviderRequest, VoiceProviderResponse], ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def call(self, request: VoiceProviderRequest) -> VoiceProviderResponse:
        raise NotImplementedError


class VoiceProviderRouter:
    """Provider-neutral adapter over the frozen shared API router."""

    def __init__(
        self,
        *,
        config: VoiceCoreConfig | None = None,
        router: APIRouter[VoiceProviderRequest, VoiceProviderResponse] | None = None,
    ) -> None:
        self._config = config or VoiceCoreConfig()
        self._router = router or self._build_router(self._config)

    @staticmethod
    def _build_router(config: VoiceCoreConfig) -> APIRouter[VoiceProviderRequest, VoiceProviderResponse]:
        retry = get_config().retry
        policy = RetryPolicy(
            max_attempts=max(1, config.max_retries + 1),
            delay_seconds=retry.delay_seconds,
            backoff_multiplier=retry.backoff_multiplier,
            timeout_seconds=config.timeout,
            retry_exceptions=(Exception,),
        )
        return APIRouter(retry_policy=policy)

    def register(self, provider: VoiceProvider, *, priority: int | None = None) -> None:
        if priority is None:
            if provider.name == self._config.preferred_provider:
                priority = 0
            elif provider.name == self._config.fallback_provider:
                priority = 1
            else:
                priority = 10
        self._router.register_provider(provider, priority=priority)

    def synthesize(self, request: VoiceProviderRequest, *, exclude: set[str] | None = None) -> VoiceProviderResponse:
        return self._router.call(request, exclude=exclude)
