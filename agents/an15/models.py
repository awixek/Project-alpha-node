"""Typed contracts for AN-15 Omni Republisher."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from agents.an03.models import ScriptDocument
from agents.an09.models import VideoPackage
from agents.an10.models import ThumbnailPackage
from agents.an12.models import PublishPackage
from agents.an13.models import AnalyticsReport
from agents.an14.models import EvolutionReport
from shared.constants import AgentID, Platform
from shared.schemas import BaseAlphaModel


class TransformationType(str, Enum):
    SHORT_VIDEO = "short_video"
    REEL = "reel"
    BLOG_ARTICLE = "blog_article"
    SOCIAL_THREAD = "social_thread"
    LINKEDIN_ARTICLE = "linkedin_article"
    TELEGRAM_POST = "telegram_post"
    COMMUNITY_UPDATE = "community_update"
    THUMBNAIL_VARIANT = "thumbnail_variant"
    CAPTION_VARIANT = "caption_variant"


class DistributionStatus(str, Enum):
    READY = "ready"
    WARNING = "warning"
    FAILED = "failed"
    SKIPPED = "skipped"


class PlatformProfile(BaseModel):
    """Configurable destination constraints, independent of any platform API."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    platform: Platform
    max_duration_seconds: int | None = Field(default=None, ge=1)
    max_title_chars: int | None = Field(default=None, ge=1)
    max_text_chars: int | None = Field(default=None, ge=1)
    max_hashtags: int = Field(default=5, ge=0, le=100)
    aspect_ratio: str = Field(default="16:9", min_length=3, max_length=16)
    cta_style: str = Field(default="soft", min_length=1, max_length=64)
    title_strategy: str = Field(default="clear", min_length=1, max_length=64)
    caption_strategy: str = Field(default="concise", min_length=1, max_length=64)
    thumbnail_required: bool = True
    supports_threads: bool = False


class RepurposeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    enabled_platforms: list[Platform] = Field(default_factory=list)
    transformation_rules: dict[str, Any] = Field(default_factory=dict)
    default_profiles: dict[str, dict[str, Any]] = Field(default_factory=dict)
    metadata_strategy: str = "canonical_first"
    validation_level: str = "strict"
    distribution_priority: list[Platform] = Field(default_factory=list)

    @classmethod
    def from_shared_config(cls) -> "RepurposeConfig":
        from shared.config import get_config

        agent = get_config().agents.get(AgentID.OMNI_REPUBLISHER.value)
        values = dict(agent.settings) if agent else {}
        return cls(**values)


class RepurposeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    mission_id: UUID
    publish: PublishPackage
    analytics: AnalyticsReport
    evolution: EvolutionReport
    script: ScriptDocument | None = None
    video: VideoPackage | None = None
    thumbnail: ThumbnailPackage | None = None
    runtime_overrides: dict[str, Any] = Field(default_factory=dict)


class TransformedAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: UUID = Field(default_factory=uuid4)
    source_reference: str = Field(min_length=1)
    platform: Platform
    transformation: TransformationType
    title: str = Field(min_length=1)
    body: str = ""
    asset_uri: str | None = None
    duration_seconds: int | None = Field(default=None, ge=1)
    aspect_ratio: str | None = None
    source_asset_ids: list[str] = Field(default_factory=list)
    reusable: bool = True


class PlatformMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    description: str = ""
    hashtags: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    cta: str | None = None
    locale: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


class ValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: str = Field(min_length=1)
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    field: str | None = None
    recommendation: str | None = None


class PlatformDistribution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform: Platform
    status: DistributionStatus
    profile: PlatformProfile
    assets: list[TransformedAsset] = Field(default_factory=list)
    metadata: PlatformMetadata
    validation_issues: list[ValidationIssue] = Field(default_factory=list)
    optimization_summary: list[str] = Field(default_factory=list)
    priority: int = Field(default=10, ge=1, le=100)


class DistributionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ordered_platforms: list[Platform] = Field(default_factory=list)
    rationale: list[str] = Field(default_factory=list)
    staged: bool = False


class ExecutionMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution_time_ms: float = Field(ge=0)
    platforms_requested: int = Field(ge=0)
    platforms_processed: int = Field(ge=0)
    platforms_failed: int = Field(ge=0)
    assets_generated: int = Field(ge=0)
    validation_errors: int = Field(ge=0)


class RepublisherPackage(BaseAlphaModel):
    mission_id: UUID
    agent_id: AgentID = AgentID.OMNI_REPUBLISHER
    distributions: list[PlatformDistribution] = Field(default_factory=list)
    transformed_assets: list[TransformedAsset] = Field(default_factory=list)
    platform_metadata: dict[str, PlatformMetadata] = Field(default_factory=dict)
    validation_results: dict[str, list[ValidationIssue]] = Field(default_factory=dict)
    optimization_summaries: dict[str, list[str]] = Field(default_factory=dict)
    distribution_plan: DistributionPlan
    execution_metrics: ExecutionMetrics
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


__all__ = [
    "DistributionPlan", "DistributionStatus", "ExecutionMetrics", "PlatformDistribution",
    "PlatformMetadata", "PlatformProfile", "RepurposeConfig", "RepurposeRequest",
    "TransformationType", "TransformedAsset", "ValidationIssue", "RepublisherPackage",
]
