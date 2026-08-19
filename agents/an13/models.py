"""AN-13 Analytics Brain contracts."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from agents.an04.models import SEOResult
from agents.an09.models import VideoPackage
from agents.an10.models import ThumbnailPackage
from agents.an11.models import QualityReport
from agents.an12.models import PublishPackage
from shared.constants import AgentID, Platform
from shared.schemas import BaseAlphaModel


class MetricSource(str, Enum):
    PROVIDER = "provider"
    PUBLISHING = "publishing"
    DERIVED = "derived"


class NormalizedMetric(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    platform: Platform
    captured_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    views: int = Field(default=0, ge=0)
    impressions: int = Field(default=0, ge=0)
    click_through_rate: float | None = Field(default=None, ge=0, le=1)
    watch_time_seconds: float = Field(default=0, ge=0)
    average_view_duration_seconds: float = Field(default=0, ge=0)
    audience_retention: float | None = Field(default=None, ge=0, le=1)
    subscriber_conversion: float | None = Field(default=None, ge=0, le=1)
    likes: int = Field(default=0, ge=0)
    comments: int = Field(default=0, ge=0)
    shares: int = Field(default=0, ge=0)
    saves: int = Field(default=0, ge=0)
    returning_viewers: int = Field(default=0, ge=0)
    new_viewers: int = Field(default=0, ge=0)
    external_traffic: int = Field(default=0, ge=0)
    search_traffic: int = Field(default=0, ge=0)
    recommendation_traffic: int = Field(default=0, ge=0)
    keyword_rankings: dict[str, float] = Field(default_factory=dict)
    drop_off_points: list[float] = Field(default_factory=list)
    source: MetricSource = MetricSource.PROVIDER
    provider: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


class PerformanceScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: float = Field(ge=0, le=100)
    calculation: str = Field(min_length=1)
    explanation: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    contributing_factors: dict[str, float] = Field(default_factory=dict)


class TrendReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    direction: str = Field(min_length=1)
    strength: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    evidence: list[str] = Field(default_factory=list)
    explanation: str = Field(min_length=1)


class AudienceInsight(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1)
    value: float
    unit: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    explanation: str = Field(min_length=1)


class RecommendationTarget(str, Enum):
    AN01 = "AN-01"
    AN03 = "AN-03"
    AN04 = "AN-04"
    AN05 = "AN-05"
    AN10 = "AN-10"
    AN12 = "AN-12"


class AnalyticsRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recommendation_id: UUID = Field(default_factory=uuid4)
    target_agent: RecommendationTarget
    priority: int = Field(ge=1, le=10)
    expected_impact: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    action: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    evidence: list[str] = Field(default_factory=list)


class TrendAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reports: list[TrendReport] = Field(default_factory=list)
    window_size: int = Field(ge=1)
    sample_size: int = Field(ge=0)


class AnalyticsReport(BaseAlphaModel):
    mission_id: UUID
    agent_id: AgentID = AgentID.ANALYTICS_BRAIN
    normalized_metrics: list[NormalizedMetric] = Field(default_factory=list)
    trend_analysis: TrendAnalysis
    performance_scores: dict[str, PerformanceScore] = Field(default_factory=dict)
    audience_insights: list[AudienceInsight] = Field(default_factory=list)
    seo_insights: list[str] = Field(default_factory=list)
    thumbnail_insights: list[str] = Field(default_factory=list)
    publishing_insights: list[str] = Field(default_factory=list)
    recommendation_report: list[AnalyticsRecommendation] = Field(default_factory=list)
    confidence_metrics: dict[str, float] = Field(default_factory=dict)
    execution_statistics: dict[str, float | int | str] = Field(default_factory=dict)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AnalyticsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    mission_id: UUID
    publish: PublishPackage
    quality: QualityReport
    seo: SEOResult
    thumbnail: ThumbnailPackage
    video: VideoPackage
    runtime_overrides: dict[str, Any] = Field(default_factory=dict)


class AnalyticsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    scoring_weights: dict[str, float] = Field(default_factory=lambda: {
        "performance": 0.22,
        "engagement": 0.16,
        "seo": 0.12,
        "retention": 0.16,
        "thumbnail": 0.12,
        "publishing": 0.08,
        "audience": 0.09,
        "confidence": 0.05,
    })
    trend_detection_window: int = Field(default=7, ge=1, le=365)
    minimum_sample_size: int = Field(default=1, ge=1, le=100000)
    recommendation_threshold: float = Field(default=0.55, ge=0, le=1)
    anomaly_threshold: float = Field(default=0.30, ge=0, le=1)
    confidence_threshold: float = Field(default=0.60, ge=0, le=1)

    @classmethod
    def from_shared_config(cls) -> "AnalyticsConfig":
        from shared.config import get_config

        agent = get_config().agents.get(AgentID.ANALYTICS_BRAIN.value)
        values = dict(agent.settings) if agent else {}
        defaults = cls()
        weights = dict(defaults.scoring_weights)
        weights.update(dict(values.get("scoring_weights", {})))
        values["scoring_weights"] = weights
        return cls(**values)

    def effective_weights(self) -> dict[str, float]:
        positive = {k: max(0.0, float(v)) for k, v in self.scoring_weights.items()}
        total = sum(positive.values())
        if total <= 0:
            raise ValueError("At least one analytics scoring weight must be positive.")
        return {k: v / total for k, v in positive.items()}
