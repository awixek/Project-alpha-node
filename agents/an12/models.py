from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from agents.an04.models import SEOResult
from agents.an09.models import VideoPackage
from agents.an10.models import ThumbnailPackage
from agents.an11.models import QualityDecision, QualityReport
from shared.constants import AgentID, Platform
from shared.schemas import BaseAlphaModel


class SchedulingMode(str, Enum):
    IMMEDIATE = "immediate"
    SCHEDULED = "scheduled"
    DELAYED = "delayed"
    STAGED = "staged"
    DRY_RUN = "dry_run"


class PublicationStatus(str, Enum):
    PENDING = "pending"
    SCHEDULED = "scheduled"
    PUBLISHED = "published"
    VERIFIED = "verified"
    FAILED = "failed"
    SKIPPED = "skipped"
    PARTIAL = "partial"


class VerificationStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    PENDING = "pending"


class PublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    mission_id: UUID
    video: VideoPackage
    thumbnail: ThumbnailPackage
    quality: QualityReport
    seo: SEOResult
    platforms: list[Platform] = Field(default_factory=list)
    scheduling_mode: SchedulingMode = SchedulingMode.IMMEDIATE
    scheduled_at: datetime | None = None
    timezone: str = "UTC"
    runtime_overrides: dict[str, Any] = Field(default_factory=dict)


class PlatformMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    description: str
    hashtags: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    category: str | None = None
    playlist: str | None = None
    locale: str | None = None
    visibility: str = "public"
    scheduled_at: datetime | None = None
    thumbnail_required: bool = True


class UploadPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform: Platform
    video_uri: str
    thumbnail_uri: str | None = None
    metadata: PlatformMetadata
    idempotency_key: str


class PublishAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_id: UUID = Field(default_factory=uuid4)
    platform: Platform
    attempt_number: int = Field(ge=1)
    status: PublicationStatus
    started_at: datetime
    completed_at: datetime | None = None
    provider: str | None = None
    platform_id: str | None = None
    url: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False


class VerificationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform: Platform
    status: VerificationStatus
    platform_id: str | None = None
    url: str | None = None
    upload_confirmed: bool = False
    processing_confirmed: bool = False
    metadata_integrity: bool = False
    thumbnail_integrity: bool = False
    verified_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    notes: list[str] = Field(default_factory=list)


class PublicationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform: Platform
    status: PublicationStatus
    platform_id: str | None = None
    url: str | None = None
    scheduled_at: datetime | None = None
    attempts: list[PublishAttempt] = Field(default_factory=list)
    verification: VerificationReport | None = None


class PublishAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audit_id: UUID = Field(default_factory=uuid4)
    started_at: datetime
    completed_at: datetime | None = None
    eligible: bool
    quality_decision: QualityDecision
    platforms_requested: list[Platform] = Field(default_factory=list)
    platforms_published: list[Platform] = Field(default_factory=list)
    platforms_failed: list[Platform] = Field(default_factory=list)
    dry_run: bool = False
    notes: list[str] = Field(default_factory=list)


class PublishMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution_time_ms: float = Field(ge=0)
    platforms_requested: int = Field(ge=0)
    platforms_succeeded: int = Field(ge=0)
    platforms_failed: int = Field(ge=0)
    retries: int = Field(ge=0)
    verified_publications: int = Field(ge=0)


class PublishPackage(BaseAlphaModel):
    mission_id: UUID
    agent_id: AgentID = AgentID.PUBLISHER
    status: PublicationStatus
    platform_records: list[PublicationRecord] = Field(default_factory=list)
    published_urls: dict[str, str] = Field(default_factory=dict)
    platform_metadata: dict[str, PlatformMetadata] = Field(default_factory=dict)
    verification_report: list[VerificationReport] = Field(default_factory=list)
    retry_history: list[PublishAttempt] = Field(default_factory=list)
    publishing_history: list[PublicationRecord] = Field(default_factory=list)
    audit_report: PublishAudit
    execution_metrics: PublishMetrics
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PublisherConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    enabled_platforms: list[Platform] = Field(default_factory=list)
    scheduling_mode: SchedulingMode = SchedulingMode.IMMEDIATE
    default_visibility: str = "public"
    retry_policy: dict[str, Any] = Field(default_factory=dict)
    timeout: float = Field(default=120.0, gt=0)
    localization: str = "en"
    publishing_order: list[Platform] = Field(default_factory=list)
    verification_timeout: float = Field(default=120.0, gt=0)
    dry_run: bool = False
    max_attempts: int = Field(default=3, ge=1, le=20)

    @classmethod
    def from_shared_config(cls) -> "PublisherConfig":
        from shared.config import get_config

        agent = get_config().agents.get(AgentID.PUBLISHER.value)
        values = dict(agent.settings) if agent else {}
        return cls(**values)
