from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from shared.api_router import AIProvider, APIRouter
from shared.config import get_config
from shared.retry import RetryPolicy
from shared.constants import Platform


class PublishAdapterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mission_id: UUID
    platform: Platform
    video_uri: str = Field(min_length=1)
    thumbnail_uri: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=1)


class PublishAdapterResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1)
    platform_id: str | None = None
    url: str | None = None
    upload_success: bool
    processing_complete: bool = True
    metadata_integrity: bool = True
    thumbnail_integrity: bool = True
    message: str = ""


class VerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mission_id: UUID
    platform: Platform
    platform_id: str
    expected_metadata: dict[str, object] = Field(default_factory=dict)
    expected_thumbnail_uri: str | None = None


class VerificationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1)
    exists: bool
    processing_complete: bool
    metadata_integrity: bool
    thumbnail_integrity: bool
    url: str | None = None
    message: str = ""


RequestT = TypeVar("RequestT", bound=BaseModel)
ResponseT = TypeVar("ResponseT", bound=BaseModel)


class PublishingAdapter(AIProvider[PublishAdapterRequest, PublishAdapterResponse], ABC):
    """Platform adapter boundary; no platform API is known by Publisher."""

    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def platform(self) -> Platform:
        raise NotImplementedError

    @abstractmethod
    def call(self, request: PublishAdapterRequest) -> PublishAdapterResponse:
        raise NotImplementedError


class VerificationAdapter(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def platform(self) -> Platform:
        raise NotImplementedError

    @abstractmethod
    def verify(self, request: VerificationRequest) -> VerificationResponse:
        raise NotImplementedError


class AdapterRouter:
    """Thin, provider-independent facade over the frozen shared API Router."""

    def __init__(self, *, timeout: float = 120.0, max_attempts: int = 3,
                 router: APIRouter[PublishAdapterRequest, PublishAdapterResponse] | None = None) -> None:
        if router is not None:
            self._router = router
            return
        retry = get_config().retry
        policy = RetryPolicy(
            max_attempts=max(1, max_attempts),
            delay_seconds=retry.delay_seconds,
            backoff_multiplier=retry.backoff_multiplier,
            timeout_seconds=timeout,
            retry_exceptions=(Exception,),
        )
        self._router = APIRouter(retry_policy=policy)

    def register(self, adapter: PublishingAdapter, *, priority: int = 10) -> None:
        self._router.register_provider(adapter, priority=priority)

    def publish(self, request: PublishAdapterRequest) -> PublishAdapterResponse:
        return self._router.call(request)
