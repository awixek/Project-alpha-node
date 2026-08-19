from __future__ import annotations

from abc import ABC, abstractmethod

from shared.api_router import AIProvider, APIRouter
from shared.config import get_config
from shared.retry import RetryPolicy
from .models import VideoProviderRequest, VideoProviderResponse, RenderSettings


class VideoRenderProvider(AIProvider[VideoProviderRequest, VideoProviderResponse], ABC):
    """Provider contract for any future rendering backend."""

    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def call(self, request: VideoProviderRequest) -> VideoProviderResponse:
        raise NotImplementedError


class VideoRenderRouter:
    def __init__(self, *, settings: RenderSettings | None = None,
                 router: APIRouter[VideoProviderRequest, VideoProviderResponse] | None = None) -> None:
        self.settings = settings or RenderSettings()
        if router is not None:
            self._router = router
            return
        base = get_config().retry
        policy = RetryPolicy(
            max_attempts=max(1, self.settings.max_retries + 1),
            delay_seconds=base.delay_seconds,
            backoff_multiplier=base.backoff_multiplier,
            timeout_seconds=self.settings.timeout,
            retry_exceptions=(Exception,),
        )
        self._router = APIRouter(retry_policy=policy)

    def register(self, provider: VideoRenderProvider, *, priority: int | None = None) -> None:
        if priority is None:
            if provider.name == self.settings.render_quality:
                priority = 0
            else:
                priority = 10
        self._router.register_provider(provider, priority=priority)

    def render(self, request: VideoProviderRequest, *, exclude: set[str] | None = None) -> VideoProviderResponse:
        return self._router.call(request, exclude=exclude)
