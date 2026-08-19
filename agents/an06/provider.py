from __future__ import annotations
from abc import ABC, abstractmethod
from shared.api_router import AIProvider, APIRouter
from shared.config import get_config
from shared.retry import RetryPolicy
from .models import GenerationRequest, ProviderAssetResponse, VisionCreatorConfig

class VisionGenerationProvider(AIProvider[GenerationRequest, ProviderAssetResponse], ABC):
    @property
    @abstractmethod
    def name(self) -> str: raise NotImplementedError
    @abstractmethod
    def call(self, request: GenerationRequest) -> ProviderAssetResponse: raise NotImplementedError

class VisionProviderRouter:
    def __init__(self, *, config: VisionCreatorConfig | None = None, router: APIRouter[GenerationRequest, ProviderAssetResponse] | None = None) -> None:
        self._config = config or VisionCreatorConfig()
        self._router = router or self._build_router(self._config)
    @staticmethod
    def _build_router(config: VisionCreatorConfig) -> APIRouter[GenerationRequest, ProviderAssetResponse]:
        base = get_config().retry
        policy = RetryPolicy(max_attempts=max(1, config.max_retries + 1), delay_seconds=base.delay_seconds, backoff_multiplier=base.backoff_multiplier, timeout_seconds=config.timeout, retry_exceptions=(Exception,))
        return APIRouter(retry_policy=policy)
    def register(self, provider: VisionGenerationProvider, *, priority: int | None = None) -> None:
        if priority is None:
            if provider.name == self._config.preferred_provider:
                priority = 0
            elif provider.name == self._config.fallback_provider:
                priority = 1
            else:
                priority = 10
        self._router.register_provider(provider, priority=priority)
    def generate(self, request: GenerationRequest, *, exclude: set[str] | None = None) -> ProviderAssetResponse: return self._router.call(request, exclude=exclude)
    def health(self, name: str): return self._router.get_health(name)
