from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from shared.api_router import AIProvider, APIRouter
from shared.config import get_config
from shared.retry import RetryPolicy


class ThumbnailProviderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mission_id: UUID
    prompt: str = Field(min_length=1)
    aspect_ratio: str = Field(min_length=3)
    metadata: dict[str, str] = Field(default_factory=dict)


class ThumbnailProviderResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(min_length=1)
    preview_uri: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


class ThumbnailProvider(AIProvider[ThumbnailProviderRequest, ThumbnailProviderResponse], ABC):
    """Optional provider boundary for future thumbnail preview/generation adapters."""

    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def call(self, request: ThumbnailProviderRequest) -> ThumbnailProviderResponse:
        raise NotImplementedError


class ThumbnailProviderRouter:
    """Thin adapter over the frozen shared APIRouter; business logic is provider-free."""

    def __init__(self, *, timeout_seconds: float = 120.0, max_retries: int = 2,
                 router: APIRouter[ThumbnailProviderRequest, ThumbnailProviderResponse] | None = None) -> None:
        if router is not None:
            self._router = router
            return
        retry = get_config().retry
        policy = RetryPolicy(max_attempts=max(1, max_retries + 1), delay_seconds=retry.delay_seconds,
                             backoff_multiplier=retry.backoff_multiplier, timeout_seconds=timeout_seconds,
                             retry_exceptions=(Exception,))
        self._router = APIRouter(retry_policy=policy)

    def register(self, provider: ThumbnailProvider, *, priority: int = 10) -> None:
        self._router.register_provider(provider, priority=priority)

    def preview(self, request: ThumbnailProviderRequest) -> ThumbnailProviderResponse:
        return self._router.call(request)
